"""Rules of the raster label engine, on synthetic OCR output (no OCR model needed)."""

from types import SimpleNamespace

import pytest

from blueprints.engines import ocr_index, raster_labels as rl
from blueprints.scoring import CHECK, DECLINED


def box(text, x, y, w=44, h=20, score=0.99, turn=0):
    """One detected text box with a single reading."""
    return {"box": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], "det": 0.9,
            "reads": [{"turn": turn, "text": text, "score": score}]}


def plan(*items, passes=2):
    """A plan read at `passes` sizes, with the same boxes found each time."""
    return {"version": 2, "models": "test", "width": 800, "height": 600, "seconds": 0.0,
            "passes": [{"scale": 1.0 + i / 2, "seconds": 0.0, "items": [dict(it) for it in items]}
                       for i in range(passes)]}


LABELLED = [box("OH", 100, 100), box("MH", 300, 100), box("MH", 500, 100), box("KH", 100, 300),
            box("ET", 300, 300), box("WC", 500, 300), box("Parveke", 100, 500, w=120)]


# ----------------------------------------------------------------- the question


@pytest.mark.parametrize("question, kind", [
    ("How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?", "bedroom"),
    ("Kuinka monta makuuhuonetta (MH) pohjapiirustuksessa on?", "bedroom"),
    ("How many kitchens (including kitchenettes, open kitchens and utility kitchens) are on this floor plan?",
     "kitchen"),
    ("Kuinka monta keittiötä (myös keittokomerot, avo- ja tupakeittiöt sekä apukeittiöt) pohjapiirustuksessa on?",
     "kitchen"),
    ("How many bathrooms or washrooms (e.g. KH, KPH, PH, PESUH, SH) are on this floor plan, "
     "not counting separate WC rooms?", "bathroom"),
    ("Kuinka monta kylpy- tai pesuhuonetta (esim. KH, KPH, PH, PESUH, SH) pohjapiirustuksessa on, "
     "erillisiä WC-tiloja lukuun ottamatta?", "bathroom"),
    ("How many separate WC rooms (toilet rooms marked WC) are on this floor plan?", "wc"),
    ("Kuinka monta erillistä WC-tilaa pohjapiirustuksessa on?", "wc"),
    ("How many saunas are on this floor plan?", "sauna"),
    ("Kuinka monta saunaa pohjapiirustuksessa on?", "sauna"),
    ("How many balconies (parveke, including glazed balconies) are on this floor plan?", "balcony"),
    ("Kuinka monta parveketta (myös lasitetut parvekkeet) pohjapiirustuksessa on?", "balcony"),
])
def test_room_questions_are_recognised(question, kind):
    assert rl.room_kind_in(question) == kind


@pytest.mark.parametrize("question", [
    "How many windows are shown on this floor plan?",
    "Kuinka monta ikkunaa pohjapiirustuksessa on?",
    "How many toilet bowls (WC seats) are drawn on this floor plan?",
    "Kuinka monta WC-istuinta pohjapiirustukseen on piirretty?",
    "How many sinks and wash basins (kitchen and bathroom combined; a double-bowl sink counts as one) "
    "are drawn on this floor plan?",
    "Kuinka monta pesu- ja tiskiallasta (keittiö ja kylpytilat yhteensä; kaksoisallas lasketaan yhdeksi) "
    "pohjapiirustukseen on piirretty?",
    "How many showers and bathtubs are drawn on this floor plan in total?",
    "Kuinka monta suihkua ja kylpyammetta pohjapiirustukseen on piirretty yhteensä?",
    "How many doors are shown on this floor plan? Count doorways fitted with a door; "
    "open doorways without a door leaf do not count.",
    "How many floors (storeys) does this floor plan show?",
    "Kuinka monta kerrosta pohjapiirustuksessa on esitetty?",
    "What is the area of the kitchen in m²?",
])
def test_symbol_and_other_questions_are_not_room_counts(question):
    assert rl.room_kind_in(question) is None


# ----------------------------------------------------------------- one reading


