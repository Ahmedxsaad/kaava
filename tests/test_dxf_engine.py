"""DXF engine on the real dev floor plans: a golden regression test.

Skipped when the dataset is not downloaded. Dev split only.
"""

from dataclasses import replace

import pytest

from blueprints import config
from blueprints.scoring import CHECK, score_answer

pytestmark = pytest.mark.skipif(
    not (config.DATA_DIR / "junction-dataset-preview" / "questions.jsonl").is_file()
    or not config.SPLIT_FILE.is_file(),
    reason="dataset or split not present",
)

PLAN = "data/swiss_dwellings/site1000_bldg2082_floor9385.pdf"


@pytest.fixture(scope="module")
def dev_swiss():
    from blueprints.data import Dataset
    from blueprints.split import Split

    ds = Dataset(config.DATA_DIR / "junction-dataset-preview")
    questions = [q for q in Split.load().select(ds.questions, "dev") if q.source == "swiss_dwellings"]
    return ds, questions


def _ask(ds, questions, text, path=PLAN):
    from blueprints.engines import dxf

    q = next(q for q in questions if q.path == path)
    return dxf.answer(replace(q, question=text), ds)


def test_dev_floor_plans(dev_swiss):
    """Every count is right; every wrong area is one the engine itself scored as uncertain."""
    from blueprints.engines import dxf

    ds, questions = dev_swiss
    assert len(questions) > 1000
    wrong_counts, confident_wrong, right = [], [], 0
    for q in questions:
        ans = dxf.answer(q, ds)
        status = score_answer(ans.answer, q.answer, q.type, gold_unit=q.unit, pred_unit=ans.unit).status
        right += status == "correct"
        if status != "correct" and q.type == "count":
            wrong_counts.append((q.question, q.answer, ans.answer, ans.reason))
        if status == "wrong" and (ans.confidence or 0) > 0.6:
            confident_wrong.append((q.question, q.answer, ans.answer, ans.confidence))
    assert not wrong_counts, wrong_counts[:3]
    assert not confident_wrong, confident_wrong[:3]
    assert right / len(questions) >= 0.995


def test_counts_carry_one_box_per_counted_item(dev_swiss):
    ds, questions = dev_swiss
    for text, n in [("How many doors (interior doors and entrance doors together) are drawn on this floor plan?", 36),
                    ("How many windows are drawn on this floor plan?", 28),
                    ("How many bathtubs are drawn on this floor plan?", 4)]:
        ans = _ask(ds, questions, text)
        assert ans.answer == n == len(ans.evidence)
        assert all(e["kind"] == "box" and len(e["bbox"]) == 4 for e in ans.evidence)
        assert ans.cost_usd == 0.0 and ans.state == CHECK


def test_room_area_agrees_with_the_area_printed_in_the_room(dev_swiss):
    ds, questions = dev_swiss
    ans = _ask(ds, questions, "What is the net floor area of room 2.01 ('Zimmer') in m²?")
    assert ans.answer == 15.61 and ans.confidence == 0.99
    assert ans.meta["checks"]["area printed in the DXF"] == 15.61
    assert ans.meta["checks"]["area printed in the PDF"] == 15.61      # read from the other file
    assert ans.evidence[0]["kind"] == "polygon" and len(ans.evidence[0]["points"]) >= 4


def test_label_count_is_cross_checked_against_the_pdf(dev_swiss):
    ds, questions = dev_swiss
    ans = _ask(ds, questions, "How many rooms labelled 'Zimmer' are there on this floor?")
    assert ans.answer == 12 and ans.confidence == 0.99
    assert ans.meta["checks"] == {"room names in the DXF": 12, "room names in the PDF": 12}


def test_same_answer_in_every_language(dev_swiss):
    ds, questions = dev_swiss
    variants = ["How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
                "Wie viele Türen (Innentüren und Eingangstüren zusammen) sind auf diesem Grundriss eingezeichnet?",
                "Combien de portes (portes intérieures et portes d'entrée confondues) sont dessinées sur ce plan ?",
                "Quante porte (porte interne e porte d'ingresso insieme) sono disegnate in questa pianta?"]
    assert len({_ask(ds, questions, v).answer for v in variants}) == 1
    areas = ["What is the net floor area of apartment A in m², summing all its rooms except balconies, loggias, terraces and other outdoor spaces?",
             "Wie gross ist die Nettowohnfläche von Wohnung A in m² (Summe aller Räume ohne Balkone, Loggien, Terrassen und andere Aussenräume)?"]
    assert len({_ask(ds, questions, v).answer for v in areas}) == 1


def test_trick_questions_are_declined_or_flagged(dev_swiss):
    ds, questions = dev_swiss
    # A room number that exists, with a label that is not the one on the drawing.
    wrong_label = _ask(ds, questions, "What is the net floor area of room 2.01 ('Küche') in m²?")
    assert wrong_label.answer is None and "labelled 'Zimmer'" in wrong_label.reason
    assert _ask(ds, questions, "What is the net floor area of room 9.99 ('Zimmer') in m²?").answer is None
    assert _ask(ds, questions, "What is the net floor area of apartment Z in m²?").answer is None
    assert _ask(ds, questions, "How many lifts are drawn on this floor plan?").answer is None
    assert _ask(ds, questions, "What is the total length of all walls on this floor, in m?").answer is None
    # A label that is on no room: zero, not a guess.
    assert _ask(ds, questions, "How many rooms labelled 'Ballroom' are there on this floor?").answer == 0


def test_rooms_of_unknown_type_lower_the_score(dev_swiss):
    """Numbered rooms with no name printed: the floor total says so and is scored low."""
    ds, questions = dev_swiss
    ans = _ask(ds, questions, "What is the total net floor area in m² of all numbered indoor rooms on this floor "
                              "(excluding balconies, loggias, terraces and other outdoor spaces, shafts and voids)?",
               path="data/swiss_dwellings/site2150_bldg3001_floor13690.pdf")
    assert ans.confidence == 0.6 and "no name on the drawing" in ans.explanation
