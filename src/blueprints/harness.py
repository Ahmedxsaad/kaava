"""Evaluation harness: run any engine over a fixed question list, score it, log it.

An engine is a function `answer_fn(question, dataset) -> Answer`. Every run writes

    results/runs/<run_id>/predictions.jsonl   one row per question (no question text,
                                              no free-text reference: join on qid)
    results/runs/<run_id>/summary.json        config, provenance and all metrics
    results/leaderboard.csv                   one row per run x (overall | family)

so every number in the pitch can be traced to a commit and a method.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import config
from .answer import Answer
from .data import Dataset, Question
from .scoring import score_answer, summarize


class FatalEngineError(RuntimeError):
    """A failure that would repeat on every question (bad key, unknown model)."""


# ----------------------------------------------------------------- provenance


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_sha() -> str:
    """Hash of the package source, so a run is traceable even before a commit exists."""
    h = hashlib.sha1()
    src = Path(__file__).resolve().parent
    for path in sorted(src.rglob("*.py")):
        h.update(path.relative_to(src).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:10]


def provenance() -> dict:
    commit = _git("rev-parse", "--short", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "commit": commit or "uncommitted",
        "dirty": bool(status) if commit else None,
        "code_sha": code_sha(),
    }


def file_sha(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:10]


# ----------------------------------------------------------------- one row


def make_row(q: Question, split: str, ans: Answer) -> dict:
    """Score one answer and flatten it into a log row."""
    score = score_answer(ans.answer, q.answer, q.type, gold_unit=q.unit, pred_unit=ans.unit)
    row = {
        "qid": q.qid,
        "path": q.path,
        "source": q.source,
        "type": q.type,
        "unit": q.unit,
        "split": split,
        "pred": ans.answer,
        "pred_unit": ans.unit,
        "state": ans.state,
        "confidence": ans.confidence,
        "status": score.status,
        **{k: v for k, v in score.to_dict().items() if k not in ("status", "correct")},
        "method": ans.method,
        "explanation": ans.explanation,
        "decline_reason": ans.reason if ans.answer is None else "",
        "evidence": ans.evidence,
        "cost_usd": ans.cost_usd,
        "cost_eur": config.usd_to_eur(ans.cost_usd),
        "latency_ms": ans.latency_ms,
        "meta": ans.meta,
        "error": ans.error,
    }
    # Numeric references are plain facts and handy for error review. Free-text
    # references are dataset content under each drawing's licence: join on qid instead.
    if isinstance(q.answer, (int, float)) and not isinstance(q.answer, bool):
        row["gold"] = q.answer
    return row


def _read_rows(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    rows[r["qid"]] = r  # a retried question overrides its earlier row
    return rows


# ----------------------------------------------------------------- run


def new_run_id(method: str) -> str:
    """Time, method and a random tag: two runs started in the same second stay apart."""
    slug = "".join(c if c.isalnum() or c in "-._" else "-" for c in method).strip("-")[:60]
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{slug}-{uuid.uuid4().hex[:4]}"


def run_eval(
    *,
    method: str,
    answer_fn: Callable[[Question, Dataset], Answer],
    dataset: Dataset,
    questions: Sequence[Question],
    split_of: Callable[[Question], str],
    run_dir: Path,
    workers: int = 4,
    max_cost_eur: float | None = None,
    max_consecutive_errors: int = 3,
    progress: Callable[[str], None] = print,
) -> dict:
    """Answer and score `questions`, appending each row to predictions.jsonl as it lands.

    Re-running with the same run_dir resumes: finished rows are kept, rows that
    failed with an infrastructure error are retried. Returns {"rows", "complete", "stopped"}.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / "predictions.jsonl"
    done = {qid: r for qid, r in _read_rows(pred_path).items() if not r.get("error")}
    todo = [q for q in questions if q.qid not in done]
    if done:
        progress(f"resuming: {len(done)} rows already done, {len(todo)} to go")

    lock = threading.Lock()
    spent_eur = sum(r.get("cost_eur") or 0.0 for r in done.values())
    state = {"spent": spent_eur, "consecutive_errors": 0, "stop": "", "landed": 0}
    rows: dict[str, dict] = dict(done)

    with open(pred_path, "a", encoding="utf-8") as out:

        def work(q: Question) -> None:
            with lock:
                if state["stop"]:
                    return
            t0 = time.perf_counter()
            try:
                ans = answer_fn(q, dataset)
            except FatalEngineError as exc:
                with lock:
                    state["stop"] = state["stop"] or f"fatal: {exc}"
                return
            except Exception as exc:  # an engine bug must not sink the whole run
                ans = Answer.failed(f"{type(exc).__name__}: {exc}", method=method)
            if ans.latency_ms is None:
                ans.latency_ms = (time.perf_counter() - t0) * 1000
            if not ans.method:
                ans.method = method
            row = make_row(q, split_of(q), ans)

            # Write and account in the worker, under the lock, so the budget and the
            # error streak are current before the next question starts. With several
            # workers the calls already in flight still finish: the budget can be
            # overshot by at most (workers - 1) answers.
            with lock:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                rows[row["qid"]] = row
                state["landed"] += 1
                state["spent"] += row.get("cost_eur") or 0.0
                if row.get("error"):
                    state["consecutive_errors"] += 1
                    if state["consecutive_errors"] >= max_consecutive_errors:
                        state["stop"] = state["stop"] or (
                            f"{max_consecutive_errors} engine errors in a row "
                            f"(last: {row['error']})"
                        )
                else:
                    state["consecutive_errors"] = 0
                if max_cost_eur is not None and state["spent"] >= max_cost_eur:
                    state["stop"] = state["stop"] or (
                        f"budget reached: EUR {state['spent']:.2f} of {max_cost_eur:.2f}"
                    )
                if state["landed"] % 10 == 0 or state["landed"] == len(todo):
                    progress(f"  {state['landed']}/{len(todo)} answered, "
                             f"EUR {state['spent']:.3f} spent")

        with ThreadPoolExecutor(workers) as pool:
            for fut in as_completed([pool.submit(work, q) for q in todo]):
                fut.result()  # surfaces a bug in the harness itself

    ordered = [rows[q.qid] for q in questions if q.qid in rows]
    clean = [r for r in ordered if not r.get("error")]
    return {
        "rows": ordered,
        "complete": len(clean) == len(questions),
        "stopped": state["stop"],
        "spent_eur": state["spent"],
    }


