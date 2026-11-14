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
    import os

    from fastapi.testclient import TestClient

    from blueprints.server import create_app

    os.environ["BLUEPRINTS_UI_BUDGET_EUR"] = "0"  # no paid model call from a test, whatever keys are in .env
    os.environ["BLUEPRINTS_OFFLINE"] = "1"  # and no partner service either
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
    # Seven of this floor's 36 door outlines lie exactly on another. Each door is counted once: 29.
    # The dataset's answer counts outlines (36), so the answer is one to look at, not a Verified one.
    assert answer["state"] == "check" and answer["cost_eur"] == 0
    assert answer["coinciding"] == {"outlines": 36, "distinct": 29, "extra": 7, "counted": "distinct"}
    assert answer["reference"]["answer"] == 36 and answer["reference"]["status"] == "wrong"
    tubs = ask(client, SWISS, "How many bathtubs are drawn on this floor plan?")
    assert tubs["state"] == "verified" and tubs["coinciding"] is None  # nothing coincides: Verified as before
    assert tubs["answer"] == tubs["reference"]["answer"]
    boxes = [e for e in answer["evidence"] if e["kind"] == "box"]
    assert len(boxes) == answer["answer"] == 29 and sum(e.get("times", 1) for e in boxes) == 36
    assert not any("same_as" in e for e in boxes)  # one mark per door
    assert [e["n"] for e in boxes] == list(range(1, len(boxes) + 1))
    for e in boxes:
        x0, y0, x1, y1 = e["bbox"]
        assert 0 <= x0 < x1 <= view["width"] and 0 <= y0 < y1 <= view["height"]
        assert e["ref"].startswith("door-")
    assert answer["verified_because"] is None
    why = tubs["verified_because"]  # the proof behind an answer that is Verified
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
    # A stop on a drawing this dataset does not hold (the preview is a part of the full set) is left out by the app.
    known = set(client.post("/api/known", json={"drawings": [drawing for _, drawing, _ in stops]}).json()["known"])
    stops = [stop for stop in stops if stop[1] in known]
    assert len(stops) >= 12
    for state, drawing, question in stops:
        answer = ask(client, drawing, question)
        assert answer["state"] == state, (drawing, question, answer["state"], answer["reason"])
        if state != "declined":
            assert any(e["kind"] != "formula" for e in answer["evidence"]), (drawing, question)
        if "cubicasa" in drawing:  # scans: read locally, or shown from the run log. The demo costs nothing.
            if answer["method"].startswith("vlm"):
                assert answer["replayed_from"] and "-router-" not in answer["replayed_from"]
                marks = [e for e in answer["evidence"] if e["kind"] in ("point", "box")]
                assert len(marks) == answer["answer"]  # the number is the count of the marked items
            elif answer["method"].startswith("labels+model"):  # the labels, and a logged model count beside them
                assert answer["replayed_from"] and answer["readings"]["labels"] is not None
                assert any(e["kind"] == "box" for e in answer["evidence"])
                assert any(e["kind"] == "context" and e["role"] == "marked by the vision model" for e in answer["evidence"])
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


@needs_data
def test_archive_drawings_with_dots_in_their_names_open(client):
    """Archive scans have no questions and names like 'hka.1398.jpg': they still open and show their image."""
    sources = {s["source"]: s for s in client.get("/api/sources").json()}
    archive = next((name for name, s in sources.items() if s["archive"] and s["drawings"] and name != "uploads"), None)
    if archive is None:
        pytest.skip("this dataset has no archive drawings")
    items = client.get("/api/drawings", params={"source": archive, "limit": 30}).json()["items"]
    dotted = next((i for i in items if "." in i["title"]), items[0])
    assert dotted["formats"]
    detail = client.get("/api/drawing", params={"drawing": dotted["id"]}).json()
    assert detail["view"]["kind"] in ("image", "page") and detail["view"]["width"] > 0
    assert client.get(detail["view"]["url"]).status_code == 200
    # Nobody wrote questions for it: a few general ones are offered, none with an answer on file.
    assert all(s["type"] == "count" for s in detail["suggested"])


