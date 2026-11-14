"""The API behind the verification UI, on the real dev drawings (skipped without the dataset).

No test here calls a vision model: scanned plans are answered from logged runs.
"""

import json
import re
import struct

import pytest

from blueprints import config, mesh
from blueprints.server import _fit_affine

PREVIEW = config.DATA_DIR / "junction-dataset-preview"
needs_data = pytest.mark.skipif(
    not (PREVIEW / "questions.jsonl").is_file() or not config.SPLIT_FILE.is_file(),
    reason="dataset or split not present",
)
SWISS = "data/swiss_dwellings/site1000_bldg2082_floor9385"
DOORS = "How many doors (interior doors and entrance doors together) are drawn on this floor plan?"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from blueprints.server import create_app

    return TestClient(create_app(PREVIEW))


def ask(client, drawing, question):
    response = client.post("/api/ask", json={"drawing": drawing, "question": question})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- pure helpers


def test_mesh_round_trip():
    from array import array

    positions, indices = array("f"), array("I")
    mesh._prism(positions, indices, [(0, 0), (2, 0), (2, 1), (0, 1)], 0.0, 3.0, [(0, 1, 2), (0, 2, 3)])
    elements = [{"id": "box", "cls": "wall", "v": [0, len(positions) // 3], "i": [0, len(indices)]}]
    offset, bbox = mesh._centre(positions, elements)
    data = mesh._pack({"elements": elements, "vertices": len(positions) // 3, "triangles": len(indices) // 3},
                      positions, indices)
    header, pos, idx = mesh.unpack(data)
    assert data[:4] == b"BPM1" and (8 + struct.unpack_from("<I", data, 4)[0]) % 4 == 0
    assert header["vertices"] == 8 and header["triangles"] == 12  # 8 side + 2 top + 2 bottom triangles
    assert len(pos) == 24 and len(idx) == 36 and max(idx) == 7
    assert offset == [1.0, 0.5, 0.0] and bbox == [-1.0, -0.5, 0.0, 1.0, 0.5, 3.0]
    assert elements[0]["c"] == [0.0, 0.0, 1.5]
    outline = mesh.footprint(elements[0], pos, idx)
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((-1, 1, -0.5, 0.5), abs=0.02)


def test_fit_affine_ignores_a_bad_anchor():
    # plan = model scaled by 20, y flipped, moved; one anchor is a label printed elsewhere
    points = [(0, 0), (10, 0), (10, 6), (0, 6), (5, 3), (2, 5), (8, 1)]
    pairs = [((x, y), (100 + 20 * x, 400 - 20 * y)) for x, y in points]
    pairs.append(((4, 4), (900.0, 900.0)))
    fit = _fit_affine(pairs)
    assert fit is not None and fit["anchors"] == 7 and fit["residual_pt"] < 0.01
    (a, d), (b, e), (c, f) = fit["coef"]
    assert (a, b, c, d, e, f) == pytest.approx((20, 0, 100, 0, -20, 400), abs=1e-6)
    assert _fit_affine(pairs[:3]) is None  # too few anchors to trust


# ---------------------------------------------------------------- the API on dev drawings


@needs_data
def test_only_dev_drawings_are_served(client):
    from blueprints.data import Dataset
    from blueprints.split import Split

    ds, split = Dataset(PREVIEW), Split.load()
    held_out = next(q for q in ds.questions if split.of(q) in ("calibration", "test"))
    assert client.get("/api/drawing", params={"drawing": held_out.drawing}).status_code == 404
    assert client.post("/api/ask", json={"drawing": held_out.drawing, "question": "x"}).status_code == 404
    assert client.get("/api/image", params={"drawing": held_out.drawing}).status_code == 404
    for source in client.get("/api/sources").json():
        if source["archive"]:
            continue
        page = client.get("/api/drawings", params={"source": source["source"], "limit": 200}).json()
        for item in page["items"]:
            files = ds.formats(item["id"])
            assert split.groups[ds.group_of(files[0])] == "dev"


@needs_data
def test_plan_view_and_evidence_share_one_pixel_space(client):
    detail = client.get("/api/drawing", params={"drawing": SWISS}).json()
    view = detail["view"]
    assert view["kind"] == "plan" and view["aligned"] and detail["rooms"]
    assert client.get(view["url"]).headers["content-type"] == "image/png"
    answer = ask(client, SWISS, DOORS)
    assert answer["state"] == "verified" and answer["cost_eur"] == 0
    boxes = [e for e in answer["evidence"] if e["kind"] == "box"]
    assert len(boxes) == answer["answer"] == answer["reference"]["answer"]
    assert [e["n"] for e in boxes] == list(range(1, len(boxes) + 1))
    for e in boxes:
        x0, y0, x1, y1 = e["bbox"]
        assert 0 <= x0 < x1 <= view["width"] and 0 <= y0 < y1 <= view["height"]
        assert e["ref"].startswith("door-")
    why = answer["verified_because"]
    assert why["error_upper_bound"] < 0.01 and why["calibration_answers"] > 1000


@needs_data
def test_plan_mesh_names_the_elements_the_evidence_refers_to(client):
    data = client.get("/api/mesh", params={"drawing": SWISS}).content
    header, positions, indices = mesh.unpack(data)
    assert header["kind"] == "plan" and len(positions) == 3 * header["vertices"]
    ids = {e["id"] for e in header["elements"]}
    answer = ask(client, SWISS, DOORS)
    assert {e["ref"] for e in answer["evidence"] if e["kind"] == "box"} <= ids
    assert max(indices) < header["vertices"]


@needs_data
def test_dataset_answer_never_reaches_the_engine(client):
    """A question the dataset does not contain gets no reference; the answer is the same either way."""
    known = ask(client, SWISS, DOORS)
    reworded = ask(client, SWISS, "How many doors are drawn on this floor plan?")
    assert known["reference"] is not None and reworded["reference"] is None
    assert reworded["answer"] == known["answer"]


@needs_data
def test_demo_tour_returns_the_states_it_promises(client):
    source = (config.REPO_ROOT / "web" / "src" / "tour.ts").read_text(encoding="utf-8")
    stops = re.findall(r'state: "(\w+)",\s+drawing: "([^"]+)",\s+question: "([^"]+)"', source)
    assert len(stops) >= 6
    for state, drawing, question in stops:
        answer = ask(client, drawing, question)
        assert answer["state"] == state, (drawing, question, answer["state"], answer["reason"])
        if state != "declined":
            assert any(e["kind"] != "formula" for e in answer["evidence"]), (drawing, question)
        if "cubicasa" in drawing:  # scans: read locally, or shown from the run log. The demo costs nothing.
            if answer["method"].startswith("vlm"):
                assert answer["replayed_from"]
            else:
                assert answer["cost_eur"] == 0 and any(e["kind"] == "box" for e in answer["evidence"])


@needs_data
def test_model_elements_land_on_the_plan_rendered_from_the_model(client):
    drawing = "data/ifc_models/bsc-esplanades_1807-ep-ar-v18_plan-04-4-korrus"
    detail = client.get("/api/drawing", params={"drawing": drawing}).json()
    answer = ask(client, drawing, "How many doors are shown on the floor plan of '4.korrus'?")
    doors = [e for e in answer["evidence"] if e["kind"] == "element"]
    assert len(doors) == answer["answer"] == 42
    placed = [e for e in doors if "x" in e]
    assert len(placed) == 42
    for e in placed:
        assert 0 <= e["x"] <= detail["view"]["width"] and 0 <= e["y"] <= detail["view"]["height"]
    room = ask(client, drawing, "What is the net floor area of the room 'KORIDOR (402)' on '4.korrus', in m²?")
    outline = [e for e in room["evidence"] if e["kind"] == "element"][0]["points"]
    # The outline drawn on the plan encloses the area the model reports (plan pixels -> m² through the fit).
    fit = client.app.state.service.plan_fit(drawing)
    (a, d), (b, e_), _ = fit["coef"]
    px_per_m2 = abs(a * e_ - b * d) * (detail["view"]["width"] / 1747.559) ** 2
    shoelace = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]))) / 2
    assert shoelace / px_per_m2 == pytest.approx(room["answer"], rel=0.03)


@needs_data
def test_stats_come_from_the_run_log(client):
    stats = client.get("/api/stats").json()
    assert stats["runs"] and all(run["rows"] for run in stats["runs"])
    with open(config.RESULTS_DIR / "leaderboard.csv", encoding="utf-8") as f:
        logged = {line.split(",", 1)[0] for line in f.readlines()[1:]}
    assert {run["run_id"] for run in stats["runs"]} <= logged
    assert json.dumps(stats)  # serialisable as it is
