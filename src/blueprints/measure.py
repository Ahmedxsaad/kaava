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


SCAN_SIDE = 2500       # an image no larger than this is a scanned plan, not a sheet of a drawing set


def is_scan(path: Path) -> bool:
    return max(_grey(str(path)).shape) <= SCAN_SIDE


@lru_cache(maxsize=4)
def _masks(path_str: str, kernel: int, how: str = "ink"):
    """Ink, and the ink that is wall.

    how="ink": the walls are the ink thick enough to be one (a sheet of a drawing set, black on white).
    how="scan": a scanned plan is drawn in any of several ways (coloured walls, grey walls, walls as
    two thin lines with hatching between): the walls are the ones scan_plan.py finds, the same that
    stand in its 3D view. Falls back to the ink when that finds none.
    """
    import cv2
    import numpy as np

    grey = _grey(path_str)
    if how == "scan" and max(grey.shape) <= SCAN_SIDE:
        from . import scan_plan

        plan = scan_plan.load(Path(path_str))
        if plan.get("ok"):
            walls = np.zeros(grey.shape, np.uint8)
            # Largest first, each with its holes: a wall standing inside a room is drawn after the room is cut out.
            for poly in sorted(plan["polygons"], key=lambda p: -cv2.contourArea(np.array(p["outer"], np.float32))):
                cv2.fillPoly(walls, [np.array(poly["outer"], np.int32)], 1)
                for hole in poly["holes"]:
                    cv2.fillPoly(walls, [np.array(hole, np.int32)], 0)
            return (grey < scan_plan.INK_BELOW).astype(np.uint8), walls
    dark = (grey < DARK).astype(np.uint8)
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


def measure_room(path: Path, box: list[float], px_per_m: float, how: str = "ink") -> dict:
    """Snap a rough box onto the inner faces of the walls around a rectangular room and measure it.

    Returns {"ok": bool, "why": str, "bbox": [x0, y0, x1, y1], "width_m", "height_m", "area_m2",
    "perimeter_m", "openings": [{"bbox", "width_m", "side"}], "sides": {side: wall cover}}.
    """
    import numpy as np

    kernel = max(3, round(px_per_m * THIN_M)) | 1  # odd, so that removing thin strokes does not shift the walls
    dark, thick = _masks(str(path), kernel, how)
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


# ----------------------------------------------------------------- a room of any shape, holes cut out

SEAL_M = (0.6, 2.75)     # the square that must fit in the room: an opening narrower than it is sealed
COLUMN_MIN_M2 = 0.03     # a solid speck smaller than this inside a room is ink, not a column
LEAK = 0.12              # this share of the room found lying outside the model's box means it leaked out
WHOLE = 0.7              # the square should reach this share of the floor in the model's box
NOOK = 0.75              # a nook's mouth is at least this share of its width; a narrower mouth is a door


WINDOW_M = 6.0           # a glazed stretch of wall is at most this long between two pieces of solid wall
WINDOW_LINE = 0.8        # ...and a drawn line runs along at least this share of it


