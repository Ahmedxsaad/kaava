"""Does preparing a scan with Cloudinary (AI enlargement, colour) help the label engine?

Runs the label engine on the same dev scans twice, as they came and as Cloudinary
prepares them, and logs both runs in results/. Dev split only.

    .venv/bin/python -I scripts/eval_prepared_scans.py --plans 40
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from blueprints import cloud, config  # noqa: E402
from blueprints.cli import _mix_weights  # noqa: E402
from blueprints.data import Dataset  # noqa: E402
from blueprints.engines import raster_labels  # noqa: E402
from blueprints.harness import finalize_run, format_table, new_run_id, run_eval  # noqa: E402
from blueprints.split import Split  # noqa: E402


class Prepared:
    """A dataset whose scans are read as Cloudinary prepared them."""

    def __init__(self, dataset: Dataset, prepared: dict[str, Path]):
        self._ds, self._prepared = dataset, prepared

    def __getattr__(self, name):
        return getattr(self._ds, name)

    def abspath(self, rel: str) -> Path:
        return self._prepared.get(rel) or self._ds.abspath(rel)


def main() -> int:
    from PIL import Image

    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=int, default=40)
    args = parser.parse_args()
    load_dotenv(config.REPO_ROOT / ".env")
    if not cloud.configured():
        raise SystemExit("Cloudinary is not configured in .env")
    ds = Dataset(config.DATA_DIR / "junction-dataset-preview")
    split = Split.load()
    pool = [q for q in split.select(ds.questions, "dev")
            if q.source == "cubicasa5k" and raster_labels.room_kind_in(q.question)]
    paths = sorted({q.path for q in pool}, key=lambda p: hashlib.sha1(p.encode()).hexdigest())
    small = []
    for path in paths:  # the scans the preparation would enlarge
        with Image.open(ds.abspath(path)) as im:
            if max(im.size) < cloud.SMALL_EDGE:
                small.append((path, im.size))
        if len(small) >= args.plans:
            break
    out_dir = config.REPO_ROOT / "cache" / "prepared"
    out_dir.mkdir(parents=True, exist_ok=True)

    def prepare(item) -> tuple[str, Path | None]:
        path, (w, h) = item
        ident = hashlib.sha1(path.encode()).hexdigest()[:12]
        target = out_dir / f"{ident}.png"
        if not target.is_file():
            try:
                data, _ = cloud.prepare(ds.abspath(path), f"eval-{ident}", w, h)
                with Image.open(io.BytesIO(data)) as better:
                    better.convert("RGB").save(target)
            except Exception as exc:
                print(f"  could not prepare {path}: {type(exc).__name__}: {str(exc)[:100]}")
                return path, None
        return path, target

    with ThreadPoolExecutor(4) as workers:
        prepared = {path: target for path, target in workers.map(prepare, small) if target}
    print(f"{len(prepared)} of {len(small)} scans prepared by Cloudinary")
    questions = [q for q in pool if q.path in prepared]
    sample_name = f"dev_scans_prepared_{len(prepared)}"
    weights, mix_note = _mix_weights(ds)
    for method, dataset in ((raster_labels.METHOD, ds),
                            (f"{raster_labels.METHOD}@cloudinary-prepared", Prepared(ds, prepared))):
        run_id = new_run_id(method)
        run_dir = config.RESULTS_DIR / "runs" / run_id
        result = run_eval(method=method, answer_fn=lambda q, _ds, d=dataset: raster_labels.answer(q, d), dataset=ds,
                          questions=questions, split_of=split.of, run_dir=run_dir, workers=4,
                          max_consecutive_errors=25, progress=lambda _m: None)
        summary = finalize_run(run_id=run_id, run_dir=run_dir, method=method, result=result, dataset=ds,
                               split_name="dev", sample_name=sample_name, weights=weights,
                               run_config={"mix": mix_note, "steps": [str(s) for s in cloud.steps_for(600, 600)]},
                               notes="label engine on the same dev scans, as they came or as Cloudinary prepared them",
                               log=result["complete"])
        print(f"\n== {method}  ({run_id})")
        print(format_table(summary["metrics"]))
        levels: dict[float, list[int]] = {}
        for row in result["rows"]:
            if row.get("pred") is not None:
                level = levels.setdefault(row.get("confidence"), [0, 0])
                level[0] += 1
                level[1] += row["status"] == "correct"
        for score in sorted(levels, reverse=True):
            n, ok = levels[score]
            print(f"   score {score}: {n} answers, {ok} right ({100 * ok / n:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
