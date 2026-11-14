"""Counting small symbols on a large sheet: tile by tile, at a size where they can be seen.

A sheet of a drawing set is some 10,000 pixels wide; a vision model looks at about
2,500. A door on a school plan is then a dozen pixels, and a model asked to mark a
hundred of them in one look loses count or runs out of words. So the part of the sheet
to search is cut into overlapping tiles, the model boxes the symbols in each tile at
full size, and the code puts the boxes back on the sheet, drops the ones two tiles
both found, and counts what is left. Every counted symbol is a box on the sheet.

Tiles with nothing drawn on them are not sent. Answers are state "check".
"""

from __future__ import annotations

import io
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .pointing import GRID, SCHEMA_BOXES, SYSTEM_BOXES
from .render import Rendered

TILE = 1500          # pixels of the sheet in one tile
OVERLAP = 260        # shared with the next tile, so that a symbol on a seam is whole in one of them
MAX_TILES = 16
MIN_INK = 0.004      # a tile with less than this share of dark pixels is empty paper
SAME = 0.35          # two boxes are the same symbol when they share this much of the smaller one
WORKERS = 4

SYSTEM_TILE = SYSTEM_BOXES + """
- This image is one part of a larger sheet. Mark an item that is cut by the edge of the image only if more than half of it is inside the image.
- Mark only items drawn on the plan itself, not the ones in a legend, a key plan or a detail's title."""


def _open(path: Path):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        return im.convert("RGB")


def _starts(low: float, high: float) -> list[int]:
    """Where tiles start along one side so that they cover [low, high] with the wanted overlap."""
    span = high - low
    if span <= TILE:
        return [int(low)]
    count = math.ceil((span - OVERLAP) / (TILE - OVERLAP))
    step = (span - TILE) / (count - 1)
    return [int(low + i * step) for i in range(count)]


def plan(width: int, height: int, region: list[float] | None) -> list[tuple[int, int, int, int]]:
    """The tiles (x0, y0, x1, y1) over `region` of a sheet, enlarged if there would be too many."""
    global_tile = TILE
    x0, y0, x1, y1 = region or [0, 0, width, height]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    tiles = [(x, y, min(width, x + TILE), min(height, y + TILE)) for y in _starts(y0, y1) for x in _starts(x0, x1)]
    if len(tiles) > MAX_TILES:  # a very large region: fewer, larger tiles (the model sees them scaled down)
        scale = math.sqrt(len(tiles) / MAX_TILES) * 1.05
        size = int(global_tile * scale)
        cols = max(1, math.ceil((x1 - x0 - OVERLAP) / (size - OVERLAP)))
        rows = max(1, math.ceil((y1 - y0 - OVERLAP) / (size - OVERLAP)))
        xs = [int(x0 + i * ((x1 - x0 - size) / max(1, cols - 1))) for i in range(cols)] if cols > 1 else [int(x0)]
        ys = [int(y0 + i * ((y1 - y0 - size) / max(1, rows - 1))) for i in range(rows)] if rows > 1 else [int(y0)]
        tiles = [(x, y, min(width, x + size), min(height, y + size)) for y in ys for x in xs]
    return tiles


def _shared(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1e-6, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def merge(boxes: list[dict]) -> list[dict]:
    """One box per symbol: of two boxes on the same symbol (found in two tiles), the one further from its tile's edge stays."""
    kept: list[dict] = []
    for box in sorted(boxes, key=lambda b: -b["margin"]):
        if any(_shared(box["bbox"], other["bbox"]) > SAME for other in kept):
            continue
        kept.append(box)
    return sorted(kept, key=lambda b: (round(b["bbox"][1] / 300), b["bbox"][0]))


def agree(first: list[dict], second: list[dict]) -> tuple[list[dict], list[dict]]:
    """Two passes over the same tiles: the boxes both passes drew, and the ones only one of them drew."""
    both, once, taken = [], [], set()
    for box in first:
        match = next((i for i, other in enumerate(second)
                      if i not in taken and _shared(box["bbox"], other["bbox"]) > SAME), None)
        if match is None:
            once.append(box)
        else:
            taken.add(match)
            both.append(box)
    once += [other for i, other in enumerate(second) if i not in taken]
    return both, once


def count(vlm, path: Path, question: str, region: list[float] | None = None, note=None) -> dict:
    """Box what the question asks about in every tile of `region` (pixels of the file) and merge.

    Returns {"boxes": [evidence box, ...], "tiles": n, "sent": n, "cost_usd", "latency_ms", "errors": [...]}.
    """
    import numpy as np

    sheet = _open(path)
    tiles = plan(sheet.width, sheet.height, region)
    grey = np.asarray(sheet.convert("L"))
    jobs = []
    for (x0, y0, x1, y1) in tiles:
        if (grey[y0:y1, x0:x1] < 128).mean() < MIN_INK:
            continue  # empty paper
        crop = sheet.crop((x0, y0, x1, y1))
        longest = max(crop.size)
        scale = min(1.0, vlm.max_edge / longest)
        if scale < 1.0:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
        jobs.append(((x0, y0, x1, y1), Rendered(buffer.getvalue(), "image/png", crop.width, crop.height, 0, scale)))

    started = time.perf_counter()

    done = [0]
    if note:
        note(f"Boxing the symbols tile by tile at full size: 0 of {len(jobs)} tiles")

    def ask(job):
        tile, image = job
        reply = vlm.ask(SYSTEM_TILE, [image], f"Question: {question}", SCHEMA_BOXES)
        done[0] += 1
        if note:
            note(f"Boxing the symbols tile by tile at full size: {done[0]} of {len(jobs)} tiles")
        return tile, reply

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        replies = list(pool.map(ask, jobs))

    found, errors, cost = [], [], 0.0
    for (x0, y0, x1, y1), reply in replies:
        cost += reply.cost_usd or 0.0
        if reply.error or reply.data is None:
            errors.append(reply.error or reply.no_answer or "no usable reply")
            continue
        for item in reply.data.get("items") or []:
            try:
                by0, bx0, by1, bx1 = (float(v) for v in item["box_2d"])
            except (KeyError, TypeError, ValueError):
                continue
            (bx0, bx1), (by0, by1) = sorted((bx0, bx1)), sorted((by0, by1))
            box = [x0 + bx0 / GRID * (x1 - x0), y0 + by0 / GRID * (y1 - y0),
                   x0 + bx1 / GRID * (x1 - x0), y0 + by1 / GRID * (y1 - y0)]
            # How far the box is from the nearest edge of its tile: a box in the middle was seen whole.
            margin = min(box[0] - x0, box[1] - y0, x1 - box[2], y1 - box[3])
            found.append({"kind": "box", "space": "image_px", "bbox": [round(v, 1) for v in box],
                          "label": str(item.get("label", ""))[:60], "margin": margin})
    boxes = merge(found)
    for box in boxes:
        box.pop("margin", None)
    return {"boxes": boxes, "tiles": len(tiles), "sent": len(jobs), "cost_usd": cost,
            "latency_ms": (time.perf_counter() - started) * 1000, "errors": errors, "found_before_merge": len(found)}