def _glazing(walls, ink, px_per_m: float):
    """The stretches of wall that are drawn as thin lines: windows and glazed screens.

    A window is a gap between two pieces of solid wall, in line with them, no thicker than a wall,
    with a line drawn along it from one piece to the other. A door opening is the same gap with
    nothing drawn across it, and stays open. Without this a glazed wall is an open side of the room.
    """
    import cv2
    import numpy as np

    out = np.zeros_like(walls)
    reach = max(3, int(WINDOW_M * px_per_m))
    thick_most = 0.7 * px_per_m      # a wall is not thicker than this
    short_least = 0.25 * px_per_m    # a gap shorter than this is not a window
    for along_x in (True, False):
        kernel = np.ones((1, reach), np.uint8) if along_x else np.ones((reach, 1), np.uint8)
        gaps = ((cv2.morphologyEx(walls, cv2.MORPH_CLOSE, kernel) == 1) & (walls == 0)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(gaps, connectivity=4)
        for n in range(1, count):
            left, top, w, h, _ = stats[n]
            run, across = (w, h) if along_x else (h, w)
            if across > thick_most or run < short_least:
                continue
            patch = ink[top:top + h, left:left + w]
            lines = patch.sum(axis=1 if along_x else 0)   # ink along each line that runs the length of the gap
            if lines.size and lines.max() >= WINDOW_LINE * run:
                out[top:top + h, left:left + w][labels[top:top + h, left:left + w] == n] = 1
    return out


def _shut_door(part, mouth_wide: bool, ys, xs, free, px_per_m: float) -> bool:
    """Whether a shallow recess in a wall is a door opening with its leaf drawn across the far side.

    A doorway whose door is drawn shut ends at a line, not at a wall: just behind that line the
    floor of the next room begins. A real niche ends at a wall.
    """
    import numpy as np

    py, px = np.nonzero(part)
    deep = (py.max() - py.min() if mouth_wide else px.max() - px.min()) + 1
    mouth = (px.max() - px.min() if mouth_wide else py.max() - py.min()) + 1
    if deep > 0.5 * px_per_m or not DOOR_M[0] * px_per_m <= mouth <= DOOR_M[1] * px_per_m:
        return False
    look = max(6, int(0.25 * px_per_m))  # how far behind the recess to look
    trim = mouth // 5
    if mouth_wide:
        room_above = ys.mean() < (py.min() + py.max()) / 2
        rows = range(py.max() + 1, min(free.shape[0], py.max() + 1 + look)) if room_above else range(max(0, py.min() - look), py.min())
        behind = free[list(rows), px.min() + trim:px.max() + 1 - trim] if len(rows) else np.zeros((0, 0))
        clear = behind.mean(axis=1) if behind.size else np.zeros(0)
    else:
        room_left = xs.mean() < (px.min() + px.max()) / 2
        cols = range(px.max() + 1, min(free.shape[1], px.max() + 1 + look)) if room_left else range(max(0, px.min() - look), px.min())
        behind = free[py.min() + trim:py.max() + 1 - trim, list(cols)] if len(cols) else np.zeros((0, 0))
        clear = behind.mean(axis=0) if behind.size else np.zeros(0)
    return bool(clear.size and (clear > 0.9).sum() >= max(2, look // 3))


def room_shape(path: Path, box: list[float], px_per_m: float, texts: list[dict] | None = None, how: str = "ink") -> dict:
    """The floor of the room the model's box is around, whatever its shape, with what stands in it cut out.

    A rectangle fits a rectangular room and nothing else: an L-shaped room, a corridor that runs
    around a lift, a hall with columns in it all come out too large. Here the room is the free
    space inside its walls:

      1. the walls are the thick ink, and the windows drawn as thin lines between two pieces of wall
         (printed text inside the room is not a wall);
      2. the largest square that still fits in the room is slid around inside it: what it can reach
         is the room. A door or a window is narrower than the square, so the square cannot leave
         through it, and the room stops exactly at the face of its walls;
      3. free space the square could not reach is a nook of this room when only this room touches
         it, and a doorway when another room touches it too: nooks are added, doorways are not;
      4. whatever the room encloses and is not floor (a column, a shaft, a core with its own walls)
         is a hole: its area is taken out and it is returned, so it can be drawn.

    Returns {"ok", "why", "polygon", "holes": [{"ring", "area_m2"}], "gross_m2", "hole_m2", "area_m2",
    "perimeter_m" (the outline and the rings of the holes), "openings": [{"bbox", "width_m"}], "bbox", "seal_m"}.
    """
    import cv2
    import numpy as np

    kernel = max(3, round(px_per_m * THIN_M)) | 1
    dark, thick = _masks(str(path), kernel, how)
    height, width = dark.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width - 1, x1), min(height - 1, y1)
    bw, bh = x1 - x0, y1 - y0
    if bw < 0.6 * px_per_m or bh < 0.6 * px_per_m:
        return {"ok": False, "why": "the box is smaller than a room"}
    pad = int(max(3.0 * px_per_m, 0.6 * max(bw, bh)))  # the neighbours must be in view to tell a doorway from a nook
    wx0, wy0, wx1, wy1 = max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad)
    walls = thick[wy0:wy1, wx0:wx1].copy()
    ink = dark[wy0:wy1, wx0:wx1]

    # Bold printed text survives as thick ink: a speck that lies inside one printed text is not structure.
    if texts:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
        small = (0.6 * px_per_m) ** 2
        boxes = [(t["box"][0] - wx0 - 2, t["box"][1] - wy0 - 2, t["box"][2] - wx0 + 2, t["box"][3] - wy0 + 2) for t in texts]
        for n in range(1, count):
            left, top, w, h, area = stats[n]
            if area < small and any(a <= left and b <= top and left + w <= c and top + h <= d for a, b, c, d in boxes):
                walls[labels == n] = 0
    walls |= _glazing(walls, ink, px_per_m)  # a window is wall, drawn thin
    free = (walls == 0).astype(np.uint8)

    bx0, by0, bx1, by1 = x0 - wx0, y0 - wy0, x1 - wx0, y1 - wy0
    inner = np.zeros_like(free)
    inner[by0 + bh // 10:by1 - bh // 10, bx0 + bw // 10:bx1 - bw // 10] = 1
    grow = int(max(0.5 * px_per_m, 0.08 * max(bw, bh)))
    outside = np.ones_like(free)
    outside[max(0, by0 - grow):by1 + grow, max(0, bx0 - grow):bx1 + grow] = 0

    # The largest square that still finds the room: tried from large to small.
    side = int(min(SEAL_M[1] * px_per_m, 0.9 * min(bw, bh)))
    least = int(SEAL_M[0] * px_per_m)
    # A column in the middle of a room stops a large square from passing beside it, and the room falls
    # into two halves: so the square is made smaller until what it reaches is (nearly) all the floor in
    # the box. Only when no size does that is a smaller share accepted, at the largest size that gives it.
    floor_in_box = max(1, int((free & inner).sum()))
    core = opened_labels = None
    fallback = None
    while side >= least:
        opened = cv2.morphologyEx(free, cv2.MORPH_OPEN, np.ones((side, side), np.uint8))
        count, labels_here = cv2.connectedComponents(opened, connectivity=4)
        if count > 1:
            overlap = np.bincount(labels_here[inner == 1], minlength=count)
            overlap[0] = 0
            best = int(overlap.argmax())
            if overlap[best] >= WHOLE * floor_in_box:
                core, opened_labels = best, labels_here
                break
            if fallback is None and overlap[best] >= 0.35 * inner.sum():
                fallback = (best, labels_here, side)
        side = int(side * 0.8)
    if core is None and fallback is not None:
        core, opened_labels, side = fallback
    if core is None:
        return {"ok": False, "why": "no room was found inside the box: its walls are not solid lines, or it is narrower than a door is wide"}
    room = (opened_labels == core).astype(np.uint8)
    if (room & outside).sum() > LEAK * room.sum():
        return {"ok": False, "why": f"the room is open to the space next to it (an opening wider than {side / px_per_m:.1f} m): "
                                    "where it ends cannot be told from the walls"}

    # Free space the square could not reach: a nook when only this room touches it, a doorway otherwise.
    rest = ((free == 1) & (opened_labels == 0)).astype(np.uint8)
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    # Along a wall the square leaves slivers a pixel or two wide (a wall's face is never perfectly
    # straight). They are floor of the room they lie against; left in, they would string every
    # doorway of the room together into one.
    hair = 2 * max(2, int(0.04 * px_per_m)) + 1
    solid_rest = cv2.morphologyEx(rest, cv2.MORPH_OPEN, np.ones((hair, hair), np.uint8))
    count, sliver_labels = cv2.connectedComponents(((rest == 1) & (solid_rest == 0)).astype(np.uint8), connectivity=4)
    mine = np.unique(sliver_labels[(cv2.dilate(room, cross) == 1) & (sliver_labels > 0)])
    room[np.isin(sliver_labels, mine)] = 1
    rest = solid_rest
    count, rest_labels = cv2.connectedComponents(rest, connectivity=4)
    near_room = cv2.dilate(room, cross)
    others = cv2.dilate(((opened_labels > 0) & (opened_labels != core)).astype(np.uint8), cross)
    edge = np.zeros_like(free)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = 1
    touching = np.unique(rest_labels[(near_room == 1) & (rest_labels > 0)])
    passage_labels = set(np.unique(rest_labels[((others == 1) | (edge == 1)) & (rest_labels > 0)]).tolist())
    doorways = np.zeros_like(free)
    for n in touching.tolist():
        part = rest_labels == n
        if n not in passage_labels:
            # Only this room reaches it. A nook opens onto the room across its whole width; a closet or
            # a lift is reached through a neck (its door) narrower than the space behind it.
            ys, xs = np.nonzero(part & (near_room == 1))
            mouth_wide = (xs.max() - xs.min()) >= (ys.max() - ys.min())
            mouth = (xs.max() - xs.min() if mouth_wide else ys.max() - ys.min()) + 1
            py, px = np.nonzero(part)
            behind = (px.max() - px.min() if mouth_wide else py.max() - py.min()) + 1
            if mouth >= NOOK * behind and not _shut_door(part, mouth_wide, ys, xs, free, px_per_m):
                room[part] = 1
                continue
        doorways[part] = 1

    # The room's outline and what it encloses that is not floor.
    contours, hierarchy = cv2.findContours(room, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"ok": False, "why": "no room was found inside the box"}
    outer = max((i for i in range(len(contours)) if hierarchy[0][i][3] == -1), key=lambda i: cv2.contourArea(contours[i]))
    tolerance = max(1.5, 0.02 * px_per_m)

    def ring(contour) -> list[list[float]]:
        simple = cv2.approxPolyDP(contour, tolerance, True)
        return [[float(p[0][0] + wx0), float(p[0][1] + wy0)] for p in simple]

    def length(contour) -> float:
        return float(cv2.arcLength(cv2.approxPolyDP(contour, tolerance, True), True)) / px_per_m

    holes, filled = [], 0
    for i in range(len(contours)):
        if hierarchy[0][i][3] != outer:
            continue
        area = float(cv2.contourArea(contours[i])) / px_per_m ** 2
        if area < COLUMN_MIN_M2:  # a speck of ink: floor
            filled += int(cv2.contourArea(contours[i]))
            continue
        holes.append({"ring": ring(contours[i]), "area_m2": area, "perimeter_m": length(contours[i])})
    # The areas are counted on the mask, not computed from the simplified outlines.
    mask = np.zeros_like(room)
    cv2.drawContours(mask, contours, outer, 1, thickness=cv2.FILLED)
    gross_px = int(mask.sum())
    net_px = int((room & mask).sum()) + filled
    gross, net = gross_px / px_per_m ** 2, net_px / px_per_m ** 2

    # Door openings: where the room meets a doorway and nothing at all is drawn across the wall there.
    openings = []
    contact = ((cv2.dilate(room, cross) == 1) & (doorways == 1)).astype(np.uint8)
    count, contact_labels, stats, _ = cv2.connectedComponentsWithStats(contact, connectivity=8)
    low, high = DOOR_M[0] * px_per_m, DOOR_M[1] * px_per_m
    depth = max(3, int(0.12 * px_per_m))
    for n in range(1, count):
        left, top, w, h, _ = stats[n]
        span = max(w, h)
        if not low <= span <= high:
            continue
        # The stretch of wall behind the contact line, on the side away from the room.
        if w >= h:
            above = room[max(0, top - 2), left + w // 2] == 1
            a, b = (top + 1, top + 1 + depth) if above else (top - depth, top)
            probe = ink[max(0, a):max(0, b), left + 1:left + w - 1]
            bbox = [left, min(a, b), left + w, max(a, b)]
        else:
            before = room[top + h // 2, max(0, left - 2)] == 1
            a, b = (left + 1, left + 1 + depth) if before else (left - depth, left)
            probe = ink[top + 1:top + h - 1, max(0, a):max(0, b)]
            bbox = [min(a, b), top, max(a, b), top + h]
        if probe.size and probe.sum() == 0:
            openings.append({"bbox": [float(bbox[0] + wx0), float(bbox[1] + wy0), float(bbox[2] + wx0), float(bbox[3] + wy0)],
                             "width_m": span / px_per_m})
    ys, xs = np.nonzero(room)
    return {"ok": True, "why": "", "polygon": ring(contours[outer]), "holes": holes,
            "gross_m2": gross, "hole_m2": gross - net, "area_m2": net,
            "perimeter_m": length(contours[outer]) + sum(hole["perimeter_m"] for hole in holes),
            "openings": openings, "seal_m": side / px_per_m,
            "bbox": [float(xs.min() + wx0), float(ys.min() + wy0), float(xs.max() + wx0), float(ys.max() + wy0)]}


# ----------------------------------------------------------------- a rectangle drawn on the sheet

def drawn_rectangle(path: Path, box: list[float]) -> list[float] | None:
    """The rectangle drawn in solid lines inside a rough box: a pit, a trench, an opening.

    What a solid outline encloses is paper that no path leads out of. A dashed outline (something
    hidden below) encloses nothing, so it is not taken for the thing itself. A line that crosses the
    rectangle (a joint, a dimension's extension) cuts its inside in two: pieces that line up are one.
    Returns [x0, y0, x1, y1] in the picture's pixels, or None when no such rectangle is drawn there.
    """
    import cv2
    import numpy as np

    grey = _grey(str(path))
    height, width = grey.shape
    x0, y0, x1, y1 = box
    grow = 0.12 * max(x1 - x0, y1 - y0)
    cx0, cy0 = int(max(0, x0 - grow)), int(max(0, y0 - grow))
    cx1, cy1 = int(min(width, x1 + grow)), int(min(height, y1 + grow))
    if cx1 - cx0 < 8 or cy1 - cy0 < 8:
        return None
    paper = (grey[cy0:cy1, cx0:cx1] >= 170).astype(np.uint8)  # a thin grey line is still a line
    count, labels, stats, _ = cv2.connectedComponentsWithStats(paper, connectivity=4)
    least = 0.015 * (x1 - x0) * (y1 - y0)
    pieces = []
    for n in range(1, count):
        left, top, w, h, area = stats[n]
        if left == 0 or top == 0 or left + w == cx1 - cx0 or top + h == cy1 - cy0:
            continue  # reaches the edge of the view: not enclosed
        if area < least or area < 0.85 * w * h:
            continue  # a speck, or not a rectangle
        pieces.append((int(area), int(left), int(top), int(left + w), int(top + h)))
    if not pieces:
        return None
    pieces.sort(reverse=True)
    _, gx0, gy0, gx1, gy1 = pieces[0]
    near = 10  # a crossing line's thickness, at most
    grown = True
    while grown:
        grown = False
        for piece in pieces[1:]:
            _, px0, py0, px1, py1 = piece
            if (px0, py0, px1, py1) == (gx0, gy0, gx1, gy1) or (px0 >= gx0 and py0 >= gy0 and px1 <= gx1 and py1 <= gy1):
                continue
            same_columns = abs(px0 - gx0) <= 3 and abs(px1 - gx1) <= 3 and (0 <= py0 - gy1 <= near or 0 <= gy0 - py1 <= near)
            same_rows = abs(py0 - gy0) <= 3 and abs(py1 - gy1) <= 3 and (0 <= px0 - gx1 <= near or 0 <= gx0 - px1 <= near)
            if same_columns or same_rows:
                gx0, gy0, gx1, gy1 = min(gx0, px0), min(gy0, py0), max(gx1, px1), max(gy1, py1)
                grown = True
    # The inside of the outline, and half the line that draws it on each side.
    return [float(cx0 + gx0 - 1), float(cy0 + gy0 - 1), float(cx0 + gx1 + 1), float(cy0 + gy1 + 1)]