@pytest.mark.parametrize("text, kinds", [
    ("MH", {"bedroom"}), ("MH 15.0", {"bedroom"}), ("Mh2", {"bedroom"}), ("Makuuhuone", {"bedroom"}),
    ("MAK.H.10.22²", {"bedroom"}), ("MAKUUTILA", {"bedroom"}),
    ("K", {"kitchen"}), ("K 17.6", {"kitchen"}), ("KK", {"kitchen"}), ("Keittiö", {"kitchen"}),
    ("TUPAK.", {"kitchen"}), ("OH+KK", {"kitchen"}),
    ("KH", {"bathroom"}), ("Kph", {"bathroom"}), ("PH 3.7", {"bathroom"}), ("PESUH.", {"bathroom"}),
    ("KPH/WC", {"bathroom"}),            # a toilet inside a bathroom is not a separate WC room
    ("WC", {"wc"}), ("wC", {"wc"}),
    ("S", {"sauna"}), ("S 2.8", {"sauna"}), ("Sauna", {"sauna"}),
    ("Parveke", {"balcony"}), ("Parv.", {"balcony"}), ("LASITETTU PARVEKE", {"balcony"}),
    ("KHH 7.6", set()), ("TERASSI", set()), ("KUISTI", set()), ("SK", set()), ("VH", set()),
    ("PARVI", set()),                    # a loft, not a balcony
    ("HW", set()), ("3650", set()),
])
def test_label_kinds(text, kinds):
    assert set(rl.read_text(text).kinds) == kinds


def test_other_labels_are_known_but_noise_is_not():
    assert rl.read_text("KHH 7.6").known and rl.read_text("OH").known and rl.read_text("JK").known
    assert not rl.read_text("HW").known and not rl.read_text("H").known and not rl.read_text("回8").known


def test_areas_and_lone_letters():
    assert rl.read_text("15.0").only_area and rl.read_text("30,0 m²").only_area
    assert rl.read_text("MH 15.0").has_area and not rl.read_text("MH").has_area
    assert rl.read_text("K").bare and rl.read_text("S").bare and not rl.read_text("KK").bare


@pytest.mark.parametrize("text, rooms, kinds", [
    ("3H+K+S", 3, {"kitchen", "sauna"}),
    ("2H+KK", 2, {"kitchen"}),
    ("2h+k+s 62,0 m2", 2, {"kitchen", "sauna"}),
    ("3H+K, 81,0 m²", 3, {"kitchen"}),
    ("2-3h+k+s 71", 3, {"kitchen", "sauna"}),
    ("1 H + KK", 1, {"kitchen"}),
    ("2HK", 2, {"kitchen"}),
    ("A40- 1H+KT 28M²", 1, {"kitchen"}),
    ("2H+KK+SA/50", 2, {"kitchen", "sauna"}),
    ("H+K+S", None, {"kitchen", "sauna"}),      # the digit was not read
    ("CH+KT", None, {"kitchen"}),               # "3" read as "C"
])
def test_type_line(text, rooms, kinds):
    reading = rl.read_text(text)
    assert reading.type_line is not None and not reading.kinds
    assert reading.type_line["rooms"] == rooms and set(reading.type_line["kinds"]) == kinds


def test_room_labels_are_not_type_lines():
    for text in ("OH+KK", "MH", "KH/WC", "OH", "HUONE"):
        assert rl.read_text(text).type_line is None


def test_the_turn_that_gives_a_known_label_wins():
    reads = [{"turn": 0, "text": "M", "score": 0.6}, {"turn": 90, "text": "MH", "score": 0.97},
             {"turn": 270, "text": "HW", "score": 1.0}]
    score, reading = rl.choose(reads)
    assert reading.text == "MH" and score == 0.97
    assert rl.choose([{"turn": 0, "text": "MH", "score": 0.3}]) is None  # too weak to use


def test_quarter_turns_depend_on_the_shape_of_the_box():
    assert ocr_index.turns_for(120, 20) == (0, [2])          # a line of text: upright or upside down
    assert ocr_index.turns_for(20, 120) == (3, [1])          # vertical text: reading up or down
    assert ocr_index.turns_for(30, 22) == (0, [1, 2, 3])     # a short label: any quarter turn


def test_base_scale_brings_the_long_edge_into_range():
    assert ocr_index.base_scale(500, 400) == pytest.approx(2.0)
    assert ocr_index.base_scale(1200, 900) == 1.0
    assert ocr_index.base_scale(3600, 1800) == pytest.approx(0.5)


