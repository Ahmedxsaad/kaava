"""The accuracy regression CI runs on every push (.github/workflows/ci.yml).

The exact engines (no model, no key, no cost) answer every development question of the dataset's
preview on Swiss floor plans and on IFC models. A change that makes them less right than they are,
or that lets one wrong answer through as Verified, fails the build.

    python scripts/ci_regression.py

Counted as the dataset counts (BLUEPRINTS_COUNT=outlines), so "right" means agreeing with the
dataset's own answers. The floors are a little under what the engines score today.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = "data/junction-dataset-preview"
# source: (least share right when answered, least share answered, wrong Verified answers allowed)
FLOORS = {"swiss_dwellings": (0.990, 0.99, 0), "ifc_models": (0.990, 0.99, 0)}
OVERALL = re.compile(r"^OVERALL\s+(\d+)\s+(\d+)\s+[\d.]+%\s+([\d.]+)%\s+([\d.]+)%\s+\S+\s+\S+(?:\s+(\d+)\s+(\d+))?", re.MULTILINE)


def main() -> int:
    if not (ROOT / DATASET).is_dir():
        print(f"{DATASET} is not there: run `bash 02-dataset/download.sh preview` first", file=sys.stderr)
        return 2
    failed = False
    for source, (least_right, least_answered, wrong_allowed) in FLOORS.items():
        done = subprocess.run([str(ROOT / "bp"), "--dataset", DATASET, "run", "router", "--split", "dev", "--source", source],
                              cwd=ROOT, capture_output=True, text=True, env={**os.environ, "BLUEPRINTS_COUNT": "outlines"})
        found = OVERALL.search(done.stdout)
        if done.returncode != 0 or not found:
            print(done.stdout[-2000:], done.stderr[-2000:], sep="\n", file=sys.stderr)
            print(f"{source}: the run did not finish", file=sys.stderr)
            failed = True
            continue
        n, answered = int(found.group(1)), int(found.group(2))
        right, cover = float(found.group(3)) / 100, float(found.group(4)) / 100
        verified, wrong = (int(found.group(5)), int(found.group(6))) if found.group(5) else (0, 0)
        ok = right >= least_right and cover >= least_answered and wrong <= wrong_allowed and n > 0
        failed |= not ok
        print(f"{'ok  ' if ok else 'FAIL'} {source}: {n} questions, {answered} answered ({cover:.1%}), {right:.1%} right when answered "
              f"(floor {least_right:.1%}), {verified} Verified of which {wrong} wrong (allowed {wrong_allowed})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