@needs_data
def test_the_sample_sheets_come_with_their_questions_and_their_answers_cost_nothing_to_show(client):
    """The sheets Stoa posted are taken in at start. What their questions answered is kept with the app."""
    items = client.get("/api/drawings", params={"source": "uploads", "limit": 30}).json()["items"]
    slab = next(i for i in items if i["title"] == "Foundation and slab plan S-100F")
    detail = client.get("/api/drawing", params={"drawing": slab["id"]}).json()
    assert detail["example"] and "Stoa" in detail["credit"] and detail["view"]["kind"] == "image"
    questions = [s["question"] for s in detail["suggested"]]
    assert "What is the area of the 150 mm slab-on-grade in m²?" in questions
    answer = ask(client, slab["id"], "What is the area of the 150 mm slab-on-grade in m²?")
    assert answer["state"] == "check" and answer["cost_eur"] == 0 and answer["replayed_from"]
    assert answer["answer"] == 61.69 and answer["unit"] == "m2" and answer["method"].startswith("sheet-working")
    boxes = [e for e in answer["evidence"] if e["kind"] == "box"]
    formula = next(e["text"] for e in answer["evidence"] if e["kind"] == "formula")
    assert len(boxes) == 2 and formula == "6200 × 9950 = 61,690,000 mm² = 61.69 m²"   # each figure is a box on the sheet
    for e in boxes:
        x0, y0, x1, y1 = e["bbox"]
        assert 0 <= x0 < x1 <= detail["view"]["width"] and 0 <= y0 < y1 <= detail["view"]["height"]
    checks = {c["name"]: c["value"] for c in answer["checks"]}
    assert checks.pop("a second, independent working") == "the same result"   # arithmetic is worked out twice
    assert all(value == "the program reads the same figure here" for value in checks.values())
    # The steps it took are there to be read: the model gave the working, the calculator the number.
    steps = [step["title"] for step in answer["trace"]]
    assert "The model named the figures and wrote the expression, not the result" in steps
    assert "The calculator computed" in steps
    # A question nobody has asked yet is not answered without the model being asked for.
    fresh = ask(client, slab["id"], "How thick is the slab under the genset?")
    assert fresh["state"] == "declined" and fresh["cost_eur"] == 0 and fresh["can_use_model"]


def test_labels_are_shown_beside_a_model_answer_as_a_second_method():
    from blueprints.answer import Answer
    from blueprints.server import _second_opinion

    model = Answer(answer=3, unit="count", method="vlm-pointing:x",
                   evidence=[{"kind": "point", "space": "image_px", "x": 5, "y": 6, "label": "bedroom"}] * 3)
    labels = Answer(answer=2, unit="count", confidence=0.6, method="raster-labels:v1", evidence=[
        {"kind": "box", "space": "image_px", "bbox": [1, 2, 3, 4], "label": "MH"},
        {"kind": "box", "space": "image_px", "bbox": [5, 6, 7, 8], "label": "MH"},
        {"kind": "box", "space": "image_px", "bbox": [9, 9, 9, 9], "label": "3H+K", "role": "type line"},
    ])
    _second_opinion(model, labels)
    assert model.answer == 3 and model.state == "check"  # the answer and its state are the model's
    added = [e for e in model.evidence if e.get("role") == "label read by OCR"]
    assert len(added) == 2 and model.meta["labels_said"] == 2
    assert "a different count" in model.meta["checks"]["labels printed on the plan (local OCR)"]


@needs_data
def test_the_plan_image_is_placed_under_the_3d_model(client):
    """The four corners of the plan image, in mesh coordinates, enclose the walls raised from it."""
    ground = client.get("/api/ground", params={"drawing": SWISS}).json()
    header, positions, _ = mesh.unpack(client.get("/api/mesh", params={"drawing": SWISS}).content)
    xs = [c[0] for c in ground["corners"]]
    ys = [c[1] for c in ground["corners"]]
    assert min(xs) <= header["bbox"][0] and max(xs) >= header["bbox"][3]
    assert min(ys) <= header["bbox"][1] and max(ys) >= header["bbox"][4]
    # The image is not mirrored: its top-left corner is up and to the left of its bottom-right one.
    (x0, y0, _), _, (x2, y2, _), _ = ground["corners"]
    assert x0 < x2 and y0 > y2
    assert client.get("/api/ground", params={"drawing": "data/cubicasa5k/high_quality_architectural-14441"}).status_code == 404


