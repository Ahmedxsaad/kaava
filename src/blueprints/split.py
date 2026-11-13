"""Stratified dev / calibration / test split, by drawing group, never by question.

  dev          build and debug engines here; look at anything.
  calibration  only for setting Verified thresholds (RELIABILITY.md section 4).
  test         untouched until final numbers; reading it needs an explicit flag.

Properties the code guarantees:
  * A group (see data.py) is in exactly one split, so no drawing, building or
    project is shared between splits.
  * Stratified per source, balancing every question type towards the fractions.
  * Frozen: an assigned group never moves. When the full dataset arrives, the same
    command only places the new groups, so nothing tuned on dev leaks into test.
  * IFC models that are the same project under another file name or in another
    source (same IfcProject GlobalId) are linked and share a split.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from . import config
from .data import Dataset, Question

SPLITS = ("dev", "calibration", "test")
FRACTIONS = {"dev": 0.50, "calibration": 0.25, "test": 0.25}
SEED = "stoa-junction-2026-v1"
# A GlobalId shared by more groups than this is an export-template default, not one
# building, and is ignored as a link.
MAX_GUID_LINK = 4

RULE = (
    "Unit = drawing group: cubicasa5k one plan; swiss_dwellings one site (all its "
    "floors); ifc_models one project (models and rendered plans); ifc_bench one "
    "project; opening_design one project folder. Groups sharing an IfcProject GlobalId "
    "are linked. Per source, units are placed largest first into the split with the "
    "largest remaining per-type deficit. Assignments are frozen."
)


class SplitLocked(RuntimeError):
    pass


@dataclass
class Unit:
    """One or more linked groups that must share a split."""

    groups: list[str]
    counts: Counter = field(default_factory=Counter)  # (source, type) -> questions

    @property
    def id(self) -> str:
        return self.groups[0]

    @property
    def n(self) -> int:
        return sum(self.counts.values())

    @property
    def source(self) -> str:
        per_source: Counter = Counter()
        for (source, _type), n in self.counts.items():
            per_source[source] += n
        return max(sorted(per_source), key=lambda s: per_source[s])


def _order_key(unit: Unit) -> tuple:
    digest = hashlib.sha1(f"{SEED}|{unit.id}".encode()).hexdigest()
    return (-unit.n, digest)


# ----------------------------------------------------------------- units


def find_links(dataset: Dataset, questions: Sequence[Question]) -> list[list[str]]:
    """Groups that are the same IFC project under different names or sources."""
    by_guid: dict[str, set[str]] = defaultdict(set)
    seen: set[tuple[str, str]] = set()
    for q in questions:
        if q.source not in ("ifc_models", "ifc_bench", "opening_design"):
            continue
        ifc = q.path if q.path.lower().endswith(".ifc") else None
        if ifc is None:
            model = dataset.ifc_model_of(q.path)
            ifc = f"{model}.ifc" if model and f"{model}.ifc" in dataset.files else None
        if ifc is None or (ifc, q.group) in seen:
            continue
        seen.add((ifc, q.group))
        guid = dataset.ifc_project_guid(ifc)
        if guid:
            by_guid[guid].add(q.group)
    return sorted(sorted(g) for g in by_guid.values() if 2 <= len(g) <= MAX_GUID_LINK)


def build_units(questions: Sequence[Question], links: Iterable[Sequence[str]] = ()) -> list[Unit]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for q in questions:
        find(q.group)
    for link in links:
        for other in link[1:]:
            a, b = find(link[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)

    members: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, Counter] = defaultdict(Counter)
    for q in questions:
        root = find(q.group)
        members[root].add(q.group)
        counts[root][q.family] += 1
    return [Unit(groups=sorted(members[r]), counts=counts[r]) for r in sorted(members)]


# ----------------------------------------------------------------- assignment


def assign(units: Sequence[Unit], existing: dict[str, str] | None = None) -> tuple[dict[str, str], list[str]]:
    """Place every unit. Returns ({group: split}, warnings). `existing` is never changed."""
    existing = dict(existing or {})
    out: dict[str, str] = dict(existing)
    warnings: list[str] = []

    by_source: dict[str, list[Unit]] = defaultdict(list)
    for u in units:
        by_source[u.source].append(u)

    for source in sorted(by_source):
        stratum = sorted(by_source[source], key=_order_key)
        totals: Counter = Counter()
        for u in stratum:
            totals.update(u.counts)
        current = {s: Counter() for s in SPLITS}

        pending: list[Unit] = []
        for u in stratum:  # frozen units first: they set the starting balance
            placed = {existing[g] for g in u.groups if g in existing}
            if len(placed) > 1:
                warnings.append(
                    f"linked groups already sit in different splits, left as they are: {u.groups}"
                )
                for g in u.groups:
                    out.setdefault(g, sorted(placed)[0])
                continue
            if placed:
                split = placed.pop()
                for g in u.groups:
                    out[g] = split
                current[split].update(u.counts)
            else:
                pending.append(u)

        for u in pending:
            def deficit(split: str) -> float:
                return sum(
                    n * (FRACTIONS[split] * totals[fam] - current[split][fam]) / totals[fam]
                    for fam, n in u.counts.items()
                )

            split = max(SPLITS, key=lambda s: (deficit(s), -SPLITS.index(s)))
            for g in u.groups:
                out[g] = split
            current[split].update(u.counts)

        empty = [s for s in SPLITS if not current[s]]
        if empty:
            warnings.append(
                f"{source}: no questions in {', '.join(empty)} ({len(stratum)} unit(s) in this dataset)"
            )
    return out, warnings


# ----------------------------------------------------------------- persistence


class Split:
    def __init__(self, groups: dict[str, str], meta: dict | None = None):
        self.groups = groups
        self.meta = meta or {}

    @classmethod
    def load(cls, path: Path | None = None) -> "Split":
        path = path or config.SPLIT_FILE
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found. Run: ./bp split")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["groups"], {k: v for k, v in data.items() if k != "groups"})

    def of(self, q: Question) -> str:
        return self.groups.get(q.group, "unassigned")

    def select(self, questions: Iterable[Question], name: str, *, allow_test: bool = False) -> list[Question]:
        if name not in SPLITS:
            raise ValueError(f"unknown split {name!r}; choose from {SPLITS}")
        if name == "test" and not allow_test:
            raise SplitLocked(
                "The test split is for final numbers only. Pass --final to read it; "
                "the run is then logged as a test run."
            )
        return [q for q in questions if self.of(q) == name]


def make_split(dataset: Dataset, path: Path | None = None) -> tuple[Split, dict]:
    """Create the split, or extend a saved one with the groups it has not seen."""
    path = path or config.SPLIT_FILE
    questions = dataset.questions
    previous: dict = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    existing: dict[str, str] = previous.get("groups", {})

    links = find_links(dataset, questions)
    known_links = {tuple(l) for l in previous.get("links", [])}
    all_links = sorted(known_links | {tuple(l) for l in links})
    units = build_units(questions, all_links)
    groups, warnings = assign(units, existing)

    added = sorted(set(groups) - set(existing))
    history = list(previous.get("history", []))
    if added or not previous:
        history.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": dataset.root.name,
            "questions": len(questions),
            "groups_added": len(added),
        })
    data = {
        "version": 1,
        "seed": SEED,
        "fractions": FRACTIONS,
        "rule": RULE,
        "history": history,
        "links": [list(l) for l in all_links],
        "groups": dict(sorted(groups.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    report = {"added": len(added), "kept": len(existing), "warnings": warnings, "links": all_links}
    return Split(groups, data), report


def describe(split: Split, questions: Sequence[Question]) -> str:
    """Table of questions per family and split, with drawing and group counts."""
    per: dict[tuple, Counter] = defaultdict(Counter)
    drawings: dict[tuple, set] = defaultdict(set)
    groups: dict[tuple, set] = defaultdict(set)
    for q in questions:
        s = split.of(q)
        per[q.family][s] += 1
        drawings[(q.source, s)].add(q.drawing)
        groups[(q.source, s)].add(q.group)

    cols = list(SPLITS) + (["unassigned"] if any("unassigned" in c for c in per.values()) else [])
    head = f"{'source / type':<28}" + "".join(f"{c:>13}" for c in cols) + f"{'total':>8}"
    lines = [head, "-" * len(head)]
    totals: Counter = Counter()
    last_source = None
    for (source, qtype), c in sorted(per.items()):
        if last_source not in (None, source):
            lines.append("")
        if source != last_source:
            cells = "".join(
                f"{f'{len(groups[(source, s)])}g/{len(drawings[(source, s)])}d':>13}" for s in cols
            )
            lines.append(f"{source:<28}{cells}")
        last_source = source
        total = sum(c.values())
        cells = "".join(f"{f'{c[s]} ({100 * c[s] / total:.0f}%)':>13}" for s in cols)
        lines.append(f"  {qtype:<26}{cells}{total:>8}")
        totals.update(c)
    grand = sum(totals.values())
    lines.append("-" * len(head))
    lines.append(
        f"{'all questions':<28}"
        + "".join(f"{f'{totals[s]} ({100 * totals[s] / grand:.0f}%)':>13}" for s in cols)
        + f"{grand:>8}"
    )
    lines.append("(g = groups, d = drawings; a group never spans two splits)")
    return "\n".join(lines)
