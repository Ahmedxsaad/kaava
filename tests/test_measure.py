"""Measuring a room on a plan that prints no dimensions: wall pixels against the scale bar."""

import pytest
from PIL import Image, ImageDraw

from blueprints import measure

PX_PER_M = 50  # the plan below is drawn at 50 pixels to the metre


@pytest.fixture
def plan(tmp_path):
    """A room 8 m x 5 m inside walls 10 px thick, with a 1 m door opening, a window, and a 10 m scale bar."""
    sheet = Image.new("L", (1200, 800), 255)
    d = ImageDraw.Draw(sheet)
    x0, y0, x1, y1 = 200, 150, 600, 400                           # the room's inner faces
    d.rectangle([x0 - 10, y0 - 10, x1 + 10, y0 - 1], fill=0)      # top wall
    d.rectangle([x0 - 10, y1 + 1, x1 + 10, y1 + 10], fill=0)      # bottom wall
    d.rectangle([x0 - 10, y0 - 10, x0 - 1, y1 + 10], fill=0)      # left wall
    d.rectangle([x1 + 1, y0 - 10, x1 + 10, y1 + 10], fill=0)      # right wall
    d.rectangle([300, y1 + 1, 349, y1 + 10], fill=255)            # a door opening 1 m wide in the bottom wall
    d.rectangle([x0 - 10, 220, x0 - 1, 320], fill=255)            # a window 2 m wide in the left wall ...
    d.line([x0 - 9, 220, x0 - 9, 320], fill=0, width=1)           # ... drawn as two thin lines: still wall
    d.line([x0 - 2, 220, x0 - 2, 320], fill=0, width=1)
    d.rectangle([420, 250, 470, 280], outline=0, width=1)         # a table: thin lines are not walls
    d.line([700, 700, 1200 - 1, 700], fill=0, width=2)            # the scale bar: 500 px for 10 m
    d.line([700, 690, 700, 700], fill=0, width=2)
    d.line([1199, 690, 1199, 700], fill=0, width=2)
    path = tmp_path / "plan.png"
    sheet.save(path)
    index = {"width": 1200, "height": 800, "items": [
        {"id": 0, "text": "0", "box": [694, 710, 706, 730]},
        {"id": 1, "text": "5m", "box": [935, 710, 965, 730]},
        {"id": 2, "text": "10m", "box": [1170, 710, 1198, 730]},
        {"id": 3, "text": "1:100", "box": [700, 740, 760, 760]},
    ]}
    return path, index


def test_the_scale_is_read_from_the_bar_between_its_end_marks(plan):
    path, index = plan
    bar = measure.scale_bar(path, index)
    assert bar["length_m"] == 10 and bar["label"] == "10 m"
    assert bar["px_per_m"] == pytest.approx(PX_PER_M, rel=0.01)
    assert bar["labels_in_place"] == bar["labels"] == 2           # "5m" stands halfway, "10m" at the end
    assert measure.scale_bar(path, {"width": 1200, "height": 800, "items": index["items"][3:]}) is None


def test_a_rough_box_is_moved_onto_the_walls_and_measured(plan):
    path, _ = plan
    rough = [215, 170, 580, 385]                                  # each side some 15-20 px inside the room
    room = measure.measure_room(path, rough, PX_PER_M)
    assert room["ok"] and room["bbox"] == [200.0, 150.0, 600.0, 400.0]
    assert room["width_m"] == pytest.approx(8.0, abs=0.03) and room["height_m"] == pytest.approx(5.0, abs=0.03)
    assert room["area_m2"] == pytest.approx(40.0, rel=0.01) and room["perimeter_m"] == pytest.approx(26.0, rel=0.01)
    # The door opening is found; the window, drawn with lines across it, is not an opening.
    assert [(o["side"], round(o["width_m"], 1)) for o in room["openings"]] == [("bottom", 1.0)]
    outside = measure.measure_room(path, [185, 135, 615, 415], PX_PER_M)   # a box drawn too large snaps back in
    assert outside["ok"] and outside["bbox"] == room["bbox"]


def test_what_is_not_a_walled_rectangle_is_not_measured(plan):
    path, _ = plan
    nowhere = measure.measure_room(path, [750, 200, 1000, 450], PX_PER_M)     # empty paper: no walls
    assert not nowhere["ok"] and "no wall found" in nowhere["why"]
    tiny = measure.measure_room(path, [300, 200, 310, 210], PX_PER_M)
    assert not tiny["ok"]
