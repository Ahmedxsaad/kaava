"""Examiners look inside a computed area for what must be taken out of it (examiner.py)."""

import pytest

from blueprints import examiner
from blueprints import sheet_reading as sr
from blueprints import sheet_text
from blueprints.data import Question
from blueprints.render import Rendered
from blueprints.scoring import CHECK
from blueprints.vlm import MockVLM, VLMReply

INDEX = {"source": "ocr", "width": 1000, "height": 800, "items": [
    {"id": 0, "text": "6200", "box": [400, 100, 460, 120]},
    {"id": 1, "text": "4975", "box": [700, 300, 720, 360]},
    {"id": 2, "text": "4975", "box": [700, 500, 720, 560]},
    {"id": 7, "text": "4275", "box": [500, 400, 520, 460]},
    {"id": 8, "text": "400", "box": [440, 380, 470, 396]},
]}
EMPTY = {"items": [], "figures": [], "expression": "", "result_unit": "", "text": "", "sources": [],
         "look_closer": {"what": "", "box_2d": []}, "cannot_tell": False, "basis": ""}


def figure(name, text_id, printed, value, unit="mm", what="a dimension"):
    return {"name": name, "text_id": text_id, "box_2d": [0, 0, 10, 10], "printed": printed, "value": value, "unit": unit, "what": what}


SLAB = {**EMPTY, "figures": [figure("a", 0, "6200", 6200), figure("b", 1, "4975", 4975), figure("c", 2, "4975", 4975)],
        "expression": "a * (b + c)", "result_unit": "mm2", "basis": "the slab's printed dimensions"}
TRENCH = {"what": "MOTOR CONTROL CENTER TRENCH", "box_2d": [450, 400, 700, 470], "effect": "deduct", "figures": [], "term": ""}
SIZED = {**TRENCH, "figures": [figure("d1", 7, "4275", 4275, what="trench length"), figure("d2", 8, "400", 400, what="trench width")],
         "term": "d1 * d2"}


class Readers(MockVLM):
    """Gives the working to whoever asks for one, and to each examiner the next of the reports it was given."""

    def __init__(self, *reports):
        super().__init__()
        self.reports, self.examined = list(reports), 0
        self.scale_from = {"text_id": -1, "unit": "", "from": [], "to": []}

    def ask(self, system, images, text, schema):
        if system == examiner.SYSTEM:
            assert len(images) == 2 and "The working to check" in text      # the sheet, and the enlarged view
            report = self.reports[min(self.examined, len(self.reports) - 1)]
            self.examined += 1
            return VLMReply(data={"parts": report, "scale_from": self.scale_from, "basis": "looked inside the slab"},
                            model="mock", cost_usd=0.01, latency_ms=5.0)
        return VLMReply(data=SLAB, model="mock", cost_usd=0.02, latency_ms=5.0)


@pytest.fixture
def ask(tiny, monkeypatch):
    monkeypatch.setattr(sheet_text, "read", lambda path, page=0: INDEX)
    monkeypatch.setattr(sr, "EXAMINERS", 2)
    monkeypatch.setattr(examiner, "enlarged", lambda picture, boxes, max_edge: (Rendered(b"png", "image/png", 10, 10, 0, 1.0), [0, 0, 500, 400]))
    scan = next(q for q in tiny.questions if q.source == "cubicasa5k")

    def run(vlm):
        q = Question(qid="x", path=scan.path, question="What is the area of the 150 mm slab-on-grade in m²?", answer=None,
                     unit=None, type="", source=scan.source, drawing=scan.drawing, group=scan.group)
        return sr.make_engine(vlm)(q, tiny)

    return run


