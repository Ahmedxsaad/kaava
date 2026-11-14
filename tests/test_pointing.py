"""Counting by pointing: the code counts the model's list; it never takes a number from it."""

from blueprints import pointing
from blueprints.scoring import CHECK, DECLINED
from blueprints.vlm import MockVLM, VLMReply


class Pointer(MockVLM):
    def __init__(self, data):
        super().__init__()
        self.data, self.seen = data, []

    def ask(self, system, images, text, schema):
        self.seen.append((system, text, schema))
        return VLMReply(data=self.data, model="mock", cost_usd=0.004, latency_ms=3.0)


def _count_question(tiny):
    return next(q for q in tiny.questions if q.source == "cubicasa5k")


def test_answer_is_the_length_of_the_list(tiny):
    items = [{"x": 10, "y": 20, "label": "window"}, {"x": 60, "y": 20, "label": "window"},
             {"x": 100, "y": 70, "label": "window"}]
    vlm = Pointer({"items": items, "cannot_tell": False, "confidence": 0.8, "basis": "three openings"})
    ans = pointing.make_engine(vlm)(_count_question(tiny), tiny)
    assert ans.answer == 3 and ans.unit == "count" and ans.state == CHECK
    assert [e["kind"] for e in ans.evidence] == ["point"] * 3
    assert (ans.evidence[0]["x"], ans.evidence[0]["y"]) == (10, 20) and all(e["inside"] for e in ans.evidence)
    assert ans.cost_usd == 0.004
    system, text, schema = vlm.seen[0]
    assert "120 x 90 pixels" in text                      # the model is told the image size
    assert "answer_number" not in schema["properties"]    # there is no field to state a number in


def test_empty_list_is_zero_and_cannot_tell_is_a_decline(tiny):
    q = _count_question(tiny)
    zero = pointing.make_engine(Pointer({"items": [], "cannot_tell": False, "confidence": 0.9, "basis": "none"}))(q, tiny)
    assert zero.answer == 0 and zero.evidence
    unsure = pointing.make_engine(Pointer({"items": [], "cannot_tell": True, "confidence": 0.1, "basis": "blurry"}))(q, tiny)
    assert unsure.state == DECLINED and "blurry" in unsure.reason


def test_malformed_points_are_dropped_not_counted(tiny):
    items = [{"x": 10, "y": 20, "label": "ok"}, {"x": "left", "y": 5, "label": "bad"}, {"label": "no coordinates"}]
    ans = pointing.make_engine(Pointer({"items": items, "cannot_tell": False, "confidence": 0.5, "basis": ""}))(
        _count_question(tiny), tiny)
    assert ans.answer == 1


def test_area_questions_are_not_pointed_at(tiny):
    q = next(q for q in tiny.questions if q.type == "area" and q.path.endswith(".pdf"))
    vlm = Pointer({"answer_number": 12.5, "answer_text": None, "confidence": 0.5, "basis": "read"})
    ans = pointing.make_engine(vlm)(q, tiny)
    assert ans.answer == 12.5
    assert "answer_number" in vlm.seen[0][2]["properties"]   # the plain image-only prompt was used


class Boxer(Pointer):
    native_boxes = True  # a model that locates things as 0-1000 boxes, like Gemini


def test_a_box_model_is_asked_for_boxes_and_they_land_in_file_pixels(tiny):
    items = [{"box_2d": [100, 250, 300, 500], "label": "door"},      # ymin, xmin, ymax, xmax on a 0-1000 grid
             {"box_2d": [900, 800, 700, 600], "label": "door"},      # corners given the other way round
             {"box_2d": [500, 500, 500, 500], "label": "door"}]      # a single spot: widened so it shows
    vlm = Boxer({"items": items, "cannot_tell": False, "confidence": 0.8, "basis": "door leaves with swing arcs"})
    ans = pointing.make_engine(vlm)(_count_question(tiny), tiny)
    assert ans.answer == 3 and ans.state == CHECK and ":boxes" in ans.method and ans.method.endswith(f":p{pointing.PROMPT}")
    assert [e["kind"] for e in ans.evidence] == ["box"] * 3 and all(e["space"] == "image_px" for e in ans.evidence)
    assert ans.evidence[0]["bbox"] == [30.0, 9.0, 60.0, 27.0]         # the tiny plan is 120 x 90 pixels
    assert ans.evidence[1]["bbox"] == [72.0, 63.0, 96.0, 81.0]
    x0, y0, x1, y1 = ans.evidence[2]["bbox"]
    assert x1 > x0 and y1 > y0 and all(e["inside"] for e in ans.evidence)
    system, text, schema = vlm.seen[0]
    assert "box_2d" in system and "0-1000" in system and "pixels" not in text
    assert "box_2d" in schema["properties"]["items"]["items"]["properties"]


def test_things_that_are_not_boxes_are_dropped_not_counted(tiny):
    items = [{"box_2d": [10, 10, 60, 60], "label": "ok"}, {"box_2d": [1, 2, 3], "label": "three numbers"},
             {"box_2d": "everywhere", "label": "text"}, {"label": "no box"}, "door"]
    ans = pointing.make_engine(Boxer({"items": items, "cannot_tell": False, "confidence": 0.5, "basis": ""}))(
        _count_question(tiny), tiny)
    assert ans.answer == 1
    off = pointing.make_engine(Boxer({"items": [{"box_2d": [900, 900, 1200, 1300], "label": "off the sheet"}],
                                      "cannot_tell": False, "confidence": 0.5, "basis": ""}))(_count_question(tiny), tiny)
    assert off.answer == 1 and off.evidence[0]["inside"] is False
