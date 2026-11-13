"""Evidence drawn on the plan: the DXF and the PDF of a floor line up, and the picture exists."""

from dataclasses import replace

import pytest

from blueprints import config

pytestmark = pytest.mark.skipif(
    not (config.DATA_DIR / "junction-dataset-preview" / "questions.jsonl").is_file()
    or not config.SPLIT_FILE.is_file(),
    reason="dataset or split not present",
)

PLAN = "data/swiss_dwellings/site1000_bldg2082_floor9385"


@pytest.fixture(scope="module")
def plan():
    from blueprints.data import Dataset
    from blueprints.engines import dxf_index

    ds = Dataset(config.DATA_DIR / "junction-dataset-preview")
    return ds, dxf_index.load_index(ds.abspath(PLAN + ".dxf"))


def test_drawing_and_pdf_line_up_and_confirm_the_printed_scale(plan):
    from blueprints import overlay

    ds, index = plan
    t = overlay.fit_transform(index, ds.abspath(PLAN + ".pdf"))
    assert t["anchors"] >= 20 and t["residual_pt"] < 1.0
    # The sheet says "Massstab 1:200". The scale recovered from the geometry agrees,
    # which also confirms the DXF really is in metres.
    assert t["paper_scale"] == pytest.approx(200, rel=0.01)


def test_no_overlay_rather_than_a_misleading_one(plan):
    from blueprints import overlay

    ds, index = plan
    shuffled = dict(index)
    anchors = index["anchors"]
    # Room numbers attached to the wrong places: the fit must be rejected.
    shuffled["anchors"] = [[a[0], b[1], b[2], a[3]] for a, b in zip(anchors, anchors[5:] + anchors[:5])]
    assert overlay.fit_transform(shuffled, ds.abspath(PLAN + ".pdf")) is None
    assert overlay.fit_transform({**index, "anchors": anchors[:2]}, ds.abspath(PLAN + ".pdf")) is None


def test_render_writes_a_picture_with_the_evidence(plan, tmp_path):
    from PIL import Image

    from blueprints import overlay
    from blueprints.engines import dxf
    from blueprints.split import Split

    ds, index = plan
    q = next(q for q in Split.load().select(ds.questions, "dev") if q.path == PLAN + ".pdf")
    text = "How many windows are drawn on this floor plan?"
    ans = dxf.answer(replace(q, question=text), ds)
    out = tmp_path / "windows.png"
    used = overlay.render(ans, text, ds.abspath(PLAN + ".pdf"), index, out)
    assert used is not None and out.is_file()
    with Image.open(out) as im:
        assert im.width >= 1700 and im.height > 1500
        # The pin colour is on the picture: something was drawn over the plan.
        colours = im.convert("RGB").getcolors(maxcolors=1 << 24)
        assert any(c == overlay._PIN for _, c in colours)
