"""Scoring that matches Stoa's rule, plus the reliability metrics we report.

Stoa's rule (dataset README): counts must be exact; areas, lengths and volumes
within 2%; text answers are free-form.

Design choices on top of the rule, all on the strict side:
  * An abstention (prediction None) is never correct. It lowers accuracy and
    coverage but can never be a wrong answer.
  * A prediction given in a different unit than the question asks for is wrong.
    We do not convert for the system: answering in ft2 when asked for m2 is a bug.
  * Text has no rule from Stoa, so it is never auto-marked wrong. It is "correct"
    only on a normalised exact match, otherwise "ungraded", with two soft metrics
    (token F1, and recall of the numbers in the reference) for development use.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

REL_TOL = 0.02  # areas, lengths, volumes: within 2% of the reference
_FLOAT_FUZZ = 1e-9  # guards the tolerance edge against binary rounding only

TOLERANCE_TYPES = {"area", "length", "volume"}
EXACT_TYPES = {"count"}

# Answer states (RELIABILITY.md section 1). A baseline that has no checks is "check".
VERIFIED, CHECK, DECLINED = "verified", "check", "declined"
STATES = (VERIFIED, CHECK, DECLINED)


@dataclass(frozen=True)
class Score:
    status: str  # correct | wrong | abstained | ungraded
    correct: bool | None  # None only for ungraded text
    reason: str = ""
    abs_err: float | None = None
    rel_err: float | None = None
    token_f1: float | None = None  # text only
    number_recall: float | None = None  # text only

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


# ----------------------------------------------------------------- parsing

_UNIT_ALIASES = {
    "m2": "m²", "m^2": "m²", "sqm": "m²", "sq m": "m²", "sq.m": "m²", "m²": "m²",
    "m3": "m³", "m^3": "m³", "cbm": "m³", "m³": "m³",
    "ft2": "ft²", "ft^2": "ft²", "sqft": "ft²", "sq ft": "ft²", "sf": "ft²", "ft²": "ft²",
    "ft3": "ft³", "ft^3": "ft³", "ft³": "ft³",
    "m": "m", "mm": "mm", "cm": "cm", "ft": "ft", "in": "in",
    "count": "count", "pcs": "count", "kpl": "count", "": "",
}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    key = unicodedata.normalize("NFKC", str(unit)).strip().lower()
    # NFKC turns "m²" into "m2", so both spellings meet in the alias table.
    return _UNIT_ALIASES.get(key, key)


_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def parse_number(value: Any) -> float | None:
    """Parse a prediction into a finite float, or None if it is not one number.

    Deliberately strict: "12.5" parses; "12,5", "1,234", "about 12" and "12 m2" do
    not. A comma is ambiguous between decimal and thousands separator, and a wrong
    guess here would be a silent 10x or 1000x error in a quantity.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        s = value.strip().replace("−", "-")  # unicode minus
        if _NUM_RE.match(s):
            x = float(s)
            return x if math.isfinite(x) else None
    return None


def _norm_text(s: Any) -> str:
    s = unicodedata.normalize("NFKC", str(s)).casefold()
    s = re.sub(r"[^\w\s.%-]", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


_TEXT_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?![\w])")


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _TEXT_NUM_RE.findall(unicodedata.normalize("NFKC", text)):
        try:
            out.append(float(m.replace(",", ".")))
        except ValueError:
            pass
    return out


def _token_f1(pred: str, gold: str) -> float:
    p, g = _norm_text(pred).split(), _norm_text(gold).split()
    if not p or not g:
        return float(p == g)
    remaining: dict[str, int] = defaultdict(int)
    for tok in g:
        remaining[tok] += 1
    common = 0
    for tok in p:
        if remaining[tok] > 0:
            remaining[tok] -= 1
            common += 1
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(g)
    return 2 * precision * recall / (precision + recall)


