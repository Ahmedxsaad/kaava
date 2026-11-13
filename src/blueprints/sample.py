"""A fixed, stratified question sample for repeatable evaluation.

The dataset mix is 53% one family (cubicasa5k counts). A proportional sample of
200 would leave a handful of questions for areas, IFC and schedules; an equal
sample would misstate the headline. So the allocation is proportional to the
square root of each family's size, with a floor per family, and the summary
re-weights family results to the real mix (scoring.summarize `weighted`).

Inside a family, questions are spread over drawings first, then over question
templates, so no single drawing or phrasing dominates.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .data import Dataset, Question


def allocate(sizes: dict[tuple[str, str], int], n: int, floor: int = 5) -> dict[tuple[str, str], int]:
    """Split n over families in proportion to sqrt(size), at least `floor`, at most size."""
    quota = {f: min(size, floor) for f, size in sizes.items()}
    remaining = n - sum(quota.values())
    while remaining > 0:
        open_families = [f for f in sizes if quota[f] < sizes[f]]
        if not open_families:
            break
        weight = {f: math.sqrt(sizes[f]) for f in open_families}
        total = sum(weight.values())
        shares = {f: remaining * weight[f] / total for f in open_families}
        gave = 0
        # Largest share first; ties broken by name so the result is deterministic.
        for f in sorted(open_families, key=lambda f: (-shares[f], f)):
            add = min(sizes[f] - quota[f], max(1, math.floor(shares[f])), remaining - gave)
            quota[f] += add
            gave += add
            if gave >= remaining:
                break
        remaining -= gave
    return {f: q for f, q in sorted(quota.items())}


def _pick(questions: Sequence[Question], k: int, seed: str) -> list[Question]:
    """Pick k questions, least-used drawing first, then least-used template."""
    def rank(q: Question) -> str:
        return hashlib.sha1(f"{seed}|{q.qid}".encode()).hexdigest()

    pool = sorted(questions, key=rank)
    per_drawing: Counter = Counter()
    per_template: Counter = Counter()
    picked: list[Question] = []
    while pool and len(picked) < k:
        best = min(
            range(len(pool)),
            key=lambda i: (per_drawing[pool[i].drawing], per_template[pool[i].template], i),
        )
        q = pool.pop(best)
        per_drawing[q.drawing] += 1
        per_template[q.template] += 1
        picked.append(q)
    return picked


def stratified_sample(questions: Sequence[Question], n: int, seed: str, floor: int = 5) -> list[Question]:
    by_family: dict[tuple[str, str], list[Question]] = defaultdict(list)
    for q in questions:
        by_family[q.family].append(q)
    quota = allocate({f: len(v) for f, v in by_family.items()}, n, floor)
    out: list[Question] = []
    for family in sorted(by_family):
        out.extend(_pick(by_family[family], quota[family], seed))
    return out


def save_sample(path: Path, name: str, split: str, seed: str, questions: Sequence[Question]) -> dict:
    data = {
        "name": name,
        "split": split,
        "seed": seed,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n": len(questions),
        "families": {f"{s}/{t}": c for (s, t), c in sorted(Counter(q.family for q in questions).items())},
        "drawings": len({q.drawing for q in questions}),
        "qids": [q.qid for q in questions],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def load_sample(path: Path, dataset: Dataset) -> tuple[dict, list[Question]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [qid for qid in data["qids"] if qid not in dataset.by_qid]
    if missing:
        raise KeyError(
            f"{len(missing)} of {len(data['qids'])} sampled questions are not in "
            f"{dataset.root.name} (e.g. {missing[0]}). Was the sample built on another dataset?"
        )
    return data, [dataset.by_qid[qid] for qid in data["qids"]]
