"""Router: send each question to the cheapest engine that can answer it exactly.

    an IFC model, or a plan rendered from one   ->  IFC engine   (EUR 0)
    a floor plan that comes with a DXF          ->  DXF engine   (EUR 0)
    anything else                               ->  declined for now

Raster-only plans (CubiCasa) and free-text questions have no engine yet, so they are
declined with the reason rather than guessed. After the engine answers, the verifier
turns its score into Verified where the calibrated threshold allows it.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from .. import verify
from ..answer import Answer
from ..data import Dataset, Question
from . import dxf, ifc

METHOD = "router:v1"


def route(q: Question, dataset: Dataset) -> str | None:
    if ifc.ifc_file_for(q, dataset):
        return "ifc"
    if any(PurePosixPath(f).suffix.lower() == ".dxf" for f in dataset.formats(q.path)):
        return "dxf"
    return None


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
        ans = (ifc if engine == "ifc" else dxf).answer(q, dataset)
        return verify.apply(ans, thresholds) if thresholds else ans

    return answer


def replay(predictions_file):
    """A fallback that replays the logged answers of an earlier run instead of calling the model.

    Lets "engines first, vision model for the rest" be measured on questions the model
    has already answered, at the cost that was really paid for those answers.
    """
    import json

    rows = {}
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
        return Answer(answer=row["pred"], explanation=row.get("explanation", ""), **common)

    return answer