def _number_recall(pred: str, gold: str) -> float | None:
    """Share of the reference's numbers that appear in the prediction (within 2%)."""
    gold_nums = _numbers_in(gold)
    if not gold_nums:
        return None
    pred_nums = _numbers_in(pred)
    hit = 0
    for g in gold_nums:
        tol = REL_TOL * abs(g) + _FLOAT_FUZZ
        if any(abs(p - g) <= tol for p in pred_nums):
            hit += 1
    return hit / len(gold_nums)


# ----------------------------------------------------------------- one answer


def within_tolerance(pred: float, gold: float, rel_tol: float = REL_TOL) -> bool:
    return abs(pred - gold) <= rel_tol * abs(gold) + _FLOAT_FUZZ * max(1.0, abs(gold))


def score_answer(
    pred: Any,
    gold: Any,
    qtype: str,
    *,
    gold_unit: str | None = None,
    pred_unit: str | None = None,
) -> Score:
    """Score one prediction against the reference with Stoa's rule."""
    if pred is None or (isinstance(pred, str) and not pred.strip()):
        return Score("abstained", False, "no answer")

    gold_num = parse_number(gold)
    numeric = qtype in EXACT_TYPES or qtype in TOLERANCE_TYPES or (
        qtype not in ("text",) and gold_num is not None
    )

    if not numeric:
        pred_s, gold_s = str(pred), str(gold)
        f1 = _token_f1(pred_s, gold_s)
        recall = _number_recall(pred_s, gold_s)
        if _norm_text(pred_s) == _norm_text(gold_s):
            return Score("correct", True, "exact text match", token_f1=1.0, number_recall=recall)
        return Score(
            "ungraded", None, "free-form text: no automatic rule", token_f1=f1, number_recall=recall
        )

    if gold_num is None:
        return Score("ungraded", None, f"reference is not a number: {gold!r}")

    if pred_unit is not None and gold_unit is not None:
        if normalize_unit(pred_unit) != normalize_unit(gold_unit):
            return Score("wrong", False, f"unit {pred_unit!r} given, {gold_unit!r} asked")

    pred_num = parse_number(pred)
    if pred_num is None:
        return Score("wrong", False, f"not a single number: {pred!r}")

    abs_err = abs(pred_num - gold_num)
    rel_err = abs_err / abs(gold_num) if gold_num != 0 else (0.0 if abs_err == 0 else math.inf)

    # "other" follows its unit: a count is exact, any measured quantity gets 2%.
    exact = qtype in EXACT_TYPES or (
        qtype not in TOLERANCE_TYPES and normalize_unit(gold_unit) == "count"
    )
    if exact:
        ok = pred_num == gold_num and float(pred_num).is_integer()
        reason = "exact" if ok else "count must match exactly"
    else:
        ok = within_tolerance(pred_num, gold_num)
        reason = "within 2%" if ok else "outside 2%"
    return Score("correct" if ok else "wrong", ok, reason, abs_err=abs_err, rel_err=rel_err)


# ----------------------------------------------------------------- statistics


def clopper_pearson_upper(errors: int, n: int, confidence: float = 0.95) -> float | None:
    """One-sided upper confidence bound on an error rate (exact binomial).

    0 errors in n gives roughly 3/n at 95% (the "rule of three").
    """
    if n <= 0:
        return None
    if errors >= n:
        return 1.0
    alpha = 1.0 - confidence

    def cdf(p: float) -> float:  # P(X <= errors) for X ~ Binomial(n, p)
        if p <= 0.0:
            return 1.0
        if p >= 1.0:
            return 0.0
        log_p, log_q = math.log(p), math.log1p(-p)
        total = 0.0
        for k in range(errors + 1):
            log_term = (
                math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                + k * log_p + (n - k) * log_q
            )
            total += math.exp(log_term)
        return total

    lo, hi = errors / n, 1.0
    for _ in range(100):  # cdf falls as p rises; find p where cdf(p) = alpha
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ----------------------------------------------------------------- aggregates


