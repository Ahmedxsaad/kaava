"""Working shown: the model names printed figures and rows; the code counts, computes and checks."""

import pytest

from blueprints import sheet_reading as sr
from blueprints import sheet_text
from blueprints.data import Question
from blueprints.scoring import CHECK, DECLINED
from blueprints.vlm import MockVLM, VLMReply

EMPTY = {"items": [], "figures": [], "expression": "", "result_unit": "", "text": "", "sources": [],
         "look_closer": {"what": "", "box_2d": []}, "cannot_tell": False, "basis": ""}
# A sheet 1000 x 800 with a few printed texts, as the program would have read them.
INDEX = {"source": "ocr", "width": 1000, "height": 800, "items": [
    {"id": 0, "text": "6200", "box": [400, 100, 460, 120]},
    {"id": 1, "text": "4975", "box": [700, 300, 720, 360]},
    {"id": 2, "text": "4975", "box": [700, 500, 720, 560]},
    {"id": 3, "text": "FP-1", "box": [100, 600, 140, 620]},
    {"id": 4, "text": "FP-2", "box": [200, 600, 240, 620]},
    {"id": 5, "text": "1,240 SF", "box": [800, 700, 880, 720]},
    {"id": 6, "text": "52'-8\"", "box": [50, 50, 110, 70]},
]}


class Reader(MockVLM):
    """Gives the same working each time it is asked, or the workings it was given one after another."""

    def __init__(self, *others, **reply):
        super().__init__()
        self.replies, self.seen = [{**EMPTY, **reply}, *({**EMPTY, **other} for other in others)], []

    def ask(self, system, images, text, schema):
        self.seen.append((system, text, schema))
        reply = self.replies[min(len(self.seen), len(self.replies)) - 1]
        if reply.get("fails"):
            return VLMReply(data=None, model="mock", cost_usd=0.0, error="the call failed")
        return VLMReply(data=reply, model="mock", cost_usd=0.02, latency_ms=5.0)


@pytest.fixture
def sheet(tiny, monkeypatch):
    monkeypatch.setattr(sheet_text, "read", lambda path, page=0: INDEX)
    monkeypatch.setattr(sr, "EXAMINERS", 0)  # the examiners have their own tests (test_examiner.py)
    scan = next(q for q in tiny.questions if q.source == "cubicasa5k")

    def ask(vlm, question):
        q = Question(qid="x", path=scan.path, question=question, answer=None, unit=None, type="",
                     source=scan.source, drawing=scan.drawing, group=scan.group)
        return sr.make_engine(vlm)(q, tiny)

    return ask


def figure(name, text_id, printed, value, unit, what="a figure"):
    return {"name": name, "text_id": text_id, "box_2d": [0, 0, 10, 10], "printed": printed, "value": value,
            "unit": unit, "what": what}


def test_a_printed_figure_is_read_as_the_number_it_states():
    assert sr.number_in("6,092 SF") == 6092
    assert sr.number_in("52'-8\"") == pytest.approx(52 + 8 / 12)
    assert sr.number_in("5'-10 3/8\"") == pytest.approx(5 + 10.375 / 12)
    assert sr.number_in("7'-0\"", "in") == 84
    assert sr.number_in("15,61") == 15.61 and sr.number_in("1,234.5") == 1234.5
    assert sr.number_in("D13 @ 300") is None      # two numbers: not one figure
    assert sr.number_in("27 000") is None         # a gap in a figure is not guessed at


def test_only_plain_arithmetic_over_the_listed_figures_is_computed():
    assert sr.evaluate("a * (b + c)", {"a": 6200, "b": 4975, "c": 4975}) == 61_690_000
    assert sr.evaluate("sum", {"a": 1, "b": 2.5}) == 3.5
    for bad in ("__import__('os').system('true')", "a ** 2", "a.real", "abs(a)", "z + 1", "a /", "'a'"):
        with pytest.raises(ValueError):
            sr.evaluate(bad, {"a": 1})


