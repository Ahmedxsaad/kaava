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
