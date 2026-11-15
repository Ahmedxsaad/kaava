"""Give each kept answer the cost of its inference, from a record of that cost. Never an invented figure.

An answer shown again in the app spends nothing, but the reader should still see what working it out
cost. Answers kept before 15 Nov 2026 did not store that. This script fills it in from two records,
in this order, and leaves the answer without a figure where neither has one:

1. the question log (ledger.py): the row written when the answer was first worked out, found by its
   answer and its response time to the millisecond. Note: "when it was first worked out".
2. a benchmark run on the example sheets (results/stoa_sheets/*-system.json): the same question on the
   same sheet, worked out by the same pipeline, with an answer that agrees with the kept one by the
   dataset's rule (counts exact, measures within 2%). Note: "measured for this question in the
   benchmark run <file>", with the answer of that run when it is not the very same number.
3. with --measure (paid: one fresh run per question, a few cents each): the question is worked out
   afresh once, by the same engine the app uses, and what that cost is recorded in
   results/kept_costs/. The kept answer itself is not replaced. Note: "measured on a fresh run of
   this question on <date>". A fresh answer that disagrees with the kept one is reported, and the note
   then says so in as many words: the reader sees that two runs differed.

    .venv/bin/python scripts/kept_costs.py              # report only
    .venv/bin/python scripts/kept_costs.py --write      # write cost_usd and cost_note into the kept files
    .venv/bin/python scripts/kept_costs.py --measure --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time

from blueprints import config, ledger

EXAMPLES = config.REPO_ROOT / "examples"
FOLDERS = (EXAMPLES / "answers", config.REPO_ROOT / "cache" / "answers")


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return str(a) == str(b)


def _agrees(a, b, unit: str | None) -> bool:
    """The dataset's rule: a count must be the same number, a measure within 2%, a text the same text."""
    if unit in (None, "", "count"):
        return _same(a, b)
    try:
        return abs(float(a) - float(b)) <= 0.02 * abs(float(b))
    except (TypeError, ValueError):
        return str(a) == str(b)


def questions_of_examples() -> dict[str, tuple[str, str]]:
    """Kept-file name -> (drawing id, question) for every question the examples and the tour know."""
    import re

    ids = {}
    for listing in sorted(EXAMPLES.glob("*/sheets.json")):
        for entry in json.loads(listing.read_text(encoding="utf-8")):
            file = listing.parent / entry["file"]
            if file.is_file():
                ids[entry["file"]] = (hashlib.sha1(file.read_bytes()).hexdigest()[:12], entry.get("questions", []))
    asked = {q for _, qs in ids.values() for q in qs}
    tour = config.REPO_ROOT / "web" / "src" / "tour.ts"
    if tour.is_file():
        asked |= {m.group(1).replace('\\"', '"') for m in re.finditer(r'question:\s*"((?:[^"\\]|\\.)*)"', tour.read_text(encoding="utf-8"))}
    keyed = EXAMPLES / "stoa" / "questions.jsonl"
    if keyed.is_file():
        asked |= {json.loads(line)["question"] for line in keyed.read_text(encoding="utf-8").splitlines() if line.strip()}
    out = {}
    for ident, _ in ids.values():
        for question in asked:
            name = hashlib.sha1(f"uploads/{ident}.png\x1f{question}".encode("utf-8")).hexdigest()[:20] + ".json"
            out[name] = (f"uploads/{ident}", question)
    return out