def test_units_are_converted_by_the_code_to_the_unit_the_question_asks_for():
    assert sr.convert(61_690_000, "mm2", "m2") == (pytest.approx(61.69), "m2")
    assert sr.convert(100, "ft", "m") == (pytest.approx(30.48), "m")
    assert sr.convert(5, "m2", "m") == (5, "m2")  # an area is not turned into a length
    assert sr.asked_unit("What is the area of the slab in m²?") == "m2"
    assert sr.asked_unit("What is the total square footage?") == "ft2"
    assert sr.asked_unit("How many linear metres of baseboard?") == "m"
    assert sr.asked_unit("How wide is door 121.A?") is None


def test_a_quantity_is_computed_from_the_figures_and_each_figure_is_checked_against_the_programs_reading(sheet):
    vlm = Reader(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm"),
                          figure("c", 2, "4975", 4975, "mm")],
                 expression="a * (b + c)", result_unit="mm2", basis="the slab's clear dimensions")
    ans = sheet(vlm, "What is the area of the 150 mm slab-on-grade in m²?")
    assert ans.answer == 61.69 and ans.unit == "m2" and ans.state == CHECK
    assert len(vlm.seen) == 2 and ans.cost_usd == 0.04      # worked out twice, independently, and both are paid for
    boxes = [e for e in ans.evidence if e["kind"] == "box"]
    assert [e["bbox"] for e in boxes] == [[400, 100, 460, 120], [700, 300, 720, 360], [700, 500, 720, 560]]
    assert ans.evidence[-1] == {"kind": "formula", "text": "6200 × (4975 + 4975) = 61,690,000 mm² = 61.69 m²"}
    checks = ans.meta["checks"]
    assert checks.pop("a second, independent working") == "the same result"
    assert all(v == "the program reads the same figure here" for v in checks.values())
    assert ans.confidence == 0.9
    steps = [title for title, _ in ans.meta["trace"]]
    assert steps == ["A program read the sheet", "The model named the figures and wrote the expression, not the result",
                     "The program read each figure again at the place marked", "The calculator computed",
                     "The question was worked out a second time, independently"]
    system, text, schema = vlm.seen[0]
    assert "0 | 6200 |" in text and "5 | 1,240 SF |" in text   # the model is given the texts the program read
    assert "answer_number" not in schema["properties"]         # and has no field to state the result in


def test_a_figure_the_program_reads_differently_means_no_number(sheet):
    vlm = Reader(figures=[figure("a", 0, "6800", 6800, "mm")], expression="a", result_unit="mm")
    ans = sheet(vlm, "How long is the slab in metres?")
    assert ans.state == DECLINED and ans.answer is None
    assert "the program reads 6,200 here, the model 6,800" in ans.reason and "no number is given" in ans.reason