def _aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    answered = [r for r in rows if r["status"] != "abstained"]
    correct = sum(1 for r in rows if r["status"] == "correct")
    wrong = sum(1 for r in rows if r["status"] == "wrong")
    ungraded = sum(1 for r in rows if r["status"] == "ungraded")
    graded = n - ungraded

    verified = [r for r in rows if r.get("state") == VERIFIED]
    verified_wrong = sum(1 for r in verified if r["status"] == "wrong")
    verified_graded = sum(1 for r in verified if r["status"] in ("correct", "wrong"))

    costs = [r["cost_eur"] for r in rows if r.get("cost_eur") is not None]
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    f1s = [r["token_f1"] for r in rows if r.get("token_f1") is not None]
    recalls = [r["number_recall"] for r in rows if r.get("number_recall") is not None]

    def ratio(a: float, b: float) -> float | None:
        return a / b if b else None

    return {
        "n": n,
        "answered": len(answered),
        "coverage": ratio(len(answered), n),
        "correct": correct,
        "wrong": wrong,
        "ungraded": ungraded,
        # Headline: correct over every gradable question; abstaining counts against it.
        "accuracy": ratio(correct, graded),
        "accuracy_when_answered": ratio(correct, correct + wrong),
        "verified": len(verified),
        "verified_share": ratio(len(verified), n),
        "verified_wrong": verified_wrong,
        "verified_error_rate": ratio(verified_wrong, verified_graded),
        "verified_error_upper95": clopper_pearson_upper(verified_wrong, verified_graded),
        "check": sum(1 for r in rows if r.get("state") == CHECK),
        "declined": sum(1 for r in rows if r.get("state") == DECLINED),
        "eur_total": sum(costs) if costs else None,
        # Cost is spread over all questions asked, including ones we declined.
        "eur_per_question": ratio(sum(costs), n) if costs else None,
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "text_token_f1": ratio(sum(f1s), len(f1s)),
        "text_number_recall": ratio(sum(recalls), len(recalls)),
    }


def summarize(rows: Iterable[dict], weights: dict[tuple[str, str], float] | None = None) -> dict:
    """Aggregate scored rows per (source, type), per source, per type and overall.

    Each row needs: source, type, status; optional: state, cost_eur, latency_ms,
    token_f1, number_recall.

    weights: optional {(source, type): share of the real question mix}. When given,
    "weighted" reports the accuracy and cost expected on that mix, which is the fair
    headline when the evaluated sample is not proportional to the dataset.
    """
    rows = list(rows)
    by_family: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_source: dict[str, list[dict]] = defaultdict(list)
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_family[(r["source"], r["type"])].append(r)
        by_source[r["source"]].append(r)
        by_type[r["type"]].append(r)

    families = {f"{s}/{t}": _aggregate(v) for (s, t), v in sorted(by_family.items())}
    out = {
        "overall": _aggregate(rows),
        "by_family": families,
        "by_source": {s: _aggregate(v) for s, v in sorted(by_source.items())},
        "by_type": {t: _aggregate(v) for t, v in sorted(by_type.items())},
    }

    if weights:
        acc = cost = cov = w_acc = w_all = 0.0
        missing = []
        for (s, t), w in weights.items():
            fam = families.get(f"{s}/{t}")
            if fam is None:
                missing.append(f"{s}/{t}")
                continue
            w_all += w
            cov += w * (fam["coverage"] or 0.0)
            cost += w * (fam["eur_per_question"] or 0.0)
            if fam["accuracy"] is not None:  # text-only families have no gradable accuracy
                acc += w * fam["accuracy"]
                w_acc += w
        out["weighted"] = {
            "accuracy": acc / w_acc if w_acc else None,
            "coverage": cov / w_all if w_all else None,
            "eur_per_question": cost / w_all if w_all else None,
            "weight_covered": w_all,
            "weight_graded": w_acc,
            "families_not_evaluated": missing,
        }
    return out
