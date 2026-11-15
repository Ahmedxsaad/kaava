"""The scale of a sheet, taken from whatever on it has a known size.

A plan drawn to scale can be measured once one length on it is known. Three things give one,
from the most exact to the least:

  1. the graphic scale bar (measure.scale_bar): its labels are read, its ends are found in the pixels;
  2. a dimension printed on the plan: the model points at the figure and at the two ends of its
     dimension line; the program reads the figure itself and moves each end onto the line that
     crosses the dimension line there (an extension line, a tick, a wall's face);
  3. doors: an ordinary single door is a standard size (0.9 m here, between 0.8 and 1.0 m in
     practice). The model boxes some doors; the program measures the leaf and its swing in the
     pixels of each box. This is an estimate, said as one, with the range it leaves open.

The same standard is also a check on the first two: door openings measured at the sheet's scale
must come out the width of a door. A scale bar that makes the doors two metres wide was misread.

Nothing here is a number from a model: the model points, the lengths are pixels.
"""

from __future__ import annotations

from pathlib import Path

from . import measure

STANDARD_DOOR_M = 0.9         # the leaf of an ordinary single door
DOOR_RANGE_M = (0.8, 1.0)     # ...in practice
PLAUSIBLE_LEAF_M = (0.6, 1.15)  # a door leaf measured at the right scale comes out in this range
DOUBLE_FROM_M = 1.3           # an opening this wide or wider has two leaves


def from_bar(path: Path, index: dict) -> dict | None:
    bar = measure.scale_bar(path, index)
    if bar is None:
        return None
    return {"source": "bar", "px_per_m": bar["px_per_m"], "low": bar["px_per_m"], "high": bar["px_per_m"], "estimate": False,
            "label": f"{bar['label']} = {bar['length_px']:,} px",
            "said": f"the scale bar ({bar['label']} = {bar['length_px']:,} px)",
            "evidence": [{"kind": "box", "space": "image_px", "bbox": [float(v) for v in bar["bbox"]], "role": "scale bar",
                          "label": f"{bar['label']} = {bar['length_px']:,} px"}],
            "trace": ["The program read the scale from the scale bar",
                      f"{bar['label']} = {bar['length_px']:,} px, {bar['px_per_m']:.1f} px per metre"],
            "checks": {"scale bar": f"{bar['label']} = {bar['length_px']:,} px ({bar['px_per_m']:.1f} px per metre)",
                       "scale bar labels standing at their marks": f"{bar['labels_in_place']} of {bar['labels']}"}}


def _snap(dark, at: float, across: float, along_x: bool, reach: int, band: int, face: int = 0) -> tuple[float, bool]:
    """Move one end of a dimension line onto the line that crosses it nearest to where the model pointed.

    `face` is 0 for a dimension's own tick or extension line (its middle is the end), +1 or -1 when the
    crossing line is a wall and the length runs from its face: the face on the side the length lies on.
    """
    import numpy as np

    height, width = dark.shape
    a, c = int(round(at)), int(round(across))
    if along_x:
        x0, x1, y0, y1 = max(0, a - reach), min(width, a + reach + 1), max(0, c - band), min(height, c + band + 1)
        profile = dark[y0:y1, x0:x1].sum(axis=0)
        start, full = x0, y1 - y0
    else:
        y0, y1, x0, x1 = max(0, a - reach), min(height, a + reach + 1), max(0, c - band), min(width, c + band + 1)
        profile = dark[y0:y1, x0:x1].sum(axis=1)
        start, full = y0, x1 - x0
    crossing = np.nonzero(profile >= 0.6 * full)[0]
    if not len(crossing):
        return at, False
    nearest = crossing[np.abs(crossing + start - a).argmin()]
    # The middle of the run of crossing pixels the nearest one belongs to (a line is a few pixels thick).
    run = [int(nearest)]
    while run[0] - 1 in crossing:
        run.insert(0, run[0] - 1)
    while run[-1] + 1 in crossing:
        run.append(run[-1] + 1)
    if face > 0:
        return float(start + run[-1] + 1), True  # the length lies beyond this wall: from its far face
    if face < 0:
        return float(start + run[0]), True
    return start + (run[0] + run[-1]) / 2, True