@needs_data
def test_the_sheet_scale_lets_a_reader_measure(client):
    """Pixels per metre of the shown plan: a room's outline measured in pixels gives its area in m²."""
    detail = client.get("/api/drawing", params={"drawing": SWISS}).json()
    scale = detail["view"]["px_per_m"]
    assert scale and scale > 10
    room = max(detail["rooms"], key=lambda r: r["area_m2"])
    pts = room["points"]
    shoelace = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2
    assert shoelace / scale ** 2 == pytest.approx(room["area_m2"], rel=0.03)
    scan = client.get("/api/drawing", params={"drawing": "data/cubicasa5k/high_quality_architectural-14441"}).json()
    assert scan["view"]["px_per_m"] is None  # a scan has no known scale: nothing to measure with


@needs_data
def test_a_brought_drawing_opens_and_can_be_shared(client):
    """Someone brings their own plan: it opens, offers questions, and an answer becomes a sheet to share."""
    import io

    from PIL import Image, ImageDraw

    from blueprints.server import UPLOADS_DIR

    picture = Image.new("RGB", (900, 600), "white")
    ImageDraw.Draw(picture).rectangle([80, 80, 820, 520], outline="black", width=6)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    made = client.post("/api/upload", params={"name": "site sketch.png"}, content=buffer.getvalue())
    assert made.status_code == 200, made.text
    summary = made.json()
    try:
        assert summary["source"] == "uploads" and summary["title"] == "site sketch"
        detail = client.get("/api/drawing", params={"drawing": summary["id"]}).json()
        assert detail["view"]["kind"] == "image" and (detail["view"]["width"], detail["view"]["height"]) == (900, 600)
        assert detail["suggested"] and detail["can_enhance"] is False  # offline: no enlarged view
        assert client.get(detail["view"]["url"]).status_code == 200
        assert any(s["source"] == "uploads" for s in client.get("/api/sources").json())
        shared = client.post("/api/share", json={"drawing": summary["id"], "question": "How many windows are shown on this floor plan?"}).json()
        assert shared["hosted"] == "local"
        sheet_png = client.get(shared["url"])
        assert sheet_png.status_code == 200 and sheet_png.headers["content-type"] == "image/png"
        assert client.post("/api/upload", params={"name": "x.txt"}, content=b"not a drawing").status_code == 415
    finally:
        ident = summary["id"].split("/")[-1]
        for suffix in (".png", ".json"):
            (UPLOADS_DIR / f"{ident}{suffix}").unlink(missing_ok=True)


@needs_data
def test_a_small_scan_is_read_as_it_came_and_only_shown_enlarged(client, monkeypatch):
    """With Cloudinary on, a brought scan is stored untouched; the enlarged copy is a separate view of it."""
    import io

    from PIL import Image, ImageDraw

    from blueprints import cloud
    from blueprints.server import UPLOADS_DIR

    def never(*_args, **_kwargs):
        raise AssertionError("a brought drawing must not be replaced by a prepared copy")

    asked = []
    monkeypatch.setattr(cloud, "configured", lambda: True)
    monkeypatch.setattr(cloud, "prepare", never)
    monkeypatch.setattr(cloud, "enhance", lambda image, ident: asked.append(image) or {"public_id": "x", "url": "https://example.test/enlarged"})
    picture = Image.new("RGB", (700, 500), "white")
    ImageDraw.Draw(picture).rectangle([60, 60, 640, 440], outline="black", width=5)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    made = client.post("/api/upload", params={"name": "small scan.png"}, content=buffer.getvalue())
    assert made.status_code == 200, made.text
    drawing = made.json()["id"]
    ident = drawing.split("/")[-1]
    try:
        detail = client.get("/api/drawing", params={"drawing": drawing}).json()
        assert (detail["view"]["width"], detail["view"]["height"]) == (700, 500) and detail["can_enhance"] is True
        with Image.open(UPLOADS_DIR / f"{ident}.png") as stored:
            assert stored.size == (700, 500)
        for _ in range(2):  # asked for twice, enlarged once
            assert client.get("/api/enhanced", params={"drawing": drawing}).json()["url"] == "https://example.test/enlarged"
        assert len(asked) == 1 and asked[0] == UPLOADS_DIR / f"{ident}.png"
        # a full-size drawing has no enlarged view
        assert client.get("/api/drawing", params={"drawing": SWISS}).json()["can_enhance"] is False
        assert client.get("/api/enhanced", params={"drawing": SWISS}).status_code == 409
    finally:
        for suffix in (".png", ".json"):
            (UPLOADS_DIR / f"{ident}{suffix}").unlink(missing_ok=True)


