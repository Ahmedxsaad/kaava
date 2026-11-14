"""Can "the labels and the vision model give the same count" be Verified on scans?

On a scan the label engine reads the room names printed on the plan (local OCR); the
vision model marks the rooms it sees. This measures, on calibration drawings, how often
an answer both agree on is wrong, and writes the family `labels+model:v1/count` into
splits/verified_thresholds.json: a threshold if the 95% upper bound on that error rate
is at most 1%, none otherwise.

The design was fixed before any calibration answer of the model was seen:
  - questions: calibration scans where the label engine's answer scores 0.9 or more,
    picked by score only, spread over drawings (one per drawing first, then a second, ...);
  - one family, one level (every agreeing answer), so one bound at 95% confidence;
  - when the two differ, the shown answer is the reading that was right more often here.

Only totals are printed. No calibration answer is looked at one by one.

    .venv/bin/python -I scripts/calibrate_agreement.py sample --n 800
    ./bp --dataset data/junction-dataset run pointing --split calibration \
        --sample splits/calibration_scans_agree_800.json --provider gemini --model ... --effort low
    .venv/bin/python -I scripts/calibrate_agreement.py finish --model-run <run id of that run>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints import config, verify  # noqa: E402
from blueprints.data import Dataset  # noqa: E402
from blueprints.engines import raster_labels, router  # noqa: E402
from blueprints.sample import save_sample  # noqa: E402
from blueprints.split import Split  # noqa: E402

DATASET = config.DATA_DIR / "junction-dataset"
SEED = "labels+model:v1"


def _raw_run() -> str:
    """The run that set the current thresholds: every engine's raw answer on the calibration split."""
    return verify.load_thresholds()["calibration_runs"][0]


def _strong_label_rows(run_id: str) -> dict[str, dict]:
    rows = {}
    with open(config.RESULTS_DIR / "runs" / run_id / "predictions.jsonl", encoding="utf-8") as f:
        for line in f:
            if f'"{raster_labels.METHOD}"' not in line:
                continue
            row = json.loads(line)
            if (row["method"] == raster_labels.METHOD and row.get("pred") is not None
                    and (row.get("confidence") or 0.0) >= router.LABELS_STAND_FROM):
                rows[row["qid"]] = row
    return rows


def cmd_sample(args: argparse.Namespace) -> int:
    ds, split = Dataset(DATASET), Split.load()
    rows = _strong_label_rows(args.labels_run or _raw_run())
    order = lambda text: hashlib.sha1(f"{SEED}:{text}".encode()).hexdigest()  # noqa: E731
    by_drawing: dict[str, list[str]] = defaultdict(list)
    for qid, row in rows.items():
        if split.of(ds.by_qid[qid]) != "calibration":
            raise SystemExit(f"{qid} is not a calibration question")
        by_drawing[row["path"]].append(qid)
    for qids in by_drawing.values():
        qids.sort(key=order)
    picked: list[str] = []
    depth = 0
    while len(picked) < args.n and any(len(q) > depth for q in by_drawing.values()):
        for drawing in sorted(by_drawing, key=order):  # one question per drawing, then a second round, ...
            if len(by_drawing[drawing]) > depth and len(picked) < args.n:
                picked.append(by_drawing[drawing][depth])
        depth += 1
    name = f"calibration_scans_agree_{len(picked)}"
    data = save_sample(config.SPLITS_DIR / f"{name}.json", name, "calibration", SEED, [ds.by_qid[q] for q in picked])
    print(f"splits/{name}.json: {data['n']} questions on {data['drawings']} drawings, "
          f"from {len(rows)} strong label answers on {len(by_drawing)} calibration drawings")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    labels_run = args.labels_run or _raw_run()
    labels = _strong_label_rows(labels_run)
    model = {}
    with open(config.RESULTS_DIR / "runs" / args.model_run / "predictions.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                model[row["qid"]] = row
    summary = json.loads((config.RESULTS_DIR / "runs" / args.model_run / "summary.json").read_text())
    if summary.get("split") != "calibration":
        raise SystemExit("the model run is not on the calibration split")
    both = [q for q in model if q in labels]
    silent = [q for q in both if model[q].get("pred") is None]
    answered = [q for q in both if model[q].get("pred") is not None]
    agree = [q for q in answered if float(model[q]["pred"]) == float(labels[q]["pred"])]
    differ = [q for q in answered if q not in set(agree)]
    right = lambda rows, qids: sum(rows[q]["status"] == "correct" for q in qids)  # noqa: E731

    rows = [{"method": router.AGREED, "type": "count", "confidence": router.AGREED_SCORE, "status": labels[q]["status"]}
            for q in agree if labels[q]["status"] in ("correct", "wrong")]
    outcome = verify.calibrate(rows)
    family = outcome["families"].get(verify.family_of(router.AGREED, "count"))
    if family is None:
        raise SystemExit("no agreeing answers to calibrate on")
    family["design"] = {"labels_run": labels_run, "model_run": args.model_run, "model_method": summary.get("method"),
                        "questions": len(both), "model_gave_no_answer": len(silent), "agree": len(agree),
                        "differ": len(differ), "labels_right_when_they_differ": right(labels, differ),
                        "model_right_when_they_differ": right(model, differ),
                        "labels_right_overall": right(labels, both), "model_right_overall": right(model, both)}

    thresholds = verify.load_thresholds()
    thresholds["families"][verify.family_of(router.AGREED, "count")] = family
    runs = thresholds.setdefault("calibration_runs", [])
    if args.model_run not in runs:
        runs.append(args.model_run)
    verify.THRESHOLDS_FILE.write_text(json.dumps(thresholds, indent=1) + "\n", encoding="utf-8")

    level = family["levels_tested"][0]
    print(f"calibration scans, strong label answers put to the model: {len(both)}")
    print(f"  labels right {right(labels, both)}, model right {right(model, both)}, model gave no answer {len(silent)}")
    print(f"  the two agree on {len(agree)}: wrong {level['errors']}, 95% upper bound {100 * level['upper_bound']:.2f}%")
    print(f"  the two differ on {len(differ)}: labels right {right(labels, differ)}, model right {right(model, differ)}")
    print("  decision: " + (f"Verified when the two agree (bound at most {100 * outcome['alpha']:g}%)"
                            if family["threshold"] is not None else "not Verified: the bound is above the target"))
    print(f"saved in {verify.THRESHOLDS_FILE.relative_to(config.REPO_ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=800)
    p.add_argument("--labels-run", default=None)
    p.set_defaults(fn=cmd_sample)
    p = sub.add_parser("finish")
    p.add_argument("--model-run", required=True)
    p.add_argument("--labels-run", default=None)
    p.set_defaults(fn=cmd_finish)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