# ----------------------------------------------------------------- merging and counting


def test_same_place_at_two_sizes_is_one_label():
    labels = rl.merge(plan(*LABELLED))
    assert len(labels) == len(LABELLED)
    assert all(len(lb.readings) == 2 for lb in labels)


def test_counts_from_labels():
    index = plan(*LABELLED)
    assert rl.count_kind(index, "bedroom").answer == 2
    assert rl.count_kind(index, "bathroom").answer == 1
    assert rl.count_kind(index, "wc").answer == 1
    assert rl.count_kind(index, "balcony").answer == 1
    bedroom = rl.count_kind(index, "bedroom")
    assert bedroom.source == "labels" and bedroom.score == 0.6 and len(bedroom.counted) == 2
    # A label that is there, read the same at both sizes, scores higher than a label that is absent.
    assert rl.count_kind(index, "wc").score == 0.9 and rl.count_kind(index, "bathroom").score == 0.99
    no_wc = plan(*[it for it in LABELLED if it["reads"][0]["text"] != "WC"])
    assert (rl.count_kind(no_wc, "wc").answer, rl.count_kind(no_wc, "wc").score) == (0, 0.6)


def test_type_line_agreeing_with_labels_gives_the_top_score():
    index = plan(*LABELLED, box("3H+K+S", 300, 500, w=110), box("K", 300, 200, w=16), box("Sauna", 500, 500, w=80))
    bedroom = rl.count_kind(index, "bedroom")
    assert (bedroom.answer, bedroom.score, bedroom.source) == (2, 0.99, "labels+type")
    sauna = rl.count_kind(index, "sauna")
    assert (sauna.answer, sauna.score, sauna.source) == (1, 0.99, "labels+type")
    kitchen = rl.count_kind(index, "kitchen")
    assert (kitchen.answer, kitchen.score, kitchen.source) == (1, 0.99, "labels+type")


def test_type_line_overrules_labels_that_disagree_at_a_low_score():
    # Four "MH" on the sheet (a neighbour's rooms are visible) but the home is a 3H+K.
    index = plan(box("OH", 100, 100), box("MH", 300, 100), box("MH", 500, 100), box("MH", 100, 300),
                 box("MH", 300, 300), box("3H+K", 500, 500, w=80))
    bedroom = rl.count_kind(index, "bedroom")
    assert (bedroom.answer, bedroom.score, bedroom.source) == (2, 0.6, "type")


def test_sauna_follows_the_type_line():
    stated = plan(*LABELLED, box("2H+KK+S", 300, 500, w=110))
    sauna = rl.count_kind(stated, "sauna")
    assert (sauna.answer, sauna.source) == (1, "type")           # the label was not read; the type line says so
    none = plan(*LABELLED, box("2H+KT", 300, 500, w=110), box("S", 500, 500, w=16))
    sauna = rl.count_kind(none, "sauna")
    assert (sauna.answer, sauna.source) == (0, "type")           # a lone S is not a sauna when the type line has none


def test_no_sauna_label_and_no_type_line_is_declined():
    result = rl.count_kind(plan(*LABELLED), "sauna")
    assert result.answer is None and "type line" in result.reason


def test_zero_kitchens_or_bathrooms_is_never_answered_from_missing_labels():
    index = plan(box("OH", 100, 100), box("MH", 300, 100), box("ET", 500, 100))
    assert rl.count_kind(index, "kitchen").answer is None
    assert rl.count_kind(index, "bathroom").answer is None
    assert rl.count_kind(index, "wc").answer == 0               # a home without a separate WC is ordinary


def test_lone_k_next_to_a_full_kitchen_label_is_a_cupboard():
    index = plan(*LABELLED, box("KK", 300, 200), box("K", 600, 200, w=16), box("K", 650, 400, w=16))
    kitchen = rl.count_kind(index, "kitchen")
    assert kitchen.answer == 1 and kitchen.counted[0].best("kitchen")[2].text == "KK"


def test_several_lone_k_count_as_one_kitchen_at_a_low_score():
    index = plan(*LABELLED, box("K", 600, 200, w=16), box("K", 650, 400, w=16))
    kitchen = rl.count_kind(index, "kitchen")
    assert kitchen.answer == 1 and kitchen.score == 0.6


