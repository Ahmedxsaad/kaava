"""Every text printed on a sheet, with its place, read once and cached.

A drawing sheet carries most of its quantities as text: dimension strings, schedules,
area tables, equipment tags. A vision model sees a large sheet scaled down some five
times, where a 2 mm dimension figure is a smudge. So the texts are read by a program at
the sheet's own size:

  * a PDF with a text layer gives its texts exactly, from the file itself;
  * anything else (a scan, a PNG of a sheet, a PDF that is only a picture) is read by
    the local OCR model, tile by tile, at close to full resolution. Nothing leaves the
    machine and it costs nothing.

The result is a list of {id, text, box}: the sheet's text index. The box is in the units
of the source file (pixels of an image, points of a PDF page), the same space evidence
is drawn in.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from . import config

VERSION = 2
PAGED = (".pdf",)
# OCR works on the sheet scaled so that its long edge is at most this, cut into tiles.
WORK_LONG_EDGE = 7200
TILE = 1500
OVERLAP = 220
MIN_SCORE = 0.5
# A PDF page with fewer texts than this is taken to be a picture of a sheet, and is read by OCR.
MIN_PDF_TEXTS = 12
PDF_OCR_ZOOM_EDGE = 6000

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _cache_file(path: Path, page: int) -> Path:
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return config.REPO_ROOT / "cache" / "sheet_text" / f"{digest.hexdigest()[:16]}-p{page}-v{VERSION}.json"


def read(path: Path, page: int = 0) -> dict:
    """The sheet's text index, from cache when possible.

    {"source": "pdf" | "ocr", "width", "height", "items": [{"id", "text", "box": [x0, y0, x1, y1]}], "seconds"}
    """
    store = _cache_file(path, page)
    with _guard:
        lock = _locks.setdefault(str(store), threading.Lock())
    with lock:
        if store.is_file():
            return json.loads(store.read_text(encoding="utf-8"))
        started = time.perf_counter()
        original = path.with_suffix(".pdf")  # a picture made from a PDF kept beside it: the PDF's texts are exact
        if path.suffix.lower() in PAGED:
            index = _read_pdf(path, page)
        elif original.is_file():
            index = _read_pdf(original, page)
            if index["source"] == "pdf":  # from the page's points to the picture's pixels
                from PIL import Image

                with Image.open(path) as im:
                    fx, fy = im.width / index["width"], im.height / index["height"]
                for item in index["items"]:
                    x0, y0, x1, y1 = item["box"]
                    item["box"] = [round(x0 * fx, 1), round(y0 * fy, 1), round(x1 * fx, 1), round(y1 * fy, 1)]
                index["width"], index["height"] = im.width, im.height
            else:
                index = _read_image(_open_image(path))
        else:
            index = _read_image(_open_image(path))
        index["seconds"] = round(time.perf_counter() - started, 2)
        index["items"] = _in_reading_order(index["items"])
        for number, item in enumerate(index["items"]):
            item["id"] = number
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(index), encoding="utf-8")
        return index


def _in_reading_order(items: list[dict]) -> list[dict]:
    """Top to bottom in bands of about one text height, left to right inside a band."""
    if not items:
        return items
    heights = sorted(item["box"][3] - item["box"][1] for item in items)
    band = max(1.0, heights[len(heights) // 2])
    return sorted(items, key=lambda item: (round((item["box"][1] + item["box"][3]) / 2 / band), item["box"][0]))


# ----------------------------------------------------------------- a PDF's own text


def _read_pdf(path: Path, page_number: int) -> dict:
    import pymupdf

    with pymupdf.open(path) as doc:
        page = doc[min(page_number, len(doc) - 1)]
        width, height = page.rect.width, page.rect.height
        items = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                # Spans of one line that touch are one text (a cell, a dimension string).
                run: dict | None = None
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        run = None
                        continue
                    box = list(span["bbox"])
                    size = span.get("size", 8.0)
                    if run is not None and box[0] - run["box"][2] < 0.6 * size and abs(box[1] - run["box"][1]) < 0.6 * size:
                        run["text"] += text
                        run["box"] = [min(run["box"][0], box[0]), min(run["box"][1], box[1]),
                                      max(run["box"][2], box[2]), max(run["box"][3], box[3])]
                    else:
                        run = {"text": text, "box": box}
                        items.append(run)
        items = [{"text": " ".join(item["text"].split()), "box": [round(v, 2) for v in item["box"]]}
                 for item in items if item["text"].strip()]
        if len(items) >= MIN_PDF_TEXTS:
            return {"source": "pdf", "width": width, "height": height, "items": items}
        # A picture of a sheet inside a PDF: render it and read it like any image.
        zoom = PDF_OCR_ZOOM_EDGE / max(width, height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        from PIL import Image

        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    index = _read_image(image)
    for item in index["items"]:  # back to the page's points
        item["box"] = [round(v / zoom, 2) for v in item["box"]]
    return {**index, "width": width, "height": height}


# ----------------------------------------------------------------- a picture, read by OCR


def _open_image(path: Path):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None  # a sheet at 600 dpi is larger than PIL's default limit; the file is ours to read
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            image = Image.new("RGB", rgba.size, (255, 255, 255))
            image.paste(rgba, mask=rgba.getchannel("A"))
            return image
        return im.convert("RGB")


def _read_image(image) -> dict:
    from PIL import Image

    from .engines import ocr_index

    scale = min(1.0, WORK_LONG_EDGE / max(image.size))
    work = image if scale == 1.0 else image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    stride = TILE - OVERLAP
    found: list[dict] = []
    for top in range(0, max(1, work.height - OVERLAP), stride):
        for left in range(0, max(1, work.width - OVERLAP), stride):
            tile = work.crop((left, top, min(work.width, left + TILE), min(work.height, top + TILE)))
            reading = ocr_index._read_scale(tile, 1.0)
            for item in reading["items"]:
                best = _best_reading(item["reads"])
                text = " ".join(best["text"].split())
                if best["score"] < MIN_SCORE or not text:
                    continue
                xs = [p[0] for p in item["box"]]
                ys = [p[1] for p in item["box"]]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                # A text cut by the tile's edge is read whole in the neighbouring tile: leave the cut one out.
                cut = ((x0 < 3 and left > 0) or (y0 < 3 and top > 0)
                       or (x1 > tile.width - 3 and left + TILE < work.width)
                       or (y1 > tile.height - 3 and top + TILE < work.height))
                if cut:
                    continue
                found.append({"text": text, "score": round(best["score"], 3), "turn": best["turn"],
                              "box": [round((left + x0) / scale, 1), round((top + y0) / scale, 1),
                                      round((left + x1) / scale, 1), round((top + y1) / scale, 1)]})
    return {"source": "ocr", "width": image.width, "height": image.height, "items": _without_repeats(found)}


# Text on a drawing reads from the bottom or from the right-hand side of the sheet, never upside
# down. A figure such as 600 read the wrong way up is 009, and the OCR is as sure of one as of the
# other: so a reading in a conventional direction wins unless the other is clearly better.
CONVENTIONAL_TURNS = (0, 270)
TURN_MARGIN = 0.12


def _best_reading(reads: list[dict]) -> dict:
    return max(reads, key=lambda r: r["score"] + (TURN_MARGIN if r["turn"] in CONVENTIONAL_TURNS else 0.0))


def _overlap(a: list[float], b: list[float]) -> float:
    """Shared area over the smaller box's area."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / max(1e-6, smaller)


