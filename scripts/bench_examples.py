"""The system and a vision model alone on Stoa's sample sheets, scored against an answer key of our own.

Stoa posted four sheets as examples of what the final evaluation may ask, without answers.
examples/stoa/questions.jsonl holds 27 questions on them whose answers are plain from the
sheet: printed in a table or a callout, added up by hand from printed figures, or labels
counted by hand. Each line says where its answer comes from. The key is ours, not Stoa's.

    python scripts/bench_examples.py            # both: the system, and the model alone
    python scripts/bench_examples.py --only system

Scored by the dataset's rule (counts exact, areas and lengths within 2%). A declined answer
is not wrong: it is counted on its own. Writes results/stoa_sheets/<time>-<method>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from blueprints import baseline, config, model_path  # noqa: E402
from blueprints.data import Question  # noqa: E402
from blueprints.scoring import score_answer  # noqa: E402
from blueprints.vlm import make_vlm  # noqa: E402

SHEETS = config.REPO_ROOT / "examples" / "stoa"


class Sheets:
    """The example sheets, looking like a dataset to an engine."""

    def abspath(self, path: str) -> Path:
        return SHEETS / path

    def formats(self, path: str) -> list[str]:
        return [path]


def run(name: str, engine, questions: list[dict]) -> dict:
    rows, started = [], time.time()
    for n, item in enumerate(questions, 1):
        q = Question(qid=f"stoa-{n}", path=item["file"], question=item["question"], answer=None, unit=None,
                     type=item["type"], source="stoa_sheets", drawing=item["file"], group=item["file"])
        ans = engine(q, Sheets())
        if ans.answer is None:
            status = "declined"
        else:
            status = score_answer(ans.answer, item["answer"], item["type"], gold_unit=item["unit"], pred_unit=ans.unit).status
        rows.append({"file": item["file"], "question": item["question"], "gold": item["answer"], "gold_unit": item["unit"],
                     "key": item["key"], "pred": ans.answer, "pred_unit": ans.unit, "state": ans.state, "status": status,
                     "method": ans.method, "explanation": ans.explanation, "reason": ans.reason,
                     "cost_eur": config.usd_to_eur(ans.cost_usd or 0.0), "latency_ms": ans.latency_ms,
                     "working": ans.meta.get("working"), "error": ans.error})
        print(f"  {n:>2}/{len(questions)} {status:<9} {str(ans.answer):>10} (key {item['answer']})  {item['question'][:70]}")
    right = sum(r["status"] == "correct" for r in rows)
    wrong = sum(r["status"] == "wrong" for r in rows)
    declined = sum(r["status"] == "declined" for r in rows)
    cost = sum(r["cost_eur"] for r in rows)
    summary = {"method": name, "n": len(rows), "correct": right, "wrong": wrong, "declined": declined,
               "accuracy": right / len(rows), "wrong_when_answered": wrong / max(1, right + wrong),
               "eur_total": round(cost, 4), "eur_per_question": round(cost / len(rows), 5),
               "seconds": round(time.time() - started, 1),
               "key": "written by us from the sheets (examples/stoa/questions.jsonl); Stoa gave no answers",
               "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    out = config.RESULTS_DIR / "stoa_sheets" / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{name.split(':')[0]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{name}: {right} right, {wrong} wrong, {declined} no number, of {len(rows)} | EUR {cost:.3f} | {out.relative_to(config.REPO_ROOT)}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["system", "alone"], default=None)
    args = parser.parse_args()
    load_dotenv(config.REPO_ROOT / ".env")
    questions = [json.loads(line) for line in (SHEETS / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    vlm = make_vlm("gemini", "gemini-4-carbon", "low")
    if args.only != "alone":
        run(f"system:{vlm.label}", model_path.make(vlm), questions)
    if args.only != "system":
        run(f"alone:{vlm.label}", baseline.make_engine(vlm), questions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
