"""Router: send each question to the cheapest engine that can answer it exactly.

    an IFC model, or a plan rendered from one   ->  IFC engine     (EUR 0)
    a floor plan that comes with a DXF          ->  DXF engine     (EUR 0)
    a scan, asked to count labelled rooms       ->  label engine   (EUR 0, local OCR)
    anything else                               ->  declined, or the fallback (a vision model)

On scans the label engine reads the room labels printed on the plan. Where its two
sources agree (score 0.9 and up, about 94% right on held-out plans) its answer stands;
below that it is no better than a vision model, so the fallback answers if there is
one. Symbol counts on scans and free-text questions have no exact engine yet. After an
engine answers, the verifier turns its score into Verified where the calibrated
threshold allows it.

Two readings of one scan: a strong label answer can be put next to a vision model's
answer to the same question (`confirm`). The OCR reads the room names printed on the
plan; the model marks the rooms it sees. When both give the same count, that is its own
kind of answer (labels+model:v1) with its own error rate measured on calibration
drawings; it is Verified only if that measurement allows it.

Measured on 800 calibration answers (scripts/calibrate_agreement.py): the labels alone
were right 94.5% of the time, the model 98.3%. They agreed on 762, of which 10 were
wrong (1.3%, bound 2.2%): better than either alone, not enough for Verified. Where they
differed (38), the model was right 34 times and the labels 4, so the model's count is
the one shown and the difference is flagged.

By label score: at 0.99 (the labels and the apartment's type line agree) the labels
alone were right 97.0% of the time against the model's 98.3%; at 0.9 they were right
93.0% against 98.2%. So the second reading is paid for only below 0.99, where it buys
five points; at 0.99 the free answer stands.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from .. import verify
from ..answer import Answer
from ..data import Dataset, Question
from . import dxf, ifc, raster_labels

METHOD = "router:v1"
RASTER = (".png", ".jpg", ".jpeg")
# The label engine's answer stands from this score; measured on held-out plans:
# 94% right at 0.9 and up, 79% at the level below (about what a vision model gets).
LABELS_STAND_FROM = 0.9


def route(q: Question, dataset: Dataset) -> str | None:
    if ifc.ifc_file_for(q, dataset):
        return "ifc"
    if any(PurePosixPath(f).suffix.lower() == ".dxf" for f in dataset.formats(q.path)):
        return "dxf"
    if q.path.lower().endswith(RASTER) and raster_labels.room_kind_in(q.question):
        return "labels"
    return None


# A label answer below this score is read a second time by the model when there is one.
CONFIRM_BELOW = 0.99
# The printed labels and a vision model's marked items give the same count.
AGREED = "labels+model:v1"
AGREED_SCORE = 0.95   # one level only: the family is Verified as a whole or not at all
DIFFER_SCORE = 0.5    # the two readings differ: never Verified
# Whose count is shown when the two differ: the reading that was right more often on
# calibration drawings (the model, 34 times against 4).
ON_DIFFERENCE = "model"


def confirmed(labels: Answer, other: Answer) -> Answer:
    """A strong label reading together with a vision model's reading of the same question."""
    cost = (labels.cost_usd or 0.0) + (other.cost_usd or 0.0)
    latency = (labels.latency_ms or 0.0) + (other.latency_ms or 0.0)
    if other.answer is None:  # the model gave nothing: the labels' answer stands as it was
        labels.cost_usd, labels.latency_ms = cost, latency
        labels.meta = {**labels.meta, "model_said": None}
        return labels
    try:
        agree = float(other.answer) == float(labels.answer)
    except (TypeError, ValueError):
        agree = False
    seen = []  # what the model marked, drawn but not counted
    for item in other.evidence:
        if item.get("space") != "image_px":
            continue
        if item.get("kind") == "box" and not item.get("role"):
            seen.append({**item, "role": "marked by the vision model"})
        elif item.get("kind") == "point":
            x, y = item["x"], item["y"]
            seen.append({"kind": "box", "space": "image_px", "bbox": [x - 14, y - 14, x + 14, y + 14],
                         "label": item.get("label", ""), "role": "marked by the vision model"})
    checks = {**(labels.meta.get("checks") or {}),
              "rooms marked by the vision model": f"{other.answer}: {'the same count' if agree else 'a different count'}"}
    meta = {**labels.meta, "checks": checks, "labels_said": labels.answer, "labels_score": labels.confidence,
            "model_said": other.answer, "model_method": other.method}
    if agree:
        return Answer(answer=labels.answer, unit=labels.unit, confidence=AGREED_SCORE, method=AGREED,
                      evidence=labels.evidence + seen, cost_usd=cost, latency_ms=latency, meta=meta,
                      explanation=f"Two readings give the same count. {labels.explanation}")
    explanation = (f"Two readings differ: the printed labels give {labels.answer}, the vision model "
                   f"marked {other.answer}. ")
    if ON_DIFFERENCE == "model":  # the model's items are the counted ones; the labels are drawn beside them
        read = [{**item, "role": item.get("role") or "label read by OCR"} for item in labels.evidence
                if item.get("kind") == "box" and item.get("space") == "image_px"]
        counted = [item for item in other.evidence if item.get("kind") != "formula"]
        evidence = (counted or [{"kind": "formula", "text": "the model marked nothing of this kind"}]) + read
        return Answer(answer=other.answer, unit=labels.unit, confidence=DIFFER_SCORE, method=AGREED, evidence=evidence,
                      cost_usd=cost, latency_ms=latency, meta=meta, explanation=explanation + other.explanation)
    return Answer(answer=labels.answer, unit=labels.unit, confidence=DIFFER_SCORE, method=AGREED,
                  evidence=labels.evidence + seen, cost_usd=cost, latency_ms=latency, meta=meta,
                  explanation=explanation + labels.explanation)


