"""Command line: ./bp <command>

    info       what is in the dataset
    split      create (or extend) the dev / calibration / test split
    sample     save a fixed stratified question sample from one split
    baseline   run the image-only vision-model baseline on a sample
    run        run an engine (ifc, dxf) over a split or a sample and log it
    floor      the no-drawing guessing floor any real method must clear
    score      score any engine's predictions file and log the run
    errors     list the answers of a logged run, with the question text
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .answer import Answer
from .data import Dataset
from .harness import (FatalEngineError, finalize_run, format_table, make_row, new_run_id,
                      run_eval)
from .sample import load_sample, save_sample, stratified_sample
from .scoring import CHECK, DECLINED, STATES, summarize
from .split import SPLITS, Split, SplitLocked, describe, make_split


def _mix_weights(dataset: Dataset) -> tuple[dict, str]:
    """The question mix results are re-weighted to: the full dataset's."""
    if dataset.root.name.endswith("preview"):
        total = sum(config.FULL_SET_MIX.values())
        return ({k: v / total for k, v in config.FULL_SET_MIX.items()},
                "full-set mix from 02-dataset/DATASET.md")
    return dataset.family_weights(), f"mix of {dataset.root.name}"


def _rel(path: Path) -> str:
    """Path as shown to the user: relative to the repo when it is inside it."""
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def _log_test_access(what: str) -> None:
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.RESULTS_DIR / "test_access.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\t{what}\n")


def _check_split(questions, split: Split, expected: str, final: bool, what: str) -> None:
    """Refuse to evaluate on anything but the split the caller named."""
    found = Counter(split.of(q) for q in questions)
    if set(found) - {expected}:
        raise SystemExit(f"{what}: questions are not all in '{expected}': {dict(found)}. "
                         "Has the split file changed since the sample was made?")
    if expected == "test":
        if not final:
            raise SystemExit("This is the test split: final numbers only. Re-run with --final.")
        _log_test_access(what)


# ----------------------------------------------------------------- commands


