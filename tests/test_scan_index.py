"""A scan read once: every counting question about it is a count over the stored reading."""

from blueprints import scan_index
from blueprints.scoring import DECLINED
from blueprints.vlm import MockVLM, VLMReply


def test_the_dataset_s_scan_questions_map_to_the_kinds_they_count():
    asked = scan_index.kinds_asked
    assert asked("How many windows are shown on this floor plan?") == ("window",)
    assert asked("Kuinka monta ikkunaa pohjapiirustuksessa on?") == ("window",)
    assert asked("How many doors are shown on this floor plan? Count doorways fitted with a door; open doorways "
                 "without a door leaf do not count.") == ("door",)
    assert asked("How many toilet bowls (WC seats) are drawn on this floor plan?") == ("toilet_bowl",)
    assert asked("How many separate WC rooms (toilet rooms marked WC) are on this floor plan?") == ("wc_room",)
    assert asked("How many sinks and wash basins (kitchen and bathroom combined; a double-bowl sink counts as one) "
                 "are drawn on this floor plan?") == ("sink",)
    assert asked("How many showers and bathtubs are drawn on this floor plan in total?") == ("shower", "bathtub")
    assert asked("Kuinka monta kerrosta pohjapiirustuksessa on esitetty?") == ("floor_plan",)
    assert asked("How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?") == ("bedroom",)
    # Not a count over the whole drawing's index: these go to the model as they are.
    assert asked("How many doors are in apartment 3?") is None
    assert asked("How many rooms labelled 'MH' are there?") is None
    assert asked("What is the area of the kitchen in m²?") is None


class Marker(MockVLM):
    native_boxes = True

    def __init__(self, items, unsure=()):
        super().__init__()
        self.items, self.unsure = items, list(unsure)

    def ask(self, system, images, text, schema):
        self.calls += 1
        kinds = schema["properties"]["items"]["items"]["properties"]["label"]["enum"]
        return VLMReply(data={"items": [i for i in self.items if i["label"] in kinds],
                              "unsure": [k for k in self.unsure if k in kinds]},
                        model="mock", cost_usd=0.012, latency_ms=4.0)


def box(label):
    return {"box_2d": [100, 100, 200, 200], "label": label}


def _questions(tiny):
    scan = next(q for q in tiny.questions if q.source == "cubicasa5k")
    from blueprints.data import Question

    def make(text):
        return Question(qid=text[:8], path=scan.path, question=text, answer=None, unit=None, type="count",
                        source=scan.source, drawing=scan.drawing, group=scan.group)

    return make


def test_one_reading_answers_every_question_and_is_paid_for_once(tiny, tmp_path):
    vlm = Marker([box("window"), box("window"), box("door"), box("shower"), box("bathtub"), box("bedroom")])
    engine = scan_index.make_engine(vlm, "one", cache_dir=tmp_path)
    ask = _questions(tiny)
    windows = engine(ask("How many windows are shown on this floor plan?"), tiny)
    finnish = engine(ask("Kuinka monta ikkunaa pohjapiirustuksessa on?"), tiny)
    wet = engine(ask("How many showers and bathtubs are drawn on this floor plan in total?"), tiny)
    saunas = engine(ask("How many saunas are on this floor plan?"), tiny)
    assert (windows.answer, finnish.answer, wet.answer, saunas.answer) == (2, 2, 2, 0)
    assert vlm.calls == 1                                             # the drawing was looked at once
    assert windows.cost_usd == 0.012 and finnish.cost_usd == wet.cost_usd == saunas.cost_usd == 0.0
    assert len(windows.evidence) == 2 and windows.evidence[0]["kind"] == "box"
    assert saunas.evidence[0]["kind"] == "formula"                    # nothing of the kind: said, not left empty

    # A later run finds the reading on disk and charges what it cost to the first question that needs it.
    later = scan_index.make_engine(Marker([]), "one", cache_dir=tmp_path)
    again = later(ask("How many doors are shown on this floor plan?"), tiny)
    assert again.answer == 1 and again.cost_usd == 0.012


def test_a_kind_the_model_could_not_mark_gets_no_number(tiny, tmp_path):
    engine = scan_index.make_engine(Marker([box("sink")], unsure=["sink"]), "one", cache_dir=tmp_path)
    ans = engine(_questions(tiny)("How many sinks and wash basins are drawn on this floor plan?"), tiny)
    assert ans.state == DECLINED and "sink" in ans.reason


def test_a_question_the_index_does_not_hold_goes_elsewhere(tiny, tmp_path):
    vlm = Marker([])
    engine = scan_index.make_engine(vlm, "one", cache_dir=tmp_path)
    ans = engine(_questions(tiny)("What is the area of the kitchen in m²?"), tiny)
    assert ans.state == DECLINED and vlm.calls == 0
    called = []
    passed = scan_index.make_engine(vlm, "one", other=lambda q, ds: called.append(q) or ans, cache_dir=tmp_path)
    passed(_questions(tiny)("How many doors are in apartment 3?"), tiny)
    assert len(called) == 1
