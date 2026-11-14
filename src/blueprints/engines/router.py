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


def is_weak(ans: Answer) -> bool:
    """A label-engine answer the fallback should replace: none, or one whose sources do not agree."""
    return ans.method == raster_labels.METHOD and (ans.answer is None or (ans.confidence or 0.0) < LABELS_STAND_FROM)


def make(thresholds: dict | None, fallback=None):
    """A router that applies `thresholds`; pass None for raw engine answers (used to calibrate).

    fallback: an answer function for questions no exact engine covers (a vision model).
    Its answers are never Verified: they pass through with the state the model path gave.
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
