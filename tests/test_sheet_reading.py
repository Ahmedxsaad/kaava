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
    def __init__(self, **reply):
        super().__init__()
        self.reply, self.seen = {**EMPTY, **reply}, []

    def ask(self, system, images, text, schema):
        self.seen.append((system, text, schema))
        return VLMReply(data=self.reply, model="mock", cost_usd=0.02, latency_ms=5.0)


@pytest.fixture
def sheet(tiny, monkeypatch):
    monkeypatch.setattr(sheet_text, "read", lambda path, page=0: INDEX)
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
    assert ans.answer == 61.69 and ans.unit == "m2" and ans.state == CHECK and ans.cost_usd == 0.02
    boxes = [e for e in ans.evidence if e["kind"] == "box"]
    assert [e["bbox"] for e in boxes] == [[400, 100, 460, 120], [700, 300, 720, 360], [700, 500, 720, 560]]
    assert ans.evidence[-1] == {"kind": "formula", "text": "6200 × (4975 + 4975) = 61,690,000 mm² = 61.69 m²"}
    assert all(v == "the program reads the same figure here" for v in ans.meta["checks"].values())
    assert ans.confidence == 0.85
    system, text, schema = vlm.seen[0]
    assert "0 | 6200 |" in text and "5 | 1,240 SF |" in text   # the model is given the texts the program read
    assert "answer_number" not in schema["properties"]         # and has no field to state the result in


def test_a_figure_the_model_misread_shows_as_a_disagreement(sheet):
    vlm = Reader(figures=[figure("a", 0, "6800", 6800, "mm")], expression="a", result_unit="mm")
    ans = sheet(vlm, "How long is the slab in metres?")
    assert ans.answer == 6.8 and ans.confidence == 0.3
    assert "the program reads 6,200 here, the model 6,800" in list(ans.meta["checks"].values())[0]
    assert "1 differ" in ans.explanation


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