@needs_data
def test_a_dataset_answer_becomes_an_evidence_sheet(client):
    shared = client.post("/api/share", json={"drawing": SWISS, "question": DOORS}).json()
    picture = client.get(shared["url"])
    assert picture.status_code == 200 and len(picture.content) > 50_000


@needs_data
def test_the_app_can_ask_which_examples_this_server_holds_and_what_a_long_answer_is_doing(client):
    known = client.post("/api/known", json={"drawings": [SWISS, "data/swiss_dwellings/no_such_floor", 7]}).json()
    assert known == {"known": [SWISS]}                      # an example on a drawing this dataset lacks is left out
    idle = client.get("/api/progress", params={"drawing": SWISS, "question": DOORS}).json()
    assert idle == {"text": None}                           # nothing is being worked on
    from blueprints import progress
    from blueprints.data import question_id

    progress.say("some-question", "Boxing the symbols tile by tile at full size: 3 of 8 tiles")
    assert progress.get("some-question").endswith("3 of 8 tiles")
    progress.clear("some-question")
    assert progress.get("some-question") is None and question_id("a", "b")


def test_a_long_model_answer_is_worked_on_once_and_a_request_that_outlives_its_wait_is_told_so(monkeypatch, tmp_path):
    """A proxy in front of the app cuts a request off after a while. The work goes on; asking again joins it."""
    import threading
    import time

    from blueprints import model_path, server
    from blueprints.answer import Answer
    from blueprints.data import Question

    calls, release = [], threading.Event()

    def slow_engine(vlm):
        def answer(q, dataset):
            calls.append(q.question)
            release.wait(5)
            return Answer(answer=7, unit="count", method="sheet-working:test", cost_usd=0.02,
                          evidence=[{"kind": "formula", "text": "7"}])
        return answer

    monkeypatch.setattr(model_path, "make", slow_engine)
    monkeypatch.setattr(server, "WAIT_FOR_MODEL_S", 0.2)
    monkeypatch.setattr(server.config, "REPO_ROOT", tmp_path)          # kept answers go to a scratch folder
    monkeypatch.setattr(server, "EXAMPLES_DIR", tmp_path / "examples")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    app_state = object.__new__(server.Service)                          # only the part that asks the model is needed
    app_state.lock, app_state._flights = threading.Lock(), {}
    q = Question(qid="q1", path="uploads/abc.png", question="How many doors?", answer=None, unit=None, type="",
                 source="uploads", drawing="uploads/abc", group="uploads")
    ask = app_state._model_fallback()
    try:
        ask(q, None)
        raise AssertionError("the first request should have been told the work is still going on")
    except server.StillWorking:
        pass
    try:
        app_state._model_fallback()(q, None)                            # asked again: joins the same work
    except server.StillWorking:
        pass
    assert calls == ["How many doors?"]                                 # the model was asked once
    release.set()
    time.sleep(0.3)
    first = app_state._model_fallback()(q, None)
    assert first.answer == 7 and first.cost_usd == 0.02
    again = app_state._model_fallback()(q, None)
    assert again.answer == 7 and again.cost_usd == 0.0                  # shown again from disk, paid for once
    assert calls == ["How many doors?"]


def test_a_model_file_too_large_to_mesh_is_refused_without_being_opened(tmp_path, monkeypatch):
    from blueprints import config, mesh

    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mesh, "MAX_IFC_BYTES", 10)
    big = tmp_path / "big.ifc"
    big.write_text("not a model at all, and never read as one")
    assert mesh.is_built(big)                      # settled: there is nothing to wait for
    with pytest.raises(mesh.MeshTooLarge, match="the model file is"):
        mesh.ifc_mesh(big)
