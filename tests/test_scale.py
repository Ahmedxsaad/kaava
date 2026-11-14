"""The scale of a sheet from what on it has a known size, and rooms measured with their holes taken out."""

import pytest
from PIL import Image, ImageDraw

from blueprints import measure, scale
from blueprints import sheet_reading as sr
from blueprints.data import Question
from blueprints.scoring import CHECK, DECLINED

PPM = 50  # every plan below is drawn at 50 pixels to the metre
W, H = 1200, 800
BY_BAR = (50 / 49.9) ** 2  # the drawn scale bar is 499 px for 10 m: areas read against it are this much larger


def plan(tmp_path, name="plan.png", bar=True, column=False, doors=0, dimension=False, notch=False):
    """A room 8 m by 5 m (inner faces 200..600 by 150..400) with a 1 m door opening in its bottom wall."""
    im = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 200, 150, 600, 400
    d.rectangle([x0 - 10, y0 - 10, x1 + 10, y0 - 1], fill=0)
    d.rectangle([x0 - 10, y1 + 1, x1 + 10, y1 + 10], fill=0)
    d.rectangle([x0 - 10, y0 - 10, x0 - 1, y1 + 10], fill=0)
    d.rectangle([x1 + 1, y0 - 10, x1 + 10, y1 + 10], fill=0)
    d.rectangle([300, y1 + 1, 349, y1 + 10], fill=255)             # the door opening, 1 m
    items = []
    if column:                                                      # a column 0.6 m square standing in the room
        d.rectangle([400, 250, 429, 279], fill=0)
    if notch:                                                       # a block 3 m by 2 m walled off in a corner: an L-shaped room
        d.rectangle([x1 - 150, y0, x1 - 141, y0 + 100], fill=0)
        d.rectangle([x1 - 150, y0 + 100, x1, y0 + 109], fill=0)
    if bar:                                                         # a 10 m scale bar
        d.line([700, 700, W - 1, 700], fill=0, width=2)
        d.line([700, 690, 700, 700], fill=0, width=2)
        d.line([W - 1, 690, W - 1, 700], fill=0, width=2)
        items += [{"id": 0, "text": "0", "box": [694, 710, 706, 730]}, {"id": 1, "text": "10m", "box": [1170, 710, 1198, 730]}]
    if dimension:                                                   # "8000" over a dimension line from face to face
        d.line([x0, 100, x1, 100], fill=0, width=1)
        d.line([x0, 80, x0, 120], fill=0, width=1)
        d.line([x1, 80, x1, 120], fill=0, width=1)
        items.append({"id": 5, "text": "8000", "box": [380, 78, 420, 96]})
    boxes = []
    for n in range(doors):                                          # door symbols 0.9 m: a leaf and its quarter-circle swing
        cx, cy = 700 + 110 * n, 300
        d.line([cx, cy, cx, cy - 45], fill=0, width=2)
        d.arc([cx - 45, cy - 45, cx + 45, cy + 45], 270, 360, fill=0, width=2)
        boxes.append([cx - 4, cy - 50, cx + 51, cy + 5])            # the model's box: a few pixels loose
    path = tmp_path / name
    im.save(path)
    measure._grey.cache_clear(); measure._masks.cache_clear()
    return path, {"source": "ocr", "width": W, "height": H, "items": items}, boxes


def grid(box):
    """A pixel box as the model gives it: [ymin, xmin, ymax, xmax] on the 0-1000 grid."""
    x0, y0, x1, y1 = box
    return [round(1000 * y0 / H), round(1000 * x0 / W), round(1000 * y1 / H), round(1000 * x1 / W)]


def ask(path, index, quantity="area", rectangular=True, question="What is the floor area of the hall in m²?", **more):
    q = Question(qid="x", path=str(path), question=question, answer=None, unit=None, type="", source="s", drawing="d", group="g")
    room = {"text_id": -1, "name": "Hall", "box_2d": grid([215, 165, 585, 385]), "rectangular": rectangular}
    return sr._measured(q, path, index, quantity, [room], W, H, dict(method="m", cost_usd=0.0, latency_ms=0.0, meta={}), **more)


def test_a_printed_dimension_gives_the_scale_and_its_ends_are_found_in_the_pixels(tmp_path):
    path, index, _ = plan(tmp_path, bar=False, dimension=True)
    found = scale.from_dimension(path, (206, 103), (592, 98), 8.0, "8000 mm", grid_px=1.2)   # the model points a few pixels off
    assert found["px_per_m"] == pytest.approx(PPM, rel=0.005) and not found["estimate"]
    assert found["checks"]["ends of the dimension line found in the pixels"] == "2 of 2"