def test_a_trench_whose_width_is_not_printed_is_marked_and_the_area_is_said_to_be_at_most(ask):
    vlm = Readers([TRENCH], [TRENCH])
    ans = ask(vlm)
    assert vlm.examined == 2 and ans.state == CHECK and ans.answer == 61.69          # nothing is estimated
    assert ans.meta["bound"] == "at most" and "MOTOR CONTROL CENTER TRENCH" in ans.meta["caveat"]
    assert "size is not printed" in ans.meta["caveat"] and ans.confidence <= 0.4
    marks = [e for e in ans.evidence if e.get("role") == "inside it, not taken out"]
    assert len(marks) == 1                                                            # both examiners marked the same trench: one mark
    assert ans.cost_usd == pytest.approx(0.02 * 2 + 0.01 * 2)


def test_a_part_whose_size_is_printed_is_taken_out_when_both_examiners_agree(ask):
    ans = ask(Readers([SIZED], [SIZED]))
    assert ans.state == CHECK and ans.answer == pytest.approx(61.69 - 4.275 * 0.4, abs=0.006)   # 59.98 m²
    assert "The dimensions alone give 61.69 m²" in ans.explanation and "Both examiners" in ans.explanation
    assert any(e.get("role") == "taken out" for e in ans.evidence)
    assert ans.meta["checks"]["before the examiners' correction"] == "61.69 m²"
    assert "caveat" not in ans.meta


def test_a_correction_only_one_examiner_makes_is_not_applied_and_is_said(ask):
    ans = ask(Readers([SIZED], []))
    assert ans.answer == 61.69 and "did not make the same correction" in ans.meta["caveat"]
    assert any(e.get("role") == "inside it, not taken out" for e in ans.evidence)


def test_nothing_inside_leaves_the_answer_as_it_was(ask):
    ans = ask(Readers([], []))
    assert ans.answer == 61.69 and "caveat" not in ans.meta
    assert any("examiner" in step[0] and "nothing found" in step[1] for step in ans.meta["trace"])
    assert not examiner.worth_examining({"figures": [figure("a", 5, "1,240 SF", 1240, "ft2")], "expression": "a"}, "ft2")   # a total read off a table


def test_a_trench_drawn_as_a_rectangle_is_measured_against_a_printed_dimension_and_taken_out(ask, monkeypatch):
    from blueprints import measure, scale

    # The examiners point at the 6200 dimension; the program finds its ends (100 px per metre here) and the trench's rectangle.
    monkeypatch.setattr(scale, "from_dimension", lambda path, start, end, length_m, printed, grid: {
        "source": "dimension", "px_per_m": 100.0, "low": 99.0, "high": 101.0, "estimate": False, "label": printed,
        "said": f"a dimension printed on the plan ({printed})", "evidence": [], "trace": ["scale", printed], "checks": {"scale": printed}})
    monkeypatch.setattr(measure, "drawn_rectangle", lambda path, box: [box[0], box[1], box[0] + 40.0, box[1] + 427.5])
    vlm = Readers([TRENCH], [TRENCH])
    vlm.scale_from = {"text_id": 0, "unit": "mm", "from": [130, 100], "to": [130, 700]}
    ans = ask(vlm)
    assert ans.state == CHECK and ans.answer == pytest.approx(61.69 - 0.4 * 4.275, abs=0.006)     # 59.98 m²
    assert "61.69 m² by the printed dimensions" in ans.meta["caveat"] and "measured where it is drawn" in ans.meta["caveat"]
    assert any(e.get("role") == "taken out, measured on the drawing" for e in ans.evidence)
    assert ans.confidence <= 0.45 and ans.meta.get("bound") is None
    # No rectangle found where the examiner pointed: nothing is guessed, the number stays and says "at most".
    monkeypatch.setattr(measure, "drawn_rectangle", lambda path, box: None)
    vlm = Readers([TRENCH], [TRENCH])
    vlm.scale_from = {"text_id": 0, "unit": "mm", "from": [130, 100], "to": [130, 700]}
    kept = ask(vlm)
    assert kept.answer == 61.69 and kept.meta["bound"] == "at most"
