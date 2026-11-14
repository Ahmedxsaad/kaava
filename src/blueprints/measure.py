"""Measuring rooms on a plan that prints no dimensions: the model points, the pixels are measured.

A plan drawn to scale carries its sizes in its geometry, not in text. A vision model
asked for a room's area looks at the picture and estimates; nothing shows what it
measured. Here it only says which room, with a rough box around it. Then the code:

  1. reads the sheet's scale from its graphic scale bar: the labels come from the text
     index ("0", "10m", "20m"), the bar's two ends from the pixels above them;
  2. moves each side of the model's box onto the inner face of the wall that runs along
     it (the walls are the thick dark strokes of the drawing);
  3. measures the snapped rectangle: area, perimeter, and for baseboards the perimeter
     less the door openings, which are the stretches of a wall where nothing is drawn.

So the number comes from wall pixels and the scale bar, and the evidence is the rectangle,
the openings and the bar, drawn on the sheet. It measures rectangular rooms on plans with
solid walls; anything else is declined rather than guessed. Answers are state "check".
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DARK = 110             # a pixel darker than this is ink
REACH_M = 1.3          # how far from the model's box a wall face is looked for
MIN_COVER = 0.12       # a wall along a side covers at least this share of it (the rest may be windows and doors)
DOOR_M = (0.55, 2.7)   # an opening in a wall of this width is a door opening
THIN_M = 0.09          # strokes thinner than this are not walls (furniture, door swings, hatching)
MULLION_M = 0.14       # two openings parted by less than this are one (the leaves of a double door)

_LABEL = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(mm|cm|m|ft|'|′)?$", re.IGNORECASE)
_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0, "ft": 0.3048, "'": 0.3048, "′": 0.3048}


@lru_cache(maxsize=4)
def _grey(path_str: str):
    import numpy as np
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path_str) as im:
        return np.asarray(im.convert("L"))


def scale_bar(path: Path, index: dict) -> dict | None:
    """Pixels per metre from the sheet's graphic scale bar, or None when no bar is found.

    The bar's labels are texts on one row: a 0 and at least one length with a unit ("10m", "20m").
    The bar itself is the ink in the band above them; its ends are the 0 mark and the last mark.
    """
    import numpy as np

    grey = _grey(str(path))
    labels = []
    for item in index["items"]:
        match = _LABEL.match(item["text"].strip())
        if match:
            labels.append((item, float(match.group(1).replace(",", ".")), (match.group(2) or "").lower()))
    best = None
    for zero, value, unit in labels:
        if value != 0:
            continue
        zy = (zero["box"][1] + zero["box"][3]) / 2
        height = zero["box"][3] - zero["box"][1]
        row = [(item, v, u) for item, v, u in labels
               if abs((item["box"][1] + item["box"][3]) / 2 - zy) < 0.6 * height and item["box"][0] > zero["box"][0]]
        units = {u for _, _, u in row if u}
        if not row or len(units) != 1:
            continue
        unit_name = units.pop()
        last, last_value, _ = max(row, key=lambda entry: entry[1])
        # The band above the labels, from a little left of the 0 to a little right of the last label.
        x0 = int(max(0, zero["box"][0] - 2 * height))
        x1 = int(min(grey.shape[1], last["box"][2] + 2 * height))
        y1 = int(min(zero["box"][1], last["box"][1]) - 2)
        y0 = int(max(0, y1 - 3 * height))
        ink = grey[y0:y1, x0:x1] < 128
        columns = np.nonzero(ink.sum(axis=0) >= 2)[0]
        if len(columns) < 2:
            continue
        left, right = x0 + int(columns.min()), x0 + int(columns.max())
        length_px = right - left
        length_m = last_value * _METRES[unit_name]
        if length_px < 50 or length_m <= 0:
            continue
        # A check: the marks between the ends should stand where their labels say (10 m halfway to 20 m).
        agree = 0
        for item, v, _ in row:
            expected = left + length_px * v / last_value
            centre = (item["box"][0] + item["box"][2]) / 2
            agree += abs(centre - expected) < 0.06 * length_px + height
        rows = np.nonzero(ink[:, columns.min():columns.max() + 1].sum(axis=1))[0]
        candidate = {"px_per_m": length_px / length_m, "length_m": length_m, "length_px": length_px,
                     "label": f"{last_value:g} {unit_name}".replace(" '", "'"),
                     "bbox": [left, y0 + int(rows.min()), right, y0 + int(rows.max())],
                     "labels_in_place": agree, "labels": len(row)}
        if best is None or candidate["length_px"] > best["length_px"]:
            best = candidate
    return best


@lru_cache(maxsize=4)
def _masks(path_str: str, kernel: int):
    """Ink, and the ink that is thick enough to be a wall."""
    import cv2
    import numpy as np

    dark = (_grey(path_str) < DARK).astype(np.uint8)
    thick = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((kernel, kernel), np.uint8))
    return dark, thick


def _wall_along(cover, base_index: int, towards_room: int) -> tuple[int, int, float] | None:
    """The wall band nearest the model's side: (first index, last index, cover), or None.

    `cover[i]` is the share of the side that is wall at offset i; a band is a run of offsets
    where that share stays above the threshold.
    """
    peak = float(cover.max()) if len(cover) else 0.0
    if peak < MIN_COVER:
        return None
    level = max(MIN_COVER, 0.5 * peak)
    bands, start = [], None
    for i, value in enumerate(cover):
        if value >= level and start is None:
            start = i
        elif value < level and start is not None:
            bands.append((start, i - 1))
            start = None
    if start is not None:
        bands.append((start, len(cover) - 1))
    # The band whose room-side face is nearest to where the model put the side.
    face = (lambda band: band[1] + 1) if towards_room > 0 else (lambda band: band[0] - 1)
    first, last = min(bands, key=lambda band: abs(face(band) - base_index))
    return first, last, float(cover[first:last + 1].mean())


def measure_room(path: Path, box: list[float], px_per_m: float) -> dict:
    """Snap a rough box onto the inner faces of the walls around a rectangular room and measure it.

    Returns {"ok": bool, "why": str, "bbox": [x0, y0, x1, y1], "width_m", "height_m", "area_m2",
    "perimeter_m", "openings": [{"bbox", "width_m", "side"}], "sides": {side: wall cover}}.
    """
    import numpy as np

    kernel = max(3, round(px_per_m * THIN_M)) | 1  # odd, so that removing thin strokes does not shift the walls
    dark, thick = _masks(str(path), kernel)
    height, width = dark.shape
    reach = max(12, round(px_per_m * REACH_M))
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(reach, x0), max(reach, y0), min(width - reach - 1, x1), min(height - reach - 1, y1)
    if x1 - x0 < 0.6 * px_per_m or y1 - y0 < 0.6 * px_per_m:
        return {"ok": False, "why": "the box is smaller than a room"}
    inset_x, inset_y = int(0.1 * (x1 - x0)), int(0.1 * (y1 - y0))
    faces, bands, cover = {}, {}, {}
    for side in ("left", "right", "top", "bottom"):
        if side in ("left", "right"):
            base = x0 if side == "left" else x1
            profile = thick[y0 + inset_y:y1 - inset_y, base - reach:base + reach + 1].mean(axis=0)
        else:
            base = y0 if side == "top" else y1
            profile = thick[base - reach:base + reach + 1, x0 + inset_x:x1 - inset_x].mean(axis=1)
        towards_room = 1 if side in ("left", "top") else -1
        found = _wall_along(profile, reach, towards_room)
        if found is None:
            return {"ok": False, "why": f"no wall found along the {side} side of the room"}
        first, last, share = found
        bands[side] = (base - reach + first, base - reach + last)   # the wall's own extent across its thickness
        faces[side] = bands[side][1] + 1 if towards_room > 0 else bands[side][0] - 1
        cover[side] = round(share, 2)
    fx0, fx1, fy0, fy1 = faces["left"], faces["right"], faces["top"], faces["bottom"]
    if fx1 - fx0 < 0.6 * px_per_m or fy1 - fy0 < 0.6 * px_per_m:
        return {"ok": False, "why": "the walls found do not enclose a room"}
    # Walls running through the inside mean this is not one rectangular room.
    margin = kernel + 2
    inside = thick[fy0 + margin:fy1 - margin, fx0 + margin:fx1 - margin]
    if inside.size and inside.mean() > 0.012:
        return {"ok": False, "why": "walls run through the box: the room is not a plain rectangle"}
    width_m, height_m = (fx1 - fx0 + 1) / px_per_m, (fy1 - fy0 + 1) / px_per_m

    # Door openings: stretches of a wall where nothing at all is drawn across its thickness.
    openings = []
    low, high = DOOR_M[0] * px_per_m, DOOR_M[1] * px_per_m
    for side in ("left", "right", "top", "bottom"):
        a, b = bands[side]
        if side in ("left", "right"):
            empty = dark[fy0:fy1 + 1, a:b + 1].sum(axis=1) == 0
            origin = fy0
        else:
            empty = dark[a:b + 1, fx0:fx1 + 1].sum(axis=0) == 0
            origin = fx0
        # The two leaves of a double door are parted by a mark a few pixels wide: one opening, not two.
        joined, run = [], None
        for i, gap in enumerate(np.append(empty, False)):
            if gap and run is None:
                run = i
            elif not gap and run is not None:
                if joined and run - joined[-1][1] <= MULLION_M * px_per_m:
                    joined[-1] = (joined[-1][0], i - 1)
                else:
                    joined.append((run, i - 1))
                run = None
        for first, last in joined:
            length = last - first + 1
            if low <= length <= high:
                s, e = origin + first, origin + last
                bbox = [a, s, b, e] if side in ("left", "right") else [s, a, e, b]
                openings.append({"bbox": [float(v) for v in bbox], "width_m": length / px_per_m, "side": side})
    return {"ok": True, "why": "", "bbox": [float(fx0), float(fy0), float(fx1), float(fy1)],
            "width_m": width_m, "height_m": height_m, "area_m2": width_m * height_m,
            "perimeter_m": 2 * (width_m + height_m), "openings": openings, "sides": cover}