def _opening(dark, start: tuple[float, float], end: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The opening in a wall that a short length lies across (a door's width), from jamb to jamb, or None.

    Along the line between the two points, in a band a few pixels thick, a door opening is a stretch
    with no ink at all, with the wall's ink on both sides of it. It is taken only when it is about as
    long as the two points are apart: a room's length across furniture and text is not an opening.
    """

    height, width = dark.shape
    (ax, ay), (bx, by) = start, end
    along_x = abs(bx - ax) >= abs(by - ay)
    lo, hi = sorted((ax, bx) if along_x else (ay, by))
    span = hi - lo
    across = int(round((ay + by) / 2 if along_x else (ax + bx) / 2))
    reach = int(max(10, 0.45 * span))
    a0, a1 = int(max(0, lo - reach)), int(min((width if along_x else height) - 1, hi + reach))
    best = None
    for shift in range(-6, 7, 2):  # the model's line may lie a few pixels off the wall's own line
        c = across + shift
        if c - 2 < 0 or c + 3 > (height if along_x else width):
            continue
        band = dark[c - 2:c + 3, a0:a1 + 1] if along_x else dark[a0:a1 + 1, c - 2:c + 3].T
        ink = band.any(axis=0)
        if ink.mean() < 0.25:  # a wall's line has ink along most of what is not the opening
            continue
        middle = int(round((lo + hi) / 2)) - a0
        if not 0 <= middle < len(ink) or ink[middle]:
            continue
        left = middle
        while left > 0 and not ink[left - 1]:
            left -= 1
        right = middle
        while right < len(ink) - 1 and not ink[right + 1]:
            right += 1
        if left == 0 or right == len(ink) - 1:
            continue  # the empty stretch runs out of view: not closed by a jamb on both sides
        gap = right - left + 1
        if 0.65 * span <= gap <= 1.4 * span and (best is None or abs(gap - span) < abs(best[1] - best[0] + 1 - span)):
            best = (a0 + left, a0 + right, c)
    if best is None:
        return None
    first, last, c = best
    return ((float(first), float(c)), (float(last + 1), float(c))) if along_x else ((float(c), float(first)), (float(c), float(last + 1)))


def from_dimension(path: Path, start: tuple[float, float], end: tuple[float, float], length_m: float, printed: str,
                   grid_px: float, least_px: int = 40, faces: bool = False) -> dict | None:
    """Pixels per metre from one printed dimension. `start` and `end` are (x, y) in pixels, as the model pointed.

    `faces`: the length is that of a thing between two walls or jambs (a room's length, a door's width),
    not of a dimension line between its ticks: it runs from wall face to wall face.
    """
    if length_m <= 0:
        return None
    dark = (measure._grey(str(path)) < measure.DARK).astype("uint8")
    (ax, ay), (bx, by) = start, end
    dx, dy = abs(bx - ax), abs(by - ay)
    if max(dx, dy) < least_px:
        return None
    snapped = [False, False]
    opening = _opening(dark, (ax, ay), (bx, by)) if faces and (dx >= 4 * dy or dy >= 4 * dx) else None
    if opening is not None:
        # An opening in a wall (a door): nothing is drawn across it, so its two jambs are where the ink resumes.
        (ax, ay), (bx, by) = opening
        snapped = [True, True]
    elif dx >= 4 * dy or dy >= 4 * dx:  # a dimension line along the sheet: its ends can be found in the pixels
        along_x = dx >= dy
        span = max(dx, dy)
        reach, band = int(max(15, 0.05 * span)), int(max(12, 0.012 * span))
        if along_x:
            mid = (ay + by) / 2
            way = (1 if bx > ax else -1) if faces else 0
            ax, snapped[0] = _snap(dark, ax, mid, True, reach, band, way)
            bx, snapped[1] = _snap(dark, bx, mid, True, reach, band, -way)
            ay = by = mid
        else:
            mid = (ax + bx) / 2
            way = (1 if by > ay else -1) if faces else 0
            ay, snapped[0] = _snap(dark, ay, mid, False, reach, band, way)
            by, snapped[1] = _snap(dark, by, mid, False, reach, band, -way)
            ax = bx = mid
    length_px = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    slack = sum(2.0 if found else 1.5 * grid_px for found in snapped)  # an end left where the model put it is only as good as its grid
    px_per_m = length_px / length_m
    ends = "both ends moved onto the lines that cross it" if all(snapped) else \
        "one end moved onto the line that crosses it, the other left where the model pointed" if any(snapped) else \
        "its ends left where the model pointed"
    box = [min(ax, bx) - 3, min(ay, by) - 3, max(ax, bx) + 3, max(ay, by) + 3]
    return {"source": "dimension", "px_per_m": px_per_m, "low": (length_px - slack) / length_m, "high": (length_px + slack) / length_m,
            "estimate": not all(snapped), "label": f"{printed} = {length_px:,.0f} px",
            "said": f"a dimension printed on the plan ({printed} = {length_px:,.0f} px; {ends})",
            "evidence": [{"kind": "box", "space": "image_px", "bbox": [float(v) for v in box], "role": "scale bar",
                          "label": f"{printed} = {length_px:,.0f} px"}],
            "trace": ["The program took the scale from a dimension printed on the plan",
                      f"{printed} = {length_px:,.0f} px, {px_per_m:.1f} px per metre; {ends}"],
            "checks": {"scale, from a printed dimension": f"{printed} = {length_px:,.0f} px ({px_per_m:.1f} px per metre)",
                       "ends of the dimension line found in the pixels": f"{sum(snapped)} of 2"}}


def _leaf_px(dark, box: list[float]) -> tuple[float, list[float]]:
    """The size of one door in pixels: its leaf and swing are the largest piece of thin ink in the model's box."""
    import cv2
    import numpy as np

    height, width = dark.shape
    x0, y0, x1, y1 = box
    guess = max(x1 - x0, y1 - y0)
    grow = 0.18 * guess
    cx0, cy0, cx1, cy1 = (int(max(0, x0 - grow)), int(max(0, y0 - grow)), int(min(width, x1 + grow)), int(min(height, y1 + grow)))
    patch = dark[cy0:cy1, cx0:cx1]
    if patch.size == 0:
        return guess, box
    # Walls are thick, a door leaf and its arc are thin: take the walls away.
    kernel = max(3, round(0.1 * guess)) | 1
    thin = (patch == 1) & (cv2.morphologyEx(patch, cv2.MORPH_OPEN, np.ones((kernel, kernel), np.uint8)) == 0)
    count, _, stats, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8), connectivity=8)
    best = None
    for n in range(1, count):
        left, top, w, h, area = stats[n]
        size = max(w, h)
        if size < 0.5 * guess or size > 1.5 * guess:
            continue
        if best is None or area > best[0]:
            best = (area, size, [float(cx0 + left), float(cy0 + top), float(cx0 + left + w), float(cy0 + top + h)])
    return (best[1], best[2]) if best else (guess, [float(v) for v in box])