def measure(names: list[str]) -> dict[str, dict]:
    """Work each of these kept questions out afresh, once, and record what it cost. Paid."""
    from dotenv import load_dotenv

    from blueprints import model_path, server
    from blueprints.vlm import make_vlm

    load_dotenv(config.REPO_ROOT / ".env")
    known = questions_of_examples()
    service = server.Service()
    engine = model_path.make(make_vlm("gemini", "gemini-4-carbon", "low"))  # as the app does (server._model_fallback)
    out_dir = config.RESULTS_DIR / "kept_costs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp, rows = time.strftime("%Y%m%d-%H%M%S"), {}
    for name in names:
        if name not in known:
            print(f"  {name[:12]}: its question is not among the examples; not measured")
            continue
        drawing, question = known[name]
        asked, _ = service._question_for(drawing, question)
        t0 = time.perf_counter()
        ans = engine(asked, service.ds)
        rows[name] = {"kept": name, "drawing": drawing, "question": question, "answer": ans.answer, "unit": ans.unit,
                      "state": ans.state, "method": ans.method, "error": ans.error,
                      "cost_eur": config.usd_to_eur(ans.cost_usd or 0.0), "latency_ms": ans.latency_ms or (time.perf_counter() - t0) * 1000,
                      "measured_on": time.strftime("%Y-%m-%d")}
        print(f"  measured {name[:12]}: answer {ans.answer} {ans.unit or ''}  EUR {rows[name]['cost_eur']:.4f}  {rows[name]['latency_ms'] / 1000:.0f} s  | {question[:60]}")
        (out_dir / f"{stamp}-fresh-runs.json").write_text(json.dumps(list(rows.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def from_log() -> list[dict]:
    """Fresh, paid answers in the question log: answer, response time, cost."""
    if not ledger.enabled():
        return []
    connection = ledger._connect()
    try:
        rows = connection.run("SELECT answer, latency_ms, cost_eur, question FROM kaava.answers "
                              "WHERE cost_eur > 0 AND NOT replayed AND answer IS NOT NULL")
    finally:
        connection.close()
    return [{"answer": a, "latency_ms": int(ms or 0), "cost_eur": float(c), "question": q} for a, ms, c, q in rows]


def from_bench() -> dict[str, dict]:
    """Kept-file name -> the newest benchmark row for that sheet and question."""
    ids = {}
    for listing in sorted(EXAMPLES.glob("*/sheets.json")):
        for entry in json.loads(listing.read_text(encoding="utf-8")):
            file = listing.parent / entry["file"]
            if file.is_file():
                ids[entry["file"]] = hashlib.sha1(file.read_bytes()).hexdigest()[:12]
    out: dict[str, dict] = {}
    folder = config.RESULTS_DIR / "stoa_sheets"
    for run in sorted(folder.glob("*-system.json")) if folder.is_dir() else []:  # oldest first: the newest wins
        for row in json.loads(run.read_text(encoding="utf-8"))["rows"]:
            if row.get("error") or row.get("pred") is None or row["file"] not in ids:
                continue
            path = f"uploads/{ids[row['file']]}.png"
            name = hashlib.sha1(f"{path}\x1f{row['question']}".encode("utf-8")).hexdigest()[:20] + ".json"
            out[name] = {**row, "run": run.name}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the figures into the kept files")
    parser.add_argument("--measure", action="store_true", help="work out afresh, once, each example answer no record covers (paid)")
    args = parser.parse_args()
    log, bench = from_log(), from_bench()
    fresh: dict[str, dict] = {}
    for old in sorted((config.RESULTS_DIR / "kept_costs").glob("*-fresh-runs.json")):  # measured before: not paid for twice
        fresh.update({row["kept"]: row for row in json.loads(old.read_text(encoding="utf-8"))})
    if args.measure:
        missing = [f.name for f in sorted(FOLDERS[0].glob("*.json"))
                   if json.loads(f.read_text(encoding="utf-8")).get("cost_usd") is None and f.name not in fresh
                   and not any(r["latency_ms"] == int(json.loads(f.read_text(encoding="utf-8")).get("latency_ms") or -1) for r in log)
                   and not (f.name in bench and _agrees(bench[f.name]["pred"], json.loads(f.read_text(encoding="utf-8"))["answer"],
                                                        json.loads(f.read_text(encoding="utf-8")).get("unit")))]
        fresh.update(measure(missing))
    known = unknown = 0
    for folder in FOLDERS:
        for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            kept = json.loads(file.read_text(encoding="utf-8"))
            if kept.get("cost_usd") is not None:
                known += 1
                continue
            cost_eur = note = None
            row = next((r for r in log if r["latency_ms"] == int(kept.get("latency_ms") or -1) and _same(r["answer"], kept["answer"])), None)
            if row is not None:
                cost_eur, note = row["cost_eur"], "when it was first worked out"
            elif file.name in bench and _agrees(bench[file.name]["pred"], kept["answer"], kept.get("unit")) and bench[file.name].get("cost_eur"):
                there = bench[file.name]
                cost_eur, note = there["cost_eur"], f"measured for this question in the benchmark run {there['run'].removesuffix('.json')}"
                if not _same(there["pred"], kept["answer"]):
                    note += f", where the answer was {there['pred']}"
            elif file.name in fresh and not fresh[file.name].get("error") and fresh[file.name].get("answer") is not None:
                there = fresh[file.name]
                if _agrees(there["answer"], kept["answer"], kept.get("unit")):
                    cost_eur, note = there["cost_eur"], f"measured on a fresh run of this question on {there['measured_on']}"
                    if not _same(there["answer"], kept["answer"]):
                        note += f", where the answer was {there['answer']}"
                else:
                    # The fresh run did not give the kept answer. Its cost is still what this question costs to
                    # work out, and the reader is told the two runs differ rather than shown no figure at all.
                    cost_eur = there["cost_eur"]
                    note = (f"measured on a fresh run of this question on {there['measured_on']}, which gave {there['answer']}: "
                            f"two runs of this question did not agree")
                    print(f"  DISAGREES   {file.name[:12]}: kept {kept['answer']}, fresh run {there['answer']}")
            where = file.parent.parent.name + "/" + file.parent.name
            if cost_eur is None:
                unknown += 1
                print(f"  no record   {where}/{file.name[:12]}  answer {kept['answer']}  ({kept.get('method', '')[-24:]})")
                continue
            known += 1
            print(f"  EUR {cost_eur:.4f}  {where}/{file.name[:12]}  answer {kept['answer']}  {note}")
            if args.write:
                kept["cost_usd"], kept["cost_note"] = cost_eur * config.USD_PER_EUR, note
                file.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    print(f"{known} kept answers with a recorded inference cost, {unknown} without" + ("" if args.write else " (nothing written: --write)"))


if __name__ == "__main__":
    main()