def wants_second_reading(ans: Answer) -> bool:
    """A label answer that stands, but is measurably weaker than the model on its own."""
    return (ans.method == raster_labels.METHOD and ans.answer is not None and not is_weak(ans)
            and (ans.confidence or 0.0) < CONFIRM_BELOW)


def is_weak(ans: Answer) -> bool:
    """A label-engine answer the fallback should replace: none, or one whose sources do not agree."""
    return ans.method == raster_labels.METHOD and (ans.answer is None or (ans.confidence or 0.0) < LABELS_STAND_FROM)


def make(thresholds: dict | None, fallback=None, confirm=None):
    """A router that applies `thresholds`; pass None for raw engine answers (used to calibrate).

    fallback: an answer function for questions no exact engine covers (a vision model).
    Its answers are never Verified: they pass through with the state the model path gave.
    confirm: an answer function (a vision model) asked as well when the labels on a scan
    give a strong answer; see `confirmed`.
    """

    def answer(q: Question, dataset: Dataset) -> Answer:
        engine = route(q, dataset)
        if engine is None:
            if fallback is not None:
                return fallback(q, dataset)
            return Answer.decline("no exact engine for this kind of drawing yet", method=METHOD, cost_usd=0.0)
        if engine == "labels":
            ans = raster_labels.answer(q, dataset)
            if is_weak(ans) and fallback is not None:
                other = fallback(q, dataset)
                if other.answer is not None or ans.answer is None:
                    # Keep what the labels said next to the model's answer: agreement is evidence.
                    other.meta = {**other.meta, "labels_said": ans.answer, "labels_score": ans.confidence}
                    return other
            elif confirm is not None and wants_second_reading(ans):
                ans = confirmed(ans, confirm(q, dataset))
        else:
            ans = (ifc if engine == "ifc" else dxf).answer(q, dataset)
        return verify.apply(ans, thresholds) if thresholds else ans

    return answer


def replay(*predictions_files):
    """A fallback that replays the logged answers of earlier runs instead of calling the model.

    Lets "engines first, vision model for the rest" be measured on questions the model
    has already answered, at the cost that was really paid for those answers. With several
    runs, the first one that holds a question answers it (for example: the run that marks
    items on scans first, then the plain image-only run for everything else).
    """
    import json

    rows = {}
    for predictions_file in reversed(predictions_files):  # so that the first run listed wins
        with open(predictions_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    rows[row["qid"]] = row

    def answer(q: Question, dataset: Dataset) -> Answer:
        row = rows.get(q.qid)
        if row is None:
            return Answer.decline("not answered in the replayed model run", method=METHOD, cost_usd=0.0)
        common = dict(method=row["method"], cost_usd=row.get("cost_usd"), latency_ms=row.get("latency_ms"),
                      confidence=row.get("confidence"), meta={"replayed": True})
        if row.get("pred") is None:
            return Answer.decline(row.get("decline_reason") or "the model gave no answer", **common)
        return Answer(answer=row["pred"], unit=row.get("pred_unit"), evidence=row.get("evidence") or [],
                      explanation=row.get("explanation", ""), **common)

    return answer