def from_doors(path: Path, boxes: list[list[float]]) -> dict | None:
    """Pixels per metre from the doors on the plan, taken as standard doors. An estimate."""
    import statistics

    boxes = [b for b in boxes if b and b[2] - b[0] > 4 and b[3] - b[1] > 4]
    if len(boxes) < 2:
        return None
    dark = (measure._grey(str(path)) < measure.DARK).astype("uint8")
    sizes, found = [], []
    for box in boxes[:12]:
        size, where = _leaf_px(dark, box)
        sizes.append(size)
        found.append(where)
    leaf = statistics.median(sizes)
    spread = (max(sizes) - min(sizes)) / leaf
    px_per_m = leaf / STANDARD_DOOR_M
    return {"source": "doors", "px_per_m": px_per_m, "low": leaf / DOOR_RANGE_M[1], "high": leaf / DOOR_RANGE_M[0], "estimate": True,
            "label": f"a door = {leaf:.0f} px",
            "said": f"the doors on the plan, taken as standard doors {STANDARD_DOOR_M:g} m wide ({len(sizes)} doors, {leaf:.0f} px each)",
            "evidence": [{"kind": "box", "space": "image_px", "bbox": where, "role": "door used as the ruler",
                          "label": f"{size:.0f} px"} for size, where in zip(sizes, found)],
            "trace": ["No scale is printed: the program measured the doors and took them as standard doors",
                      f"{len(sizes)} doors, {leaf:.0f} px each (they differ by {spread:.0%}); a standard door is "
                      f"{STANDARD_DOOR_M:g} m, so {px_per_m:.1f} px per metre. If the doors are {DOOR_RANGE_M[0]:g} or "
                      f"{DOOR_RANGE_M[1]:g} m, every length is {DOOR_RANGE_M[0] / STANDARD_DOOR_M - 1:+.0%} or "
                      f"{DOOR_RANGE_M[1] / STANDARD_DOOR_M - 1:+.0%} off"],
            "checks": {"scale, from the doors (an estimate)": f"a door = {leaf:.0f} px = {STANDARD_DOOR_M:g} m ({px_per_m:.1f} px per metre)",
                       "doors measured": len(sizes), "how much the doors differ": f"{spread:.0%}"}}


def door_check(openings: list[dict]) -> dict | None:
    """Door openings measured at the sheet's scale, against what a door is: {"leaf_m", "leaves", "ok"}."""
    import statistics

    leaves = []
    for opening in openings:
        w = float(opening["width_m"])
        leaves += [w / 2, w / 2] if w >= DOUBLE_FROM_M else [w]
    if not leaves:
        return None
    leaf = statistics.median(leaves)
    return {"leaf_m": leaf, "leaves": len(leaves), "ok": PLAUSIBLE_LEAF_M[0] <= leaf <= PLAUSIBLE_LEAF_M[1]}