# ----------------------------------------------------------------- log

LEADERBOARD_COLUMNS = [
    "run_id", "timestamp_utc", "commit", "dirty", "code_sha", "method", "dataset", "split",
    "sample", "complete", "scope", "source", "type",
    "n", "answered", "coverage", "correct", "wrong", "ungraded",
    "accuracy", "accuracy_when_answered",
    "verified", "verified_share", "verified_wrong", "verified_error_rate", "verified_error_upper95",
    "check", "declined",
    "eur_per_question", "eur_total", "latency_p50_ms", "latency_p95_ms",
    "text_token_f1", "text_number_recall", "notes",
]


def _fmt(v: object) -> object:
    if isinstance(v, float):
        return f"{v:.6g}"
    return "" if v is None else v


def finalize_run(
    *,
    run_id: str,
    run_dir: Path,
    method: str,
    result: dict,
    dataset: Dataset,
    split_name: str,
    sample_name: str,
    weights: dict[tuple[str, str], float] | None = None,
    run_config: dict | None = None,
    notes: str = "",
    leaderboard: Path | None = None,
    log: bool = True,
) -> dict:
    """Write summary.json and, unless log is False, append this run to the leaderboard."""
    rows = result["rows"]
    metrics = summarize(rows, weights=weights)
    prov = provenance()
    summary = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **prov,
        "method": method,
        "dataset": dataset.root.name,
        "questions_sha": file_sha(dataset.root / "questions.jsonl"),
        "split": split_name,
        "sample": sample_name,
        "complete": result["complete"],
        "stopped": result["stopped"],
        "errors": sum(1 for r in rows if r.get("error")),
        "fx": config.FX_NOTE,
        "config": run_config or {},
        "notes": notes,
        "metrics": metrics,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if not log:  # an interrupted run gets its summary, but no leaderboard line yet
        return summary
    leaderboard = leaderboard or (config.RESULTS_DIR / "leaderboard.csv")
    leaderboard.parent.mkdir(parents=True, exist_ok=True)
    is_new = not leaderboard.is_file()
    base = {
        "run_id": run_id, "timestamp_utc": summary["timestamp_utc"], "commit": prov["commit"],
        "dirty": prov["dirty"], "code_sha": prov["code_sha"], "method": method,
        "dataset": summary["dataset"], "split": split_name, "sample": sample_name,
        "complete": result["complete"], "notes": notes,
    }
    lines = [{**base, "scope": "overall", "source": "ALL", "type": "ALL", **metrics["overall"]}]
    if "weighted" in metrics:
        w = metrics["weighted"]
        lines.append({**base, "scope": "weighted_to_dataset_mix", "source": "ALL", "type": "ALL",
                      "accuracy": w["accuracy"], "coverage": w["coverage"],
                      "eur_per_question": w["eur_per_question"]})
    for family, m in metrics["by_family"].items():
        source, qtype = family.split("/", 1)
        lines.append({**base, "scope": "family", "source": source, "type": qtype, **m})
    with open(leaderboard, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEADERBOARD_COLUMNS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for line in lines:
            writer.writerow({k: _fmt(line.get(k)) for k in LEADERBOARD_COLUMNS})
    return summary


def format_table(metrics: dict) -> str:
    """A plain-text results table: per family, then overall."""
    def pct(x: float | None) -> str:
        return "   -  " if x is None else f"{100 * x:5.1f}%"

    def eur(x: float | None) -> str:
        return "      -" if x is None else f"{x:7.4f}"

    verified_any = metrics["overall"]["verified"] > 0
    head = f"{'source/type':<28}{'n':>5}{'answ':>6}{'acc':>8}{'acc|answ':>10}{'cover':>8}{'EUR/q':>9}{'p50 s':>7}"
    if verified_any:
        head += f"{'Verified':>10}{'wrong':>7}"
    out = [head, "-" * len(head)]
    for name, m in list(metrics["by_family"].items()) + [("OVERALL", metrics["overall"])]:
        if name == "OVERALL":
            out.append("-" * len(head))
        p50 = "" if m["latency_p50_ms"] is None else f"{m['latency_p50_ms'] / 1000:6.1f}"
        # Free-text answers have no automatic rule: say how many are left out of "acc".
        ungraded = f"   ({m['ungraded']} free-text answers not graded)" if m["ungraded"] else ""
        verified = f"{m['verified']:>10}{m['verified_wrong']:>7}" if verified_any else ""
        out.append(
            f"{name:<28}{m['n']:>5}{m['answered']:>6}{pct(m['accuracy']):>8}"
            f"{pct(m['accuracy_when_answered']):>10}{pct(m['coverage']):>8}"
            f"{eur(m['eur_per_question']):>9}{p50:>7}{verified}{ungraded}"
        )
    if "weighted" in metrics:
        w = metrics["weighted"]
        covered = w["weight_covered"]
        if covered >= 0.5:  # a re-weighted headline only means something over most of the mix
            out.append(
                f"{'weighted to dataset mix':<28}{'':>5}{'':>6}{pct(w['accuracy']):>8}{'':>10}"
                f"{pct(w['coverage']):>8}{eur(w['eur_per_question']):>9}"
                f"   (families evaluated = {100 * covered:.0f}% of all questions)"
            )
        else:
            out.append(f"(these families are {100 * covered:.1f}% of all questions in the full dataset)")
    return "\n".join(out)