def test_two_workings_that_disagree_mean_no_number_and_both_are_shown(sheet):
    one = dict(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm")],
               expression="a * b", result_unit="mm2")
    other = dict(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm"),
                          figure("c", 2, "4975", 4975, "mm")], expression="a * (b + c)", result_unit="mm2")
    ans = sheet(Reader(other, **one), "What is the area of the slab in m²?")
    assert ans.state == DECLINED and ans.cost_usd == 0.04
    assert "Two independent workings gave different results" in ans.reason
    assert "30.84 m²" in ans.reason and "61.69 m²" in ans.reason and "6200 × (4975 + 4975)" in ans.reason
    # One working that answers and one that finds no way is not agreement either.
    unsure = sheet(Reader({"cannot_tell": True, "basis": "the room's length is not printed"}, **one), "What is the area?")
    assert unsure.state == DECLINED and "the other found no way to answer" in unsure.reason
    # Two that agree within half a per cent are one result; a count must agree exactly.
    near = dict(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975.2, "mm")], expression="a + b", result_unit="mm")
    exact = dict(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm")], expression="a + b", result_unit="mm")
    assert sheet(Reader(near, **exact), "How long is it in mm?").answer == 11175
    rows = [{"text_id": -1, "box_2d": [10 * n, 10, 10 * n + 8, 18], "label": f"row {n}"} for n in range(14)]
    counted = sheet(Reader({"items": rows[:13]}, items=rows), "How many doors are listed in the schedule?")
    assert counted.state == DECLINED and "14 count" in counted.reason and "13 count" in counted.reason


def test_one_printed_figure_is_looked_up_once_and_arithmetic_is_worked_out_twice(sheet):
    lookup = Reader(figures=[figure("a", 0, "6200", 6200, "mm")], expression="a", result_unit="mm")
    assert sheet(lookup, "How long is the slab in mm?").answer == 6200 and len(lookup.seen) == 1
    few = Reader(items=[{"text_id": 3, "box_2d": [0, 0, 5, 5], "label": "pump"}])
    assert sheet(few, "How many pumps are shown?").answer == 1 and len(few.seen) == 1
    assert sr.worth_working_twice({**EMPTY, "figures": [figure("a", 0, "1", 1, "m")], "expression": "a * 2"})
    assert not sr.worth_working_twice({**EMPTY, "text": "1:200"})


def test_when_one_of_the_two_calls_fails_the_answer_rests_on_one_working_and_says_so(sheet):
    vlm = Reader({"fails": True}, figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm")],
                 expression="a + b", result_unit="mm")
    ans = sheet(vlm, "How long is the slab in mm?")
    assert ans.answer == 11175 and ans.meta["trace"][-1] == ["A second working", "the second call failed, so there is only one"]
    assert "a second, independent working" not in ans.meta["checks"]


def test_a_figure_given_in_another_unit_of_length_still_matches(sheet):
    vlm = Reader(figures=[figure("a", 0, "6200", 6.2, "m")], expression="a", result_unit="m")
    ans = sheet(vlm, "How long is the slab in metres?")
    assert ans.answer == 6.2 and ans.confidence == 0.85


def test_feet_and_inches_and_table_entries(sheet):
    feet = sheet(Reader(figures=[figure("a", 6, "52'-8\"", 52.6667, "ft")], expression="a", result_unit="ft"),
                 "How wide is the north wing, in metres?")
    assert feet.answer == pytest.approx(16.05, abs=0.01) and feet.unit == "m" and feet.confidence == 0.85
    table = sheet(Reader(figures=[figure("a", 5, "1,240 SF", 1240, "ft2")], expression="sum", result_unit="ft2"),
                  "What is the common space circulation area?")
    assert table.answer == 1240 and table.unit == "ft2"


def test_a_count_is_the_number_of_listed_items_and_a_text_named_twice_is_one_item(sheet):
    items = [{"text_id": 3, "box_2d": [0, 0, 5, 5], "label": "pump"}, {"text_id": 4, "box_2d": [0, 0, 5, 5], "label": "pump"},
             {"text_id": 3, "box_2d": [0, 0, 5, 5], "label": "the same pump again"},
             {"text_id": -1, "box_2d": [100, 100, 200, 200], "label": "a pump with no tag"}]
    ans = sheet(Reader(items=items), "How many fueling pumps are shown?")
    assert ans.answer == 3 and ans.unit == "count" and len(ans.evidence) == 3
    assert ans.evidence[0]["bbox"] == [100, 600, 140, 620]   # the printed tag's own box
    assert ans.meta["checks"] == {"items listed": 3, "identified by a printed text": 2}
    assert ans.meta["trace"][-1][0] == "The program counted the list"


def test_nothing_printed_means_no_number(sheet):
    ans = sheet(Reader(cannot_tell=True, basis="no dimension of this room is printed"), "What is the area of the hall?")
    assert ans.state == DECLINED and "no dimension of this room is printed" in ans.reason
    broken = sheet(Reader(figures=[figure("a", 0, "6200", 6200, "mm")], expression="a * b", result_unit="mm2"),
                   "What is the area?")
    assert broken.state == DECLINED and "not listed: b" in broken.reason


def test_a_descriptive_answer_carries_where_it_was_read(sheet):
    ans = sheet(Reader(text="1:200", sources=[{"text_id": 0, "box_2d": [0, 0, 5, 5]}]), "What is the scale?")
    assert ans.answer == "1:200" and ans.evidence[0]["role"] == "read here"


def test_a_pdf_page_is_turned_into_pixels_and_back(tmp_path, monkeypatch):
    """Tiles and measuring work on pixels; on a PDF what they find is given back in the page's points."""
    import pymupdf

    from blueprints import config

    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    path = tmp_path / "sheet.pdf"
    doc = pymupdf.open()
    doc.new_page(width=1200, height=600).insert_text((100, 100), "0", fontsize=12)
    doc.save(path)
    index = {"source": "pdf", "width": 1200, "height": 600, "items": [{"id": 0, "text": "0", "box": [100, 90, 108, 102]}]}
    picture, zoom, in_pixels = sr.as_pixels(path, index)
    assert picture.suffix == ".png" and picture.is_file() and zoom == 5.0     # 6000 pixels for 1200 points
    assert in_pixels["items"][0]["box"] == [500, 450, 540, 510] and in_pixels["width"] == 6000
    assert sr._to_source({"bbox": [500, 450, 540, 510]}, zoom) == {"bbox": [100, 90, 108, 102]}
    image = tmp_path / "plan.png"
    image.write_bytes(b"x")
    assert sr.as_pixels(image, index) == (image, 1.0, index)                   # an image is itself


def test_a_call_that_brings_nothing_back_is_asked_again_and_only_so_many_times(sheet):
    """A call that failed, or thought in circles until its reply was cut off, is not the end of the question."""
    working = dict(figures=[figure("a", 0, "6200", 6200, "mm"), figure("b", 1, "4975", 4975, "mm")], expression="a + b", result_unit="mm")
    vlm = Reader(working, fails=True)                       # the first call fails, the ones after it answer
    ans = sheet(vlm, "How long is the slab in mm?")
    assert ans.answer == 11175 and ans.error is None and len(vlm.seen) >= 2
    never = Reader({"fails": True}, {"fails": True}, {"fails": True}, fails=True)   # the service is down: every call fails
    gone = sheet(never, "How long is the slab in mm?")
    assert gone.answer is None and gone.error == "the call failed" and len(never.seen) == 1 + sr.ASK_AGAIN


def test_a_spiral_is_asked_again_with_the_small_cap_and_a_long_reply_with_the_full_one(sheet):
    """Cut off while thinking: the same small cap again. Cut off while writing: the reply is long, so the full cap."""
    working = dict(figures=[figure("a", 0, "6200", 6200, "mm")], expression="a", result_unit="mm")
    cut = "reply cut off (max_output_tokens)"

    class Capped(MockVLM):
        def __init__(self, first):
            super().__init__()
            self.max_output_tokens, self.first, self.caps = 8000, first, []   # a copy of the adapter shares `caps`

        def ask(self, system, images, text, schema):
            self.caps.append(self.max_output_tokens)
            if len(self.caps) == 1:
                return self.first
            return VLMReply(data={**EMPTY, **working}, model="mock", cost_usd=0.02, latency_ms=5.0)

    spiral = Capped(VLMReply(data=None, model="mock", cost_usd=0.03, latency_ms=35000.0, output_tokens=3000,
                             thought_tokens=2997, no_answer=cut))
    ans = sheet(spiral, "How long is the slab in mm?")
    assert ans.answer == 6200 and spiral.caps[:2] == [sr.QUICK_TOKENS, sr.QUICK_TOKENS]
    assert ans.cost_usd == pytest.approx(0.05) and ans.latency_ms == pytest.approx(35005.0)   # the lost attempt is counted
    long = Capped(VLMReply(data=None, model="mock", cost_usd=0.03, latency_ms=30000.0, output_tokens=3000,
                           thought_tokens=0, no_answer=cut))
    assert sheet(long, "How long is the slab in mm?").answer == 6200 and long.caps[:2] == [sr.QUICK_TOKENS, 8000]
