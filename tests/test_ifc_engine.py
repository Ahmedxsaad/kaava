"""IFC engine on the real dev models: a golden regression test.

Skipped when the dataset is not downloaded. Dev split only: it never opens
calibration or test answers.
"""

import pytest

from blueprints import config
from blueprints.scoring import CHECK, score_answer

pytestmark = pytest.mark.skipif(
    not (config.DATA_DIR / "junction-dataset-preview" / "questions.jsonl").is_file()
    or not config.SPLIT_FILE.is_file(),
    reason="dataset or split not present",
)


@pytest.fixture(scope="module")
def dev_ifc():
    from blueprints.data import Dataset
    from blueprints.split import Split

    ds = Dataset(config.DATA_DIR / "junction-dataset-preview")
    questions = [q for q in Split.load().select(ds.questions, "dev") if q.source == "ifc_models"]
    return ds, questions


def _ask(ds, questions, text):
    from blueprints.engines import ifc

    q = next(q for q in questions if q.question == text)
    return q, ifc.answer(q, ds)


def test_every_dev_ifc_question_is_answered_correctly(dev_ifc):
    from blueprints.engines import ifc

    ds, questions = dev_ifc
    assert len(questions) > 600
    wrong = []
    for q in questions:
        ans = ifc.answer(q, ds)
        score = score_answer(ans.answer, q.answer, q.type, gold_unit=q.unit, pred_unit=ans.unit)
        if score.status != "correct":
            wrong.append((q.question, q.answer, ans.answer, ans.reason))
    assert not wrong, wrong[:5]


def test_count_carries_every_counted_element_as_evidence(dev_ifc):
    ds, questions = dev_ifc
    q, ans = _ask(ds, questions, "How many doors are shown on the floor plan of '1.korrus'?")
    assert ans.answer == q.answer == len(ans.evidence)
    assert all(e["kind"] == "ifc_element" and e["class"] == "IfcDoor" and len(e["id"]) == 22 for e in ans.evidence)
    assert len({e["id"] for e in ans.evidence}) == len(ans.evidence)   # no element counted twice
    assert ans.cost_usd == 0.0 and ans.state == CHECK                 # not Verified before calibration


def test_room_area_is_cross_checked_by_two_independent_outlines(dev_ifc):
    ds, questions = dev_ifc
    q, ans = _ask(ds, questions, "What is the net floor area of the room 'KORIDOR (119)' on '1.korrus', in m²?")
    checks = ans.meta["checks"]
    assert ans.answer == pytest.approx(q.answer, rel=0.02) and ans.unit == "m2"
    assert checks["body geometry"] == pytest.approx(checks["footprint outline"], rel=0.005)
    assert ans.confidence == 0.99


def test_same_answer_in_english_and_estonian(dev_ifc):

    ds, questions = dev_ifc
    pairs = [("How many doors are shown on the floor plan of '2.korrus'?", "Mitu ust on näidatud korruse „2.korrus“ plaanil?"),
             ("What is the total net floor area of all rooms (spaces) on '1.korrus', in m²?",
              "Kui suur on kõigi ruumide netopõrandapind kokku korrusel „1.korrus“ (m²)?")]
    for en, et in pairs:
        (_, a), (_, b) = _ask(ds, questions, en), _ask(ds, questions, et)
        assert a.answer == b.answer


def test_things_not_in_the_model_are_declined_not_guessed(dev_ifc):
    from dataclasses import replace

    from blueprints.engines import ifc

    ds, questions = dev_ifc
    base = next(q for q in questions if q.path.endswith("v18.ifc"))
    for text, why in [
        ("How many doors are shown on the floor plan of '9.korrus'?", "no storey"),
        ("How many lifts are shown on the floor plan of '1.korrus'?", "element kind"),
        ("What is the net floor area of the room 'BALLROOM (999)' on '1.korrus', in m²?", "no room"),
        ("What software was used to create this model?", "measures"),
        ("How many doors are there?", "no storey"),
    ]:
        ans = ifc.answer(replace(base, question=text), ds)
        assert ans.answer is None and why in ans.reason, (text, ans.reason)