def cmd_info(args: argparse.Namespace) -> int:
    ds = Dataset(args.dataset)
    qs = ds.questions
    print(f"dataset   {ds.root}")
    print(f"files     {len(ds.files)}")
    print(f"questions {len(qs)} on {len({q.drawing for q in qs})} drawings in "
          f"{len({q.group for q in qs})} groups")
    print(f"\n{'source':<18}{'type':<8}{'questions':>10}{'drawings':>10}{'templates':>11}")
    for family, n in sorted(Counter(q.family for q in qs).items()):
        sel = [q for q in qs if q.family == family]
        print(f"{family[0]:<18}{family[1]:<8}{n:>10}{len({q.drawing for q in sel}):>10}"
              f"{len({q.template for q in sel}):>11}")
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    ds = Dataset(args.dataset)
    split, report = make_split(ds)
    print(f"{_rel(config.SPLIT_FILE)}: "
          f"{report['kept']} groups kept, {report['added']} added\n")
    print(describe(split, ds.questions))
    for link in report["links"]:
        print(f"linked (same IFC project): {', '.join(link)}")
    for warning in report["warnings"]:
        print(f"note: {warning}")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    from .render import can_render

    ds = Dataset(args.dataset)
    split = Split.load()
    try:
        pool = split.select(ds.questions, args.split, allow_test=args.final)
    except SplitLocked as exc:
        raise SystemExit(str(exc))
    if args.split == "test":
        _log_test_access(f"sample {args.name}")
    total = len(pool)
    if args.need_image:
        pool = [q for q in pool if can_render(q.path)]
    name = args.name or f"{args.split}_{args.n}"
    path = config.SPLITS_DIR / f"{name}.json"
    if path.exists() and not args.force:
        raise SystemExit(f"{path} exists. A sample is fixed so runs stay comparable; "
                         "pick another --name, or pass --force to replace it.")
    picked = stratified_sample(pool, args.n, seed=name)
    data = save_sample(path, name, args.split, name, picked)
    data_note = f" ({total - len(pool)} of {total} {args.split} questions have no image and were left out)" if args.need_image else ""
    print(f"{_rel(path)}: {data['n']} questions on "
          f"{data['drawings']} drawings{data_note}")
    for family, n in data["families"].items():
        print(f"  {family:<28}{n:>4}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from . import baseline
    from .vlm import make_vlm

    load_dotenv(config.REPO_ROOT / ".env")
    ds = Dataset(args.dataset)
    split = Split.load()
    sample_path = Path(args.sample)
    sample, questions = load_sample(sample_path, ds)
    sample_name = sample["name"]
    _check_split(questions, split, sample["split"], args.final, f"baseline on {sample_name}")
    limited = bool(args.limit and args.limit < len(questions))
    if limited:
        step = len(questions) / args.limit
        questions = [questions[int(i * step)] for i in range(args.limit)]

    try:
        vlm = make_vlm(args.provider, args.model, args.effort)
    except FatalEngineError as exc:
        raise SystemExit(f"cannot start: {exc}")
    method = baseline.method_name(vlm)

    est = baseline.estimate(questions, ds, vlm)
    price = config.PRICES_USD_PER_MTOK.get(vlm.model)
    print(f"method    {method}")
    print(f"sample    {sample_name}: {len(questions)} questions, {est['calls']} model calls"
          + (f", {est['no_image']} without an image (declined at no cost)" if est["no_image"] else ""))
    print(f"input     ~{est['input_tokens']:,} tokens ({est['image_tokens']:,} of them image)")
    if price:
        usd_in = est["input_tokens"] * price["input"] / 1e6
        low, high = (est["calls"] * t * price["output"] / 1e6 for t in (400, 3000))
        print(f"estimate  EUR {config.usd_to_eur(usd_in + low):.2f} to "
              f"{config.usd_to_eur(usd_in + high):.2f} "
              f"(input EUR {config.usd_to_eur(usd_in):.2f} measured from the files; output assumed "
              f"400 to 3,000 tokens per answer including thinking)")
    if config.PRICE_NOTES.get(vlm.model):
        print(f"price     {config.PRICE_NOTES[vlm.model]}")
    if args.dry_run:
        print("dry run: nothing sent.")
        return 0

    offline = args.provider == "mock"
    run_id = args.resume or new_run_id(method)
    runs_root = Path(tempfile.mkdtemp(prefix="bp-mock-")) if offline else config.RESULTS_DIR / "runs"
    run_dir = runs_root / run_id
    if args.resume and not run_dir.is_dir():
        raise SystemExit(f"no such run to resume: {run_dir}")
    print(f"run       {run_id}  (budget EUR {args.max_cost_eur:.2f}, {args.workers} workers)")

    result = run_eval(
        method=method, answer_fn=baseline.make_engine(vlm), dataset=ds, questions=questions,
        split_of=split.of, run_dir=run_dir, workers=args.workers, max_cost_eur=args.max_cost_eur,
    )
    clean = [r for r in result["rows"] if not r.get("error")]
    if not clean:
        print(f"\nstopped before any answer: {result['stopped']}")
        if not args.resume:
            shutil.rmtree(run_dir, ignore_errors=True)
        return 2

    weights, mix_note = _mix_weights(ds)
    leaderboard = (runs_root / "leaderboard.csv") if offline else None
    summary = finalize_run(
        run_id=run_id, run_dir=run_dir, method=method, result=result, dataset=ds,
        split_name=sample["split"], sample_name=sample_name, weights=weights,
        run_config={"provider": args.provider, "model": vlm.model, "effort": vlm.effort,
                    "image_resolution": getattr(vlm, "resolution", f"{vlm.max_edge}px"),
                    "price_usd_per_mtok": config.PRICES_USD_PER_MTOK.get(vlm.model),
                    "price_note": config.PRICE_NOTES.get(vlm.model, ""),
                    "workers": args.workers, "mix": mix_note,
                    "system_prompt": baseline.SYSTEM, "schema": baseline.SCHEMA},
        notes=args.notes, leaderboard=leaderboard, log=result["complete"] and not limited,
    )
    print()
    print(format_table(summary["metrics"]))
    print(f"\n(weighted row: {mix_note})")
    if offline:
        print(f"\nmock run, not logged to results/. Output: {run_dir}")
    elif limited and result["complete"]:
        print(f"\nsmoke test of {len(questions)} questions: kept in results/runs/{run_id}/, not on the "
              f"leaderboard.\nRun the rest of the sample with: ./bp baseline --resume {run_id} "
              f"--provider {args.provider}" + (f" --model {args.model}" if args.model else "")
              + (f" --effort {args.effort}" if args.effort != "high" else ""))
    elif result["complete"]:
        print(f"\nlogged: results/runs/{run_id}/ and results/leaderboard.csv")
    else:
        print(f"\nINCOMPLETE ({len(clean)}/{len(questions)}): {result['stopped']}")
        print(f"Not added to the leaderboard yet. Continue with: ./bp baseline --resume {run_id}"
              + (f" --sample {args.sample}" if args.sample != str(DEFAULT_SAMPLE) else ""))
        return 3
    return 0


def cmd_floor(args: argparse.Namespace) -> int:
    """The no-drawing floor: most common answer to the same question on other dev drawings."""
    from . import prior

    ds = Dataset(args.dataset)
    split = Split.load()
    dev = split.select(ds.questions, "dev")
    if args.sample:
        sample, questions = load_sample(Path(args.sample), ds)
        sample_name = sample["name"]
        if sample["split"] != "dev":
            raise SystemExit("the floor is built from dev answers; evaluate it on a dev sample")
    else:
        questions, sample_name = dev, "all-dev"
    _check_split(questions, split, "dev", False, "floor")

    run_id = new_run_id(prior.METHOD)
    run_dir = config.RESULTS_DIR / "runs" / run_id
    result = run_eval(method=prior.METHOD, answer_fn=prior.make_engine(dev), dataset=ds,
                      questions=questions, split_of=split.of, run_dir=run_dir, workers=1,
                      progress=lambda _msg: None)
    weights, mix_note = _mix_weights(ds)
    summary = finalize_run(
        run_id=run_id, run_dir=run_dir, method=prior.METHOD, result=result, dataset=ds,
        split_name="dev", sample_name=sample_name, weights=weights,
        run_config={"mix": mix_note, "prior_from": "dev answers, leave-one-group-out"},
        notes=args.notes or "never looks at the drawing; guessing floor",
    )
    print(f"method    {prior.METHOD}\nsample    {sample_name}: {len(questions)} questions\n")
    print(format_table(summary["metrics"]))
    print(f"\n(weighted row: {mix_note})")
    print(f"\nlogged: results/runs/{run_id}/ and results/leaderboard.csv")
    return 0


def _engines() -> dict:
    """name -> (method label, answer function, sources it is built for)."""
    from .engines import dxf, ifc

    return {"ifc": (ifc.METHOD, ifc.answer, ("ifc_models",)),
            "dxf": (dxf.METHOD, dxf.answer, ("swiss_dwellings",))}


def cmd_run(args: argparse.Namespace) -> int:
    """Run an engine over a sample, or over every question of a split for its sources."""
    ds = Dataset(args.dataset)
    split = Split.load()
    method, answer_fn, default_sources = _engines()[args.engine]
    if args.sample:
        sample, questions = load_sample(Path(args.sample), ds)
        sample_name, split_name = sample["name"], sample["split"]
    else:
        sources = tuple(args.source) if args.source else default_sources
        try:
            pool = split.select(ds.questions, args.split, allow_test=args.final)
        except SplitLocked as exc:
            raise SystemExit(str(exc))
        questions = [q for q in pool if q.source in sources]
        sample_name, split_name = f"all-{args.split}:{'+'.join(sources)}", args.split
    if not questions:
        raise SystemExit("no questions selected")
    _check_split(questions, split, split_name, args.final, f"run {method} on {sample_name}")

    run_id = new_run_id(method)
    run_dir = config.RESULTS_DIR / "runs" / run_id
    print(f"method    {method}\nsample    {sample_name}: {len(questions)} questions")
    result = run_eval(method=method, answer_fn=answer_fn, dataset=ds, questions=questions,
                      split_of=split.of, run_dir=run_dir, workers=args.workers,
                      max_consecutive_errors=25, progress=lambda _msg: None)
    weights, mix_note = _mix_weights(ds)
    summary = finalize_run(
        run_id=run_id, run_dir=run_dir, method=method, result=result, dataset=ds,
        split_name=split_name, sample_name=sample_name, weights=weights,
        run_config={"engine": args.engine, "mix": mix_note}, notes=args.notes,
        log=result["complete"],
    )
    print()
    print(format_table(summary["metrics"]))
    errors = sum(1 for r in result["rows"] if r.get("error"))
    if errors or not result["complete"]:
        print(f"\nINCOMPLETE: {errors} engine errors. {result['stopped']}  Not added to the leaderboard.")
        print(f"See: ./bp errors {run_id} --status abstained")
        return 3
    print(f"\nlogged: results/runs/{run_id}/ and results/leaderboard.csv")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Score a predictions file from any engine.

    One JSON object per line: {"qid": ..., "answer": ..., and optionally "unit",
    "state", "confidence", "cost_usd", "latency_ms", "evidence", "explanation"}.
    Questions of the sample that are missing from the file count as declined.
    """
    ds = Dataset(args.dataset)
    split = Split.load()
    preds: dict[str, dict] = {}
    with open(args.predictions, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                preds[rec["qid"]] = rec
    unknown = [qid for qid in preds if qid not in ds.by_qid]
    if unknown:
        raise SystemExit(f"{len(unknown)} qids are not in this dataset (e.g. {unknown[0]})")

    if args.sample:
        sample, questions = load_sample(Path(args.sample), ds)
        sample_name, split_name = sample["name"], sample["split"]
    else:
        questions = [ds.by_qid[qid] for qid in preds]
        sample_name, split_name = f"file:{Path(args.predictions).name}", args.split
    _check_split(questions, split, split_name, args.final, f"score {args.method}")

    rows = []
    for q in questions:
        rec = preds.get(q.qid)
        if rec is None or rec.get("answer") is None:
            reason = (rec or {}).get("reason") or "no prediction"
            ans = Answer.decline(reason, method=args.method, cost_usd=(rec or {}).get("cost_usd", 0.0))
        else:
            state = rec.get("state", CHECK)
            if state not in STATES or state == DECLINED:
                raise SystemExit(f"{q.qid}: state {state!r} does not fit an answered question")
            ans = Answer(answer=rec["answer"], unit=rec.get("unit"), state=state,
                         confidence=rec.get("confidence"), method=args.method,
                         evidence=rec.get("evidence") or [], explanation=rec.get("explanation", ""),
                         cost_usd=rec.get("cost_usd", 0.0), latency_ms=rec.get("latency_ms"))
        rows.append(make_row(q, split.of(q), ans))

    run_id = new_run_id(args.method)
    run_dir = config.RESULTS_DIR / "runs" / run_id
    run_dir.mkdir(parents=True)
    with open(run_dir / "predictions.jsonl", "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    weights, mix_note = _mix_weights(ds)
    summary = finalize_run(
        run_id=run_id, run_dir=run_dir, method=args.method,
        result={"rows": rows, "complete": True, "stopped": ""}, dataset=ds,
        split_name=split_name, sample_name=sample_name, weights=weights,
        run_config={"predictions": str(args.predictions), "mix": mix_note}, notes=args.notes,
    )
    print(format_table(summary["metrics"]))
    print(f"\nlogged: results/runs/{run_id}/ and results/leaderboard.csv")
    return 0


def cmd_errors(args: argparse.Namespace) -> int:
    ds = Dataset(args.dataset)
    path = config.RESULTS_DIR / "runs" / args.run_id / "predictions.jsonl"
    if not path.is_file():
        raise SystemExit(f"no such run: {path}")
    shown = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if args.status != "all" and row["status"] != args.status:
                continue
            if args.family and f"{row['source']}/{row['type']}" != args.family:
                continue
            q = ds.by_qid.get(row["qid"])
            if q is None:
                continue
            # Test references are for final scoring, not for reading while developing.
            reference = "(test split: hidden)" if row.get("split") == "test" else repr(str(q.answer)[:200])
            print(f"[{row['status']}] {row['source']}/{row['type']}  {row['path']}")
            print(f"   Q: {q.question}")
            print(f"   reference: {reference}   answer: {str(row['pred'])[:200]!r}"
                  f"   confidence: {row.get('confidence')}")
            note = row.get("explanation") or row.get("decline_reason") or row.get("error") or ""
            if note:
                print(f"   note: {note[:300]}")
            shown += 1
            if shown >= args.n:
                break
    print(f"({shown} shown)")
    return 0


# ----------------------------------------------------------------- parser

DEFAULT_SAMPLE = config.SPLITS_DIR / "dev_baseline_200.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=None, help="dataset folder (default: data/junction-dataset*)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="what is in the dataset").set_defaults(fn=cmd_info)
    sub.add_parser("split", help="create or extend the split").set_defaults(fn=cmd_split)

    p = sub.add_parser("sample", help="save a fixed stratified sample")
    p.add_argument("--split", default="dev", choices=SPLITS)
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--name", default=None)
    p.add_argument("--need-image", action="store_true",
                   help="only questions whose file is a picture (PNG, PDF, ...), not IFC/DXF")
    p.add_argument("--force", action="store_true", help="replace an existing sample file")
    p.add_argument("--final", action="store_true", help="allow the test split")
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("baseline", help="image-only vision-model baseline")
    p.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    p.add_argument("--provider", default="gemini", choices=["gemini", "anthropic", "mock"])
    p.add_argument("--model", default=None,
                   help="default: gemini-4-carbon (gemini) or claude-opus-5-5 (anthropic)")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                   help="thinking depth (Gemini 4 Carbon accepts low, medium, high)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0, help="only N questions, spread over the sample")
    p.add_argument("--max-cost-eur", type=float, default=10.0, help="stop when this much is spent")
    p.add_argument("--dry-run", action="store_true", help="print the cost estimate and stop")
    p.add_argument("--resume", default=None, metavar="RUN_ID", help="continue an interrupted run")
    p.add_argument("--final", action="store_true", help="allow the test split")
    p.add_argument("--notes", default="")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("run", help="run an engine and log the result")
    p.add_argument("engine", choices=["ifc", "dxf"])
    p.add_argument("--sample", default=None, help="sample file (default: all questions of --split)")
    p.add_argument("--split", default="dev", choices=SPLITS)
    p.add_argument("--source", action="append", help="limit to a source (repeatable)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--final", action="store_true", help="allow the test split")
    p.add_argument("--notes", default="")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("floor", help="no-drawing guessing floor (free, dev only)")
    p.add_argument("--sample", default=None, help="sample file (default: every dev question)")
    p.add_argument("--notes", default="")
    p.set_defaults(fn=cmd_floor)

    p = sub.add_parser("score", help="score a predictions file and log the run")
    p.add_argument("predictions")
    p.add_argument("--method", required=True, help="name of the engine, as it should appear in the log")
    p.add_argument("--sample", default=None, help="sample file the predictions answer")
    p.add_argument("--split", default="dev", choices=SPLITS, help="split of the questions, without --sample")
    p.add_argument("--final", action="store_true", help="allow the test split")
    p.add_argument("--notes", default="")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("errors", help="list answers of a logged run with their questions")
    p.add_argument("run_id")
    p.add_argument("--status", default="wrong", choices=["wrong", "correct", "abstained", "ungraded", "all"])
    p.add_argument("--family", default=None, help="e.g. cubicasa5k/count")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(fn=cmd_errors)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
