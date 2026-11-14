"""A sample by drawing: every question the dataset asks of N scanned plans.

A scan is read once and all its questions are answered from that reading (scan_index.py),
so what it costs per question only shows on a sample that holds whole drawings.

    python scripts/sample_scan_drawings.py --name dev_scan_drawings_44 --within splits/dev_scans_fresh_100.json
    python scripts/sample_scan_drawings.py --name dev_scan_drawings_fresh_40 --n 40 --apart-from splits/a.json splits/b.json

--within: the drawings of an existing sample (their other questions are added).
--apart-from: only drawings that none of these samples has used.
Development split only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints import config  # noqa: E402
from blueprints.data import Dataset  # noqa: E402
from blueprints.sample import save_sample  # noqa: E402
from blueprints.split import Split  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--n", type=int, default=None, help="how many drawings (default: all that qualify)")
    parser.add_argument("--within", default=None, help="take the drawings of this sample")
    parser.add_argument("--apart-from", nargs="*", default=[], help="leave out drawings these samples used")
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args()

    ds = Dataset(args.dataset)
    split = Split.load()
    scans = [q for q in ds.questions if q.source == "cubicasa5k" and split.of(q) == "dev"]
    by_drawing: dict[str, list] = {}
    for q in scans:
        by_drawing.setdefault(q.drawing, []).append(q)

    def drawings_of(sample_file: str) -> set[str]:
        qids = set(json.loads(Path(sample_file).read_text(encoding="utf-8"))["qids"])
        return {q.drawing for q in ds.questions if q.qid in qids}

    pool = set(by_drawing)
    if args.within:
        pool &= drawings_of(args.within)
    for used in args.apart_from:
        pool -= drawings_of(used)
    # A fixed order that does not depend on the dataset's order: by a hash of the name and the drawing.
    order = sorted(pool, key=lambda d: hashlib.sha1(f"{args.name}\x1f{d}".encode()).hexdigest())
    chosen = order[: args.n] if args.n else order
    questions = [q for d in chosen for q in by_drawing[d]]
    data = save_sample(config.SPLITS_DIR / f"{args.name}.json", args.name, "dev", args.name, questions)
    print(f"{args.name}: {data['n']} questions on {data['drawings']} drawings -> splits/{args.name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
