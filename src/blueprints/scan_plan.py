"""The walls of a scanned floor plan, found in its pixels.

A scan is a picture of a plan: no layers, no geometry, no scale. Its walls are still the
thickest dark strokes on it, and that is enough to stand the plan up in 3D, with the scan
itself as the floor, so that what an answer counted can be shown in place as it is on a
plan with a DXF.

Nothing here is measured or counted, and no model is asked: the walls are a stage, not an
answer. When they cannot be told from the rest of the drawing (walls drawn as hairlines, a
page that is mostly ink, one dark blob), the scan is refused and has no 3D view: a
misleading model is worse than none.

Two ways of drawing a wall are read, the first that gives a plausible plan wins:

    solid   the walls are filled strokes, thicker than everything else (black, coloured or grey):
            strokes thinner than a wall are removed (text, door swings, furniture), then pieces
            that are nowhere as thick as the walls;
    lines   the walls are two thin parallel lines, hatched or empty between them: long straight
            lines are found, and the strip between two that run side by side is wall.

The outlines, with the rooms they enclose as holes, are returned in the image's own pixels.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import config

VERSION = 6

MAX_SIDE = 1500        # morphology is done on an image no larger than this
MAX_PIXELS = 30_000_000  # an image beyond this is not opened at all
INK_BELOW = 140        # darker than this (0-255) is ink; coloured walls are dark too
GREY_BELOW = 190       # a second look for walls filled in grey, when the first finds none
MIN_STROKE = 0.0045    # of the longer side: thinner strokes are lines, not walls
KEEP_LENGTH = 0.045    # of the longer side: a piece this long is kept even when it is thin
LINE_LENGTH = 0.05     # of the longer side: how long a line must run to be a wall's line
LINE_GAP = 0.017       # of the longer side: the widest wall drawn as two lines
LINE_PIECE = 0.12      # of the longer side: a separate piece of such a wall is at least this long
THICKER_THAN_LINES = 1.5   # walls must be this many times thicker than the thinnest stroke kept
CORE = 0.62            # of the walls' thickness: a piece must be this thick somewhere to be a wall
FURNITURE = 0.45       # of the walls' thickness: strokes thinner than this are furniture drawn against a wall
ASSUMED_WALL_M = 0.22  # a scan has no scale: where one is needed its walls are taken to be this thick
MARGIN = 0.07          # of the building's longer side: how much of the page is kept around the walls
MIN_COVER, MAX_COVER = 0.006, 0.30   # walls as a share of the page
MAX_FILL = 0.42        # walls as a share of the box around them: more is a blob, not a plan
MAX_THICK = 0.11       # of the longer side: a "wall" thicker than this is a filled area
MIN_RUN = {"solid": 0.8, "grey": 1.2, "lines": 1.6}   # wall length against half the box's perimeter
MIN_REACH = 0.28       # the box around the walls, as a share of the page's longer side
MAX_POINTS = 6000      # outline points in all: beyond this the "walls" are noise

_lock = threading.Lock()
_digests: dict[tuple[str, int, int], str] = {}   # (path, mtime, size) -> content hash
_known: dict[str, bool] | None = None            # verdicts already reached, for listings


def _digest(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _digests:
        _digests[key] = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    return _digests[key]


def _folder() -> Path:
    return config.REPO_ROOT / "cache" / "scan_plan"


def cache_path(image_path: Path) -> Path:
    return _folder() / f"{_digest(image_path)}-v{VERSION}.json"


def _stamp(path: Path) -> str:
    stat = path.stat()
    return f"{path}|{stat.st_mtime_ns}|{stat.st_size}|v{VERSION}"


def _verdicts() -> dict[str, bool]:
    global _known
    if _known is None:
        try:
            _known = json.loads((_folder() / "known.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _known = {}
    return _known


def verdict_if_known(image_path: Path) -> bool | None:
    """Whether this scan has walls good enough for a 3D view, if that was already worked out.

    Costs one `stat`: for listings, which must not read or analyse every image.
    """
    try:
        return _verdicts().get(_stamp(image_path))
    except OSError:
        return None


def _remember(image_path: Path, ok: bool) -> None:
    verdicts = _verdicts()
    stamp = _stamp(image_path)
    if verdicts.get(stamp) == ok:
        return
    verdicts[stamp] = ok
    try:
        _folder().mkdir(parents=True, exist_ok=True)
        tmp = _folder() / "known.json.tmp"
        tmp.write_text(json.dumps(verdicts), encoding="utf-8")
        tmp.replace(_folder() / "known.json")
    except OSError:
        pass  # a listing will simply not know yet


def load(image_path: Path) -> dict:
    """The walls of a scan, from cache when possible. See `find_walls` for the result."""
    with _lock:
        store = cache_path(image_path)
        if store.is_file():
            try:
                plan = json.loads(store.read_text(encoding="utf-8"))
                _remember(image_path, bool(plan.get("ok")))
                return plan
            except ValueError:
                pass
        from PIL import Image

        with Image.open(image_path) as im:  # the header only: nothing is decoded yet
            width, height = im.size
        if width * height > MAX_PIXELS:
            # A sheet of a drawing set, not a scanned plan: reading it whole takes memory this is not worth.
            plan = _refused(width, height, "the image is too large to trace walls on")
        else:
            plan = find_walls(read_gray(image_path))
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(plan), encoding="utf-8")
        _remember(image_path, bool(plan.get("ok")))
        return plan


def read_gray(image_path: Path):
    """The image as greys on white paper (a transparent background is paper)."""
    import numpy as np
    from PIL import Image

    with Image.open(image_path) as im:
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            sheet = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            sheet.alpha_composite(rgba)
            im = sheet
        return np.asarray(im.convert("L"))


def _refused(width: int, height: int, why: str, **quality) -> dict:
    return {"version": VERSION, "ok": False, "why": why, "width": width, "height": height,
            "thickness_px": None, "quality": quality, "polygons": []}


def _between_lines(small, side: int):
    """Walls drawn as two thin parallel lines (with or without hatching between them).

    Long straight lines are found across and down the page; where two of them run side by side,
    closer than a wall is thick, the strip between them is wall. A single line (a dimension line,
    the edge of a worktop) has no partner and stays a line. Returns the mask, the strips' width
    and the number of pieces.
    """
    import cv2
    import numpy as np

    paper = float(np.percentile(small, 90))
    ink = (small < min(200.0, paper - 45.0)).astype(np.uint8)  # hairlines are often grey, not black
    reach = max(14, int(round(LINE_LENGTH * side)))   # a wall's line is at least this long
    gap = max(5, int(round(LINE_GAP * side)))         # the two lines of a wall are closer than this
    least = max(4, int(round(0.004 * side)) + 2)      # a strip thinner than this is one line, not two

    def strips(along: tuple[int, int], across_gap: tuple[int, int], across_least: tuple[int, int]):
        lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, along))
        filled = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, across_gap))
        thick = cv2.morphologyEx(filled, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, across_least))
        return cv2.morphologyEx(thick, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, along))

    walls = strips((reach, 1), (1, gap), (1, least)) | strips((1, reach), (gap, 1), (least, 1))
    if not walls.any():
        return walls, float(least), 0
    # Many lines close together fill into a block (stair treads, shelves, a table of text): not a wall.
    depth = cv2.distanceTransform(walls, cv2.DIST_L2, 3)
    count, labels = cv2.connectedComponents((depth > 0.75 * gap).astype(np.uint8), connectivity=8)
    if count > 1:
        blocks = cv2.dilate((labels > 0).astype(np.uint8),
                            cv2.getStructuringElement(cv2.MORPH_RECT, (2 * gap + 1, 2 * gap + 1)))
        walls = walls & (1 - blocks)
    # What is left in pieces: a wall runs on; the double outline of a sofa or a bed does not. And a
    # strip along the very edge of the image is the page's own border, not a wall.
    h, w = walls.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    keep = np.zeros(count, dtype=bool)
    for n in range(1, count):
        x, y, bw, bh, _area = stats[n]
        at_edge = (bw <= 2 * gap and (x <= 2 or x + bw >= w - 2)) or (bh <= 2 * gap and (y <= 2 or y + bh >= h - 2))
        keep[n] = max(bw, bh) >= LINE_PIECE * side and not at_edge
    walls = keep[labels].astype(np.uint8)
    if not walls.any():
        return walls, float(least), 0
    depth = cv2.distanceTransform(walls, cv2.DIST_L2, 3)
    return walls, max(float(least), float(2.0 * np.percentile(depth[walls > 0], 80))), int(keep.sum())


def _solid_strokes(small, side: int, below: int):
    """Walls drawn as filled strokes thicker than the rest. Returns the mask, the walls' thickness
    and the number of pieces, or None when the strokes on the page are all of one thickness."""
    import cv2
    import numpy as np

    ink = (small < below).astype(np.uint8)
    # Strokes thinner than a wall go: text, dimension lines, door swings, furniture.
    stroke = max(3, int(round(MIN_STROKE * side)))
    solid = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (stroke, stroke)))
    if solid.mean() < MIN_COVER:
        return None
    # How thick the walls are: the thick end of what is left.
    depth = cv2.distanceTransform(solid, cv2.DIST_L2, 3)
    thick = max(float(2.0 * np.percentile(depth[solid > 0], 80)), float(stroke))
    if thick < THICKER_THAN_LINES * stroke:
        return None
    # A piece is a wall when somewhere it is as thick as the walls are, and it is long or solid.
    # Bold text and the outlines of furniture are thinner than the walls all over; a partition
    # that joins an outer wall is one piece with it and stays.
    core_size = max(stroke + 1, int(round(CORE * thick)))
    core = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (core_size, core_size)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(solid, connectivity=8)
    with_core = np.zeros(count, dtype=bool)
    with_core[np.unique(labels[core > 0])] = True
    with_core[0] = False
    keep = np.zeros(count, dtype=bool)
    for n in range(1, count):
        _x, _y, bw, bh, area = stats[n]
        keep[n] = with_core[n] and (max(bw, bh) >= KEEP_LENGTH * side or area >= 0.9 * thick * thick)
    walls = keep[labels].astype(np.uint8)
    if not walls.any():
        return None
    if 2.0 * float(depth[walls > 0].max()) > MAX_THICK * side:
        return None  # a filled area (a dark margin, a coloured room), not a wall
    # Wardrobes, kitchen units and sills are drawn against a wall with a medium line: one piece with
    # the wall, but thinner all over. Standing up in 3D they look like walls that are not there.
    # What is thinner than a share of the walls' thickness goes, then the specks that leaves behind.
    slim = max(stroke + 1, int(round(FURNITURE * thick)))
    thinned = cv2.morphologyEx(walls, cv2.MORPH_OPEN, np.ones((slim, slim), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(thinned, connectivity=8)
    for n in range(1, count):
        _x, _y, bw, bh, area = stats[n]
        if max(bw, bh) < 1.6 * thick and area < 2.5 * thick * thick:
            thinned[labels == n] = 0
    if thinned.sum() >= 0.6 * walls.sum():  # when most of it would go, the walls themselves are that thin: keep them
        walls = thinned
    count = cv2.connectedComponents(walls, connectivity=8)[0] - 1
    return walls, thick, int(count)


def _judge(walls, thick: float, how: str, side: int) -> tuple[str, dict]:
    """Is it a plan's walls? Not too little, not most of the page, not one filled shape. Returns
    the reason it is not ("" when it is) and the figures the judgement rests on."""
    import numpy as np

    h, w = walls.shape
    area = int(walls.sum())
    if area == 0:
        return "no walls found", {"how": how}
    ys, xs = np.nonzero(walls)
    box_w, box_h = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    cover = area / float(w * h)
    fill = area / float(box_w * box_h)
    reach = max(box_w, box_h) / float(side)
    run = (area / thick) / float(box_w + box_h)  # how much wall there is, against half the box's perimeter
    quality = {"how": how, "cover": round(cover, 4), "fill": round(fill, 3), "reach": round(reach, 3), "run": round(run, 2)}
    if cover < MIN_COVER:
        return "too little of the page is wall", quality
    if cover > MAX_COVER:
        return "too much of the page is dark", quality
    if fill > MAX_FILL:
        return "the dark part is one filled shape, not walls", quality
    if reach < MIN_REACH:
        return "the walls found cover only a corner of the page", quality
    if run < MIN_RUN[how]:
        return "too little wall for a plan", quality
    return "", quality


def find_walls(gray) -> dict:
    """Walls of a plan given as a grey image (0 = black ink, 255 = white paper).

    Returns {"ok", "why", "width", "height", "thickness_px", "quality", "polygons"} with each
    polygon as {"outer": [[x, y], ...], "holes": [[[x, y], ...], ...]} in the image's own pixels.
    `ok` is False, with the reason in `why`, when the walls cannot be told from the drawing.
    """
    import cv2
    import numpy as np

    height, width = gray.shape[:2]
    scale = min(1.0, MAX_SIDE / max(height, width))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    side = max(small.shape[:2])
    inked = float((small < INK_BELOW).mean())
    if float((small < GREY_BELOW).mean()) < 0.002:
        return _refused(width, height, "the page is almost empty", ink=round(inked, 4))
    if inked > 0.6:
        return _refused(width, height, "the page is mostly ink", ink=round(inked, 4))

    found, why, quality = None, "the walls are drawn as thin lines, like everything else on the page", {}
    for how in ("solid", "grey", "lines"):
        if how == "lines":
            walls, thick, pieces = _between_lines(small, side)
        else:
            got = _solid_strokes(small, side, INK_BELOW if how == "solid" else GREY_BELOW)
            if got is None:
                continue
            walls, thick, pieces = got
        wrong, figures = _judge(walls, thick, how, side)
        figures["pieces"] = pieces
        if not wrong:
            found, quality = (walls, thick), figures
            break
        if not quality or how == "lines":  # say why the last way failed, or the only one that got anywhere
            why, quality = wrong, figures
    quality["ink"] = round(inked, 4)
    if found is None:
        return _refused(width, height, why, **quality)
    walls, thick = found

    # A small piece far from the building is not part of it: a logo, a legend mark, a scanner's edge.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    if count > 2:
        areas = stats[1:, cv2.CC_STAT_AREA]
        main = 1 + int(areas.argmax())
        reach = int(8 * thick) | 1
        near = cv2.dilate((labels == main).astype(np.uint8), np.ones((reach, reach), np.uint8))
        for n in range(1, count):
            if n != main and stats[n, cv2.CC_STAT_AREA] < 0.05 * areas.max() and not near[labels == n].any():
                walls[labels == n] = 0

    # Windows: a stretch of wall drawn as thin lines between two pieces of solid wall (measure._glazing).
    # They are glass in the 3D view, where a gap would read as a door.
    from . import measure

    windows = []
    glass = measure._glazing(walls, (small < INK_BELOW).astype(np.uint8), thick / ASSUMED_WALL_M)
    count, _, stats, _ = cv2.connectedComponentsWithStats(glass, connectivity=8)
    for n in range(1, count):
        left, top, w, h, _area = stats[n]
        if max(w, h) >= 2.0 * thick:  # shorter than that it is a door jamb or a notch, not a window
            windows.append([round(left / scale, 1), round(top / scale, 1), round((left + w) / scale, 1), round((top + h) / scale, 1)])

    # What of the page the building takes up, with a margin: the rest (a title, a scanner's dark edge) is left out.
    ys, xs = np.nonzero(walls)
    pad = max(12.0, MARGIN * max(xs.max() - xs.min(), ys.max() - ys.min()))
    crop = [round(max(0.0, (xs.min() - pad) / scale), 1), round(max(0.0, (ys.min() - pad) / scale), 1),
            round(min(float(width), (xs.max() + pad) / scale), 1), round(min(float(height), (ys.max() + pad) / scale), 1)]

    # Outlines, with the rooms they enclose as holes, in the image's own pixels.
    contours, hierarchy = cv2.findContours(walls, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    slack = max(0.8, 0.12 * thick)

    def ring(contour) -> list[list[float]] | None:
        points = cv2.approxPolyDP(contour, slack, True).reshape(-1, 2)
        if len(points) < 3 or cv2.contourArea(points.astype(np.float32)) < 0.3 * thick * thick:
            return None
        return [[round(float(x) / scale, 1), round(float(y) / scale, 1)] for x, y in points]

    polygons: list[dict] = []
    owner: dict[int, int] = {}
    for n, contour in enumerate(contours):
        if hierarchy[0][n][3] != -1:
            continue  # a hole: placed with its outer ring below
        outer = ring(contour)
        if outer is not None:
            owner[n] = len(polygons)
            polygons.append({"outer": outer, "holes": []})
    for n, contour in enumerate(contours):
        parent = hierarchy[0][n][3]
        if parent in owner:
            hole = ring(contour)
            if hole is not None:
                polygons[owner[parent]]["holes"].append(hole)
    points = sum(len(p["outer"]) + sum(len(hole) for hole in p["holes"]) for p in polygons)
    quality["points"] = points
    if not polygons:
        return _refused(width, height, "no wall outline could be drawn", **quality)
    if points > MAX_POINTS:
        return _refused(width, height, "the dark strokes are too ragged to be walls", **quality)
    return {"version": VERSION, "ok": True, "why": "", "width": width, "height": height,
            "thickness_px": round(thick / scale, 2), "quality": quality, "polygons": polygons,
            "windows": windows, "crop": crop}
