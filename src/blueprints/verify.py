"""From an engine's score to "Verified": thresholds set on the calibration split.

An engine gives each answer a score from the checks it ran (two independent sources
agree, one source only, sources differ, ...). The score is a heuristic. What makes an
answer Verified is measured: for each family (engine x kind of quantity) we look at
the calibration answers at or above a score level and require that the 95% upper
confidence bound on their error rate is at most ALPHA.

Several score levels are tried per family, so the bound for each is computed at
confidence 1 - (1 - 0.95) / K for K levels (a Bonferroni correction, as in "Learn then
Test"): all K bounds hold together with 95% confidence, and choosing the most
permissive level that passes does not inflate the error guarantee.

A family with too few calibration answers, or with errors among its best-scored ones,
gets no threshold: nothing in it is ever Verified. That is the intended behaviour.

An engine can also mark an answer as one that is never Verified (meta["not_verifiable"]),
for a reason the dataset cannot measure: the answer agrees with the dataset, yet a person
could not use the number as it stands (a door that the drawing's file holds twice is counted
twice by the dataset too). Such answers are left out of the calibration and are never
Verified, whatever their score.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import config
from .answer import Answer
from .scoring import VERIFIED, clopper_pearson_upper

ALPHA = 0.01          # target: at most 1% of Verified answers wrong
BOUND_CONFIDENCE = 0.95
THRESHOLDS_FILE = config.SPLITS_DIR / "verified_thresholds.json"

_UNIT_KIND = {"count": "count", "m2": "area", "m3": "volume", "m": "length"}


def family_of(method: str, kind: str) -> str:
    return f"{method}/{kind}"


def calibrate(rows: Iterable[dict], alpha: float = ALPHA, confidence: float = BOUND_CONFIDENCE) -> dict:
    """Per family, the lowest score level whose error bound on calibration data is <= alpha."""
    by_family: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for r in rows:
        if (r.get("meta") or {}).get("not_verifiable"):
            continue  # never Verified, so not part of what the bound is about
        if r["status"] in ("correct", "wrong") and r.get("confidence") is not None:
            by_family[family_of(r["method"], r["type"])].append((r["confidence"], r["status"] == "wrong"))

    families = {}
    for family, answers in sorted(by_family.items()):
        levels = sorted({score for score, _ in answers}, reverse=True)
        level_confidence = 1 - (1 - confidence) / len(levels)  # all levels hold together
        chosen = None
        trace = []
        for level in levels:
            selected = [wrong for score, wrong in answers if score >= level]
            errors, n = sum(selected), len(selected)
            upper = clopper_pearson_upper(errors, n, level_confidence)
            passed = upper is not None and upper <= alpha
            trace.append({"score": level, "answers": n, "errors": errors, "upper_bound": upper, "passed": passed})
            if passed:
                chosen = trace[-1]  # levels go from strict to permissive: keep the last that passes
        families[family] = {
            "threshold": chosen["score"] if chosen else None,
            "answers_at_threshold": chosen["answers"] if chosen else 0,
            "errors_at_threshold": chosen["errors"] if chosen else None,
            "upper_bound": chosen["upper_bound"] if chosen else None,
            "calibration_answers": len(answers),
            "levels_tested": trace,
        }
    return {"alpha": alpha, "bound_confidence": confidence, "families": families}


def save_thresholds(result: dict, *, runs: list[str], dataset: str, path: Path | None = None) -> Path:
    path = path or THRESHOLDS_FILE
    data = {"calibrated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": dataset, "calibration_runs": runs, **result}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return path


def load_thresholds(path: Path | None = None) -> dict:
    path = path or THRESHOLDS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"families": {}}


def apply(answer: Answer, thresholds: dict) -> Answer:
    """Mark an answer Verified when its family has a threshold and its score reaches it."""
    if answer.answer is None or answer.confidence is None or answer.meta.get("not_verifiable"):
        return answer
    kind = _UNIT_KIND.get(answer.unit or "")
    family = thresholds.get("families", {}).get(family_of(answer.method, kind)) if kind else None
    if not family or family.get("threshold") is None or answer.confidence < family["threshold"]:
        return answer
    if not answer.evidence:  # a verified zero still shows what was looked for
        answer.evidence = [{"kind": "formula", "text": "nothing of this kind was found on the drawing"}]
    answer.state = VERIFIED
    answer.meta = {**answer.meta, "verified_because": {
        "family": family_of(answer.method, kind), "score": answer.confidence,
        "threshold": family["threshold"], "calibration_answers": family["answers_at_threshold"],
        "calibration_errors": family["errors_at_threshold"], "error_upper_bound": family["upper_bound"],
    }}
    return answer