def test_doors_give_an_estimate_of_the_scale_with_the_range_a_standard_leaves_open(tmp_path):
    path, _, boxes = plan(tmp_path, bar=False, doors=4)
    found = scale.from_doors(path, boxes)
    assert found["estimate"] and found["px_per_m"] == pytest.approx(PPM, rel=0.06)   # the doors drawn are 0.9 m
    assert found["low"] == pytest.approx(found["px_per_m"] * 0.9, rel=0.01)           # if the doors are 1.0 m
    assert found["high"] == pytest.approx(found["px_per_m"] * 0.9 / 0.8, rel=0.01)    # if they are 0.8 m
    assert scale.from_doors(path, boxes[:1]) is None                                  # one door is not a standard


def test_door_openings_must_come_out_as_wide_as_doors_are():
    assert scale.door_check([{"width_m": 0.9}, {"width_m": 1.7}])["ok"]              # a single door and a double one
    wrong = scale.door_check([{"width_m": 1.25}, {"width_m": 1.2}])
    assert not wrong["ok"] and wrong["leaf_m"] == pytest.approx(1.225)
    assert scale.door_check([]) is None


def test_a_column_standing_in_a_room_is_taken_out_of_its_area_and_drawn(tmp_path):
    path, index, _ = plan(tmp_path, column=True)
    ans = ask(path, index)
    assert ans.state == CHECK and ans.answer == pytest.approx(BY_BAR * (8.02 * 5.02 - 0.36), abs=0.08)
    room = next(e for e in ans.evidence if e["kind"] == "polygon")
    assert len(room["holes"]) == 1 and "1 hole" in ans.explanation
    assert any(step[0] == "Took out what stands inside the room" for step in ans.meta["trace"])
    plain = ask(*plan(tmp_path, "plain.png")[:2])
    assert plain.answer == pytest.approx(BY_BAR * 8.02 * 5.02, abs=0.06) and "hole" not in plain.explanation   # nothing to take out


def test_a_room_that_is_not_a_rectangle_is_measured_by_its_own_outline(tmp_path):
    path, index, _ = plan(tmp_path, notch=True)
    ans = ask(path, index, rectangular=False)
    block = 3.0 * 2.2                               # the walled-off block with its two walls: 150 px by 110 px
    assert ans.state == CHECK and ans.answer == pytest.approx(BY_BAR * (8.02 * 5.02 - block), rel=0.01)
    assert "by its own outline" in ans.explanation and any(e["kind"] == "polygon" for e in ans.evidence)


def test_with_no_printed_scale_the_doors_are_the_ruler_and_the_answer_is_said_to_be_an_estimate(tmp_path):
    path, index, boxes = plan(tmp_path, bar=False, doors=4)
    ans = ask(path, index, doors=[grid(b) for b in boxes])
    assert ans.state == CHECK and ans.confidence == 0.3 and ans.meta["not_verifiable"]
    assert ans.answer == pytest.approx(40.0, rel=0.12)                 # right if the doors are 0.9 m, as drawn
    low, high = ans.meta["estimate"]["low"], ans.meta["estimate"]["high"]
    assert low < ans.answer < high and high / low == pytest.approx((1.0 / 0.8) ** 2, rel=0.02)
    assert "This is an estimate" in ans.explanation and "standard doors" in ans.explanation
    assert sum(1 for e in ans.evidence if e.get("role") == "door used as the ruler") == 4
    nothing = ask(path, index)                                         # no bar, no dimension, no doors pointed at
    assert nothing.state == DECLINED and "nothing to take its scale from" in nothing.reason


def test_a_scale_that_makes_the_doors_too_wide_is_not_trusted(tmp_path):
    path, index, _ = plan(tmp_path, bar=True)
    index["items"][1]["text"] = "8m"       # the bar's label misread: 500 px taken for 8 m, so the 1 m door comes out 0.8 m ... still a door
    assert ask(path, index, quantity="baseboard", question="How many metres of baseboard?").state == CHECK
    index["items"][1]["text"] = "13m"      # ... and here the door comes out 1.3 m: a double door, of two 0.65 m leaves: still plausible
    assert ask(path, index, quantity="baseboard", question="How many metres of baseboard?").state == CHECK
