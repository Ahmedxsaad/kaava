"""The text printed on a raster plan, read once by a local OCR model and cached.

A raster plan has no entities to query, but it does carry text: room labels ("MH 15.0",
"Keittiö", "WC"). A small open-source OCR model (PP-OCR through RapidOCR, on the CPU:
nothing leaves the machine and a question costs nothing) turns the pixels into text
boxes; everything after that is rules.

Room labels are short and drawn at any quarter turn. A two-letter label read the wrong
way up still gives confident text ("MH" upside down reads "HW"), so guessing the
orientation is not good enough. Instead:

  1. the text detector finds the boxes (it does not care about orientation),
  2. each box is cut out and read at every quarter turn its shape allows,
  3. every reading is kept with its turn and the model's confidence.

Which reading is the label is decided by the caller's rules, not here. A plan is read
at two scales, because the detector misses a small label at one size and finds it at
the other; a label found at both is stronger evidence.

The readings are cached on disk (cache/ocr/, git-ignored), keyed by the file's content
hash, so a plan is read once however many questions are asked about it.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .. import config

INDEX_VERSION = 2

SCALES = (1.0, 1.5)        # relative to the base size
BASE_LONG_EDGE = (1000, 1800)   # the base size brings the long edge into this range
MAX_LONG_EDGE = 2000
MIN_TEXT_SCORE = 0.3       # keep weak readings too; the rules decide what to trust
# A reading this confident and this long is taken as it is; anything shorter or weaker
# is read again at the other turns.
SETTLED_SCORE = 0.9
SETTLED_LENGTH = 4
MODELS = "PP-OCRv6 det tiny + rec small (RapidOCR, onnxruntime, CPU)"

_engine = None
_engine_lock = threading.Lock()
_threads = 2


def cache_path(image_path: Path) -> Path:
    h = hashlib.sha1()
    with open(image_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return config.REPO_ROOT / "cache" / "ocr" / f"{h.hexdigest()[:16]}-v{INDEX_VERSION}.json"


def load_index(image_path: Path) -> dict:
    """The readings of a plan, from cache when possible."""
    path = cache_path(image_path)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    index = build_index(image_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return index


def is_cached(image_path: Path) -> bool:
    return cache_path(image_path).is_file()


# ----------------------------------------------------------------- reading


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType

        _engine = RapidOCR(params={
            "Global.log_level": "error",
            "Det.model_type": ModelType.TINY,
            "EngineConfig.onnxruntime.intra_op_num_threads": _threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })
        recogniser = _engine._load_rec_model()
        # Pad a batch of crops only to its own widest crop, not to a fixed 320 px:
        # short labels then cost a fraction of a full text line.
        recogniser.rec_image_shape = [3, 48, 48]
        recogniser.rec_batch_num = 8
    return _engine


def base_scale(width: int, height: int) -> float:
    long_edge = max(width, height)
    low, high = BASE_LONG_EDGE
    if long_edge < low:
        return low / long_edge
    if long_edge > high:
        return high / long_edge
    return 1.0


def turns_for(width: float, height: float) -> tuple[int, list[int]]:
    """Quarter turns (counter-clockwise) to read a box at: the first guess, then the rest.

    A wide box holds horizontal text (upright or upside down), a tall box vertical text
    (reading up or down); a near-square box, which is what a short label is, can be any.
    """
    ratio = width / max(1.0, height)
    if ratio >= 2.0:
        return 0, [2]
    if ratio <= 0.5:
        return 3, [1]
    return 0, [1, 2, 3]


def _settled(text: str, score: float) -> bool:
    return score >= SETTLED_SCORE and sum(ch.isalnum() for ch in text) >= SETTLED_LENGTH


def _crop(array, box):
    """The box cut out of the image as an upright rectangle, without any turn."""
    import cv2
    import numpy as np

    points = np.asarray(box, dtype=np.float32)
    width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if width < 4 or height < 4:
        return None
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(points, target)
    return cv2.warpPerspective(array, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE,
                               flags=cv2.INTER_CUBIC)


def _recognise(engine, crops: list) -> list[tuple[str, float]]:
    if not crops:
        return []
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    out = engine._load_rec_model()(TextRecInput(img=crops, return_word_box=False))
    return [(text, float(score)) for text, score in zip(out.txts, out.scores)]


def _read_scale(image, scale: float) -> dict:
    """One reading of the plan at one size. Boxes come back in pixels of the original file."""
    import numpy as np
    from PIL import Image

    width, height = round(image.width * scale), round(image.height * scale)
    scaled = image.resize((width, height), Image.LANCZOS) if (width, height) != image.size else image
    array = np.ascontiguousarray(np.asarray(scaled)[:, :, ::-1])  # the OCR models expect BGR
    started = time.perf_counter()
    with _engine_lock:
        engine = _get_engine()
        det = engine._load_det_model()(array)
        boxes = [] if det.boxes is None else list(det.boxes)
        det_scores = [] if det.boxes is None else list(det.scores)
        crops = [_crop(array, box) for box in boxes]
        plans = [turns_for(c.shape[1], c.shape[0]) if c is not None else (0, []) for c in crops]
        # First guess for every box, then the other turns for whatever is short or unsure.
        first = [(i, plans[i][0]) for i, c in enumerate(crops) if c is not None]
        first_reads = _recognise(engine, [np.ascontiguousarray(np.rot90(crops[i], k)) for i, k in first])
        reads: dict[int, list[dict]] = {i: [] for i in range(len(boxes))}
        again = []
        for (i, k), (text, score) in zip(first, first_reads):
            reads[i].append({"turn": 90 * k, "text": text, "score": round(score, 4)})
            if not _settled(text, score):
                again.extend((i, other) for other in plans[i][1])
        more_reads = _recognise(engine, [np.ascontiguousarray(np.rot90(crops[i], k)) for i, k in again])
        for (i, k), (text, score) in zip(again, more_reads):
            reads[i].append({"turn": 90 * k, "text": text, "score": round(score, 4)})
    seconds = time.perf_counter() - started
    items = []
    for i, box in enumerate(boxes):
        kept = [r for r in reads[i] if r["text"].strip() and r["score"] >= MIN_TEXT_SCORE]
        if not kept:
            continue
        items.append({
            "box": [[round(float(px) / scale, 1), round(float(py) / scale, 1)] for px, py in box],
            "det": round(float(det_scores[i]), 3),
            "reads": kept,
        })
    return {"scale": round(scale, 4), "seconds": round(seconds, 3), "crops_read": len(first) + len(again),
            "items": items}


def build_index(image_path: Path) -> dict:
    from PIL import Image

    with Image.open(image_path) as im:
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            image = Image.new("RGB", rgba.size, (255, 255, 255))
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = im.convert("RGB")
    base = base_scale(image.width, image.height)
    passes = []
    for relative in SCALES:
        scale = min(base * relative, MAX_LONG_EDGE / max(image.size))
        if passes and abs(scale - passes[-1]["scale"]) < 0.05:
            continue  # a large plan: the second size would be the same as the first
        passes.append(_read_scale(image, scale))
    return {
        "version": INDEX_VERSION,
        "models": MODELS,
        "width": image.width,
        "height": image.height,
        "seconds": round(sum(p["seconds"] for p in passes), 3),
        "passes": passes,
    }


# ----------------------------------------------------------------- bulk reading


def _warm_one(path: str) -> tuple[str, float, str | None]:
    try:
        started = time.perf_counter()
        load_index(Path(path))
        return path, time.perf_counter() - started, None
    except Exception as exc:  # a broken image must not stop the batch
        return path, 0.0, f"{type(exc).__name__}: {exc}"


def warm(paths: list[Path], processes: int = 5, progress=print) -> dict:
    """Read many plans in parallel processes so a later run only hits the cache."""
    import multiprocessing

    todo = [str(p) for p in paths if not is_cached(p)]
    progress(f"{len(paths)} plans, {len(paths) - len(todo)} already read, {len(todo)} to read "
             f"with {processes} processes")
    started = time.perf_counter()
    errors = []
    if todo:
        with multiprocessing.get_context("spawn").Pool(processes) as pool:
            for done, (path, _seconds, error) in enumerate(pool.imap_unordered(_warm_one, todo), 1):
                if error:
                    errors.append((path, error))
                if done % 50 == 0 or done == len(todo):
                    elapsed = time.perf_counter() - started
                    progress(f"  {done}/{len(todo)}  {elapsed:.0f}s  ({elapsed / done:.2f}s per plan)")
    return {"read": len(todo), "seconds": time.perf_counter() - started, "errors": errors}


def sample_paths(paths, limit: int | None = None) -> list[str]:
    """All paths, or a fixed pseudo-random `limit` of them: the same plans on every call."""
    ordered = sorted(paths, key=lambda p: hashlib.sha1(p.encode("utf-8")).hexdigest())
    return ordered[:limit] if limit else sorted(ordered)


def main(argv: list[str] | None = None) -> int:
    """Pre-read the raster plans of a split: python -I -m blueprints.engines.ocr_index --split dev"""
    import argparse

    from ..data import Dataset
    from ..split import SPLITS, Split

    parser = argparse.ArgumentParser(description="read raster plans with OCR into the cache")
    parser.add_argument("--split", default="dev", choices=[s for s in SPLITS if s != "test"])
    parser.add_argument("--source", default="cubicasa5k")
    parser.add_argument("--limit", type=int, default=None,
                        help="only N plans: a fixed pseudo-random sample (ordered by a hash of the path)")
    parser.add_argument("--processes", type=int, default=5)
    args = parser.parse_args(argv)

    ds = Dataset(config.dataset_dir())
    split = Split.load()
    questions = split.select(ds.questions, args.split)
    paths = sample_paths({q.path for q in questions if q.source == args.source}, args.limit)
    result = warm([ds.abspath(p) for p in paths], processes=args.processes)
    print(f"read {result['read']} plans in {result['seconds']:.0f}s, {len(result['errors'])} errors")
    for path, error in result["errors"][:10]:
        print("  ", path, error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