def test_open_plan_label_and_its_kitchenette_are_one_kitchen():
    index = plan(*LABELLED, box("OH+KK", 300, 200, w=80), box("KK", 600, 200))
    assert rl.count_kind(index, "kitchen").answer == 1


def test_small_single_letters_are_not_room_labels():
    index = plan(*LABELLED, box("S", 600, 200, w=6, h=8))
    assert rl.count_kind(index, "sauna").answer is None  # the tiny S is not a label; nothing else says sauna


def test_label_read_at_one_size_only_scores_lower():
    index = plan(*LABELLED)
    index["passes"][1]["items"] = [it for it in index["passes"][1]["items"] if it["reads"][0]["text"] != "WC"]
    wc = rl.count_kind(index, "wc")
    assert wc.answer == 1 and wc.score == 0.6


def test_unlabelled_plan_is_declined():
    result = rl.count_kind(plan(box("3650", 100, 100), box("Pohjakuva", 300, 100, w=120)), "bedroom")
    assert result.answer is None and "no readable room labels" in result.reason


def test_mirrored_plan_is_declined():
    index = plan(box("OH", 100, 100), box("HM", 300, 100), box("HM", 500, 100), box("K", 100, 300, w=16),
                 box("ET", 300, 300))
    result = rl.count_kind(index, "bedroom")
    assert result.answer is None and "mirrored" in result.reason


# ----------------------------------------------------------------- the engine


def _ask(monkeypatch, index, question, path="data/cubicasa5k/x.png"):
    monkeypatch.setattr(ocr_index, "load_index", lambda _path: index)
    q = SimpleNamespace(path=path, question=question)
    return rl.answer(q, SimpleNamespace(abspath=lambda rel: rel))


def test_answer_carries_one_box_per_counted_label(monkeypatch):
    ans = _ask(monkeypatch, plan(*LABELLED), "How many bedrooms (makuuhuone, usually marked MH) are on this floor plan?")
    assert ans.answer == 2 and ans.unit == "count" and ans.state == CHECK and ans.method == rl.METHOD
    assert ans.cost_usd == 0.0 and ans.confidence == 0.6
    boxes = [e for e in ans.evidence if e["kind"] == "box"]
    assert len(boxes) == 2 and all(e["space"] == "image_px" and e["label"] == "MH" for e in boxes)
    assert boxes[0]["bbox"] == [300.0, 100.0, 344.0, 120.0] or boxes[1]["bbox"] == [300.0, 100.0, 344.0, 120.0]


def test_answer_from_the_type_line_shows_the_type_line(monkeypatch):
    index = plan(*LABELLED, box("2H+KK+S", 300, 500, w=110))
    ans = _ask(monkeypatch, index, "Kuinka monta saunaa pohjapiirustuksessa on?")
    assert ans.answer == 1 and ans.meta["source"] == "type"
    assert any(e["kind"] == "box" and e.get("role") == "type line" for e in ans.evidence)
    assert any(e["kind"] == "formula" for e in ans.evidence)


def test_zero_answer_has_formula_evidence(monkeypatch):
    index = plan(box("OH", 100, 100), box("MH", 300, 100), box("ET", 500, 100), box("KH", 100, 300),
                 box("K", 300, 300, w=16))
    ans = _ask(monkeypatch, index, "How many separate WC rooms (toilet rooms marked WC) are on this floor plan?")
    assert ans.answer == 0 and ans.evidence and ans.evidence[0]["kind"] == "formula"


def test_questions_outside_the_engine_are_declined_without_reading_the_plan(monkeypatch):
    def boom(_path):
        raise AssertionError("the plan must not be read for a question the engine does not cover")

    monkeypatch.setattr(ocr_index, "load_index", boom)
    q = SimpleNamespace(path="data/cubicasa5k/x.png", question="How many windows are shown on this floor plan?")
    ans = rl.answer(q, SimpleNamespace(abspath=lambda rel: rel))
    assert ans.state == DECLINED and "detector" in ans.reason
    q = SimpleNamespace(path="data/swiss_dwellings/x.pdf", question="How many saunas are on this floor plan?")
    assert rl.answer(q, SimpleNamespace(abspath=lambda rel: rel)).state == DECLINED
