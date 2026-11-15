"""Who should read a free-form question the second time: the same model again, or a different one?

A question in a person's own words is read as a query twice, and only two readings that agree are
run. Two readings by the same model can agree and both be wrong. This measures that, on questions
whose right reading is known: they are written from development plans' own room names, so the
rooms each one means are known from the drawing (no answer of the dataset is read).

    python scripts/bench_readers.py [--plans 10]

For each question: Gemini 4 Carbon reads it twice, Gemma 4 once. Reported for "Gemini twice" and
"Gemini + Gemma": how often the two readings agree and are right, agree and are wrong (the case
that matters: it would be answered wrongly), or differ (no number is given). Writes
results/readers/<time>.json.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from blueprints import config, plan_query  # noqa: E402
from blueprints.engines import dxf, dxf_index  # noqa: E402
from blueprints.vlm import make_vlm  # noqa: E402

ROOT = config.REPO_ROOT / "data" / "junction-dataset" / "data" / "swiss_dwellings"


def questions_for(index: dict, rng: random.Random) -> list[tuple[str, plan_query.Query]]:
    """Questions about this plan whose reading is known from its own list of rooms."""
    rooms = plan_query.rooms_of(index)
    names = Counter((r.get("name") or "").strip() for r in rooms)
    names.pop("", None)
    # A name that is no part of another name on the plan, so "the Zimmer rooms" can mean only those.
    clear = [n for n in names if len(n) >= 3 and not any(n.lower() in other.lower() for other in names if other != n)]
    several = [n for n in clear if names[n] >= 2]
    out: list[tuple[str, plan_query.Query]] = []
    ids = lambda name: [i for i, r in enumerate(rooms) if (r.get("name") or "").strip() == name]  # noqa: E731
    if several:
        a = rng.choice(several)
        out.append((f"how many windows are there in the {a} rooms", plan_query.Query(measure="count_items", item="window", rooms=ids(a))))
        b = rng.choice(several)
        out.append((f"what is the total surface of every {b}", plan_query.Query(measure="area", rooms=ids(b), combine="sum")))
        c = rng.choice(several)
        out.append((f"how many rooms on this plan are called {c}?", plan_query.Query(measure="count_rooms", rooms=ids(c))))
    numbered = [(i, r) for i, r in enumerate(rooms) if r.get("number") and sum(1 for o in rooms if o.get("number") == r["number"]) == 1]
    if numbered:
        i, r = rng.choice(numbered)
        out.append((f"give me the floor area of room {r['number']}", plan_query.Query(measure="area", rooms=[i], combine="sum")))
        i, r = rng.choice(numbered)
        out.append((f"how many metres of skirting board does room {r['number']} need, doors left out", plan_query.Query(measure="baseboard", rooms=[i], combine="sum")))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=int, default=10)
    parser.add_argument("--gemma", default="gemma-4-31b-it")
    args = parser.parse_args()
    load_dotenv(config.REPO_ROOT / ".env")
    groups = json.loads((config.REPO_ROOT / "splits" / "split_v1.json").read_text())["groups"]
    dev = sorted(k for k, v in groups.items() if v == "dev" and k.startswith("swiss_dwellings/"))
    rng = random.Random(20261115)
    cases = []
    for site in rng.sample(dev, len(dev)):
        files = sorted(ROOT.glob(Path(site).name + "_*.dxf"))
        if not files:
            continue
        index = dxf_index.load_index(files[0])
        for question, truth in questions_for(index, rng):
            cases.append({"plan": files[0].stem, "index": index, "question": question, "truth": truth})
        if len({c["plan"] for c in cases}) >= args.plans:
            break
    print(f"{len(cases)} questions on {len({c['plan'] for c in cases})} development plans", flush=True)
    carbon, gemma = make_vlm("gemini", "gemini-4-carbon", "low"), make_vlm("gemma", args.gemma)

    def read(job):
        case, llm = job
        return plan_query.ask(case["question"], case["index"], llm, dxf.is_outdoor)

    started = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        first = list(pool.map(read, [(c, carbon) for c in cases]))
        second = list(pool.map(read, [(c, carbon) for c in cases]))
    with ThreadPoolExecutor(max_workers=3) as pool:  # Gemma's free tier is rate-limited
        other = list(pool.map(read, [(c, gemma) for c in cases]))

    right = lambda q, c: not q.error and not q.cannot and plan_query.same_reading(q, c["truth"])  # noqa: E731

    def pair(a, b) -> dict:
        tally = Counter()
        for qa, qb, case in zip(a, b, cases):
            if qa.error or qb.error:
                tally["a call failed"] += 1
            elif plan_query.same_reading(qa, qb) and not qa.cannot:
                tally["agree, right" if right(qa, case) else "agree, wrong"] += 1
            else:
                tally["differ: no number"] += 1
        return dict(tally)

    report = {
        "questions": len(cases), "plans": len({c["plan"] for c in cases}), "seconds": round(time.time() - started, 1),
        "alone": {"gemini-4-carbon": sum(right(q, c) for q, c in zip(first, cases)),
                  args.gemma: sum(right(q, c) for q, c in zip(other, cases))},
        "failed calls": {"gemini-4-carbon": sum(1 for q in first + second if q.error), args.gemma: sum(1 for q in other if q.error)},
        "gemini twice": pair(first, second), "gemini + gemma": pair(first, other),
        "eur": {"gemini-4-carbon, per reading": round(config.usd_to_eur(sum(q.cost_usd or 0 for q in first + second)) / max(1, 2 * len(cases)), 5),
                args.gemma + ", per reading": round(config.usd_to_eur(sum(q.cost_usd or 0 for q in other)) / max(1, len(cases)), 5)},
        "seconds per reading": {"gemini-4-carbon": round(sum(q.latency_ms for q in first) / 1000 / len(cases), 1),
                                args.gemma: round(sum(q.latency_ms for q in other) / 1000 / len(cases), 1)},
        "where gemini twice agreed and was wrong": [
            {"question": c["question"], "plan": c["plan"], "gemma agreed": plan_query.same_reading(qa, qo)}
            for qa, qb, qo, c in zip(first, second, other, cases)
            if not qa.error and not qb.error and plan_query.same_reading(qa, qb) and not qa.cannot and not right(qa, c)],
    }
    out = config.REPO_ROOT / "results" / "readers" / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("written to", out.relative_to(config.REPO_ROOT))


if __name__ == "__main__":
    main()