def _without_repeats(items: list[dict]) -> list[dict]:
    """A text in the overlap of two tiles is read twice: keep the better reading."""
    items = sorted(items, key=lambda item: -item["score"] * (1 + 0.02 * len(item["text"])))
    kept: list[dict] = []
    cell = 400.0
    grid: dict[tuple[int, int], list[dict]] = {}
    for item in items:
        box = item["box"]
        cells = {(int(x // cell), int(y // cell)) for x in (box[0], box[2]) for y in (box[1], box[3])}
        if any(_overlap(box, other["box"]) > 0.6 for c in cells for other in grid.get(c, [])):
            continue
        kept.append(item)
        for c in cells:
            grid.setdefault(c, []).append(item)
    return kept


def at(index: dict, box: list[float], pad: float = 0.0) -> list[dict]:
    """The texts whose centre lies in `box` (source units), nearest its centre first."""
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    inside = [item for item in index["items"]
              if x0 <= (item["box"][0] + item["box"][2]) / 2 <= x1 and y0 <= (item["box"][1] + item["box"][3]) / 2 <= y1]
    return sorted(inside, key=lambda item: ((item["box"][0] + item["box"][2]) / 2 - cx) ** 2
                                           + ((item["box"][1] + item["box"][3]) / 2 - cy) ** 2)
