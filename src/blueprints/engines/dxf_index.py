"""Index of a floor-plan DXF: rooms, apartments, doors, windows and sanitary fixtures.

The Swiss Dwellings DXF files are semantic drawings: one layer per element class,
coordinates in metres, no blocks.

    ROOM_OUTLINE  closed polyline per room (and per shaft); stray 2-point segments
    ROOM_TEXT     per room: name, number, printed area ("15.61 m²"); an apartment
                  tag ("Whg. A") in one room of each apartment
    UNIT_OUTLINE  one polyline around each unit
    DOOR          per door: a 5-point frame, a leaf line and one arc per leaf
    WINDOW        per window: a closed 4-point outline and two glass lines
    FIXTURE       unnamed primitives: ellipses (48-point polylines), tub outlines
                  (69-point), rectangles, drain circles, hob burners
    WALL, ANNOTATION

Everything is geometry or a label read from the file; nothing is inferred by a model.
Coordinates are kept (in metres, drawing space) so every counted item can be drawn.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .. import config

INDEX_VERSION = 6

# $INSUNITS code -> metres per drawing unit
_UNIT_SCALE = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}

_NUMBER = re.compile(r"^\d+(\.\d+)*[a-z]?$")
_AREA = re.compile(r"^(\d+(?:[.,]\d+)?)\s*m[²2]$")
# "Whg. A", "App. B", "Apt. C", "Unit D": a short word, an optional dot, one letter or number.
_APARTMENT = re.compile(r"^(Whg|Wohnung|App|Appartement|Appartamento|Apt|Apartment|Unit|Log|Logement)\.?\s+(\w{1,3})$",
                        re.IGNORECASE)

DRAIN_MAX_RADIUS = 0.06  # drains are 2-4 cm; hob burners are 8 cm
ELLIPSE_POINTS = 48      # toilet bowls and washbasin bowls
TUB_POINTS = 69          # the rounded inner outline of a bathtub


def cache_path(dxf_path: Path) -> Path:
    digest = hashlib.sha1(dxf_path.read_bytes()).hexdigest()[:16]
    return config.REPO_ROOT / "cache" / "dxf" / f"{digest}-v{INDEX_VERSION}.json"


def load_index(dxf_path: Path) -> dict:
    path = cache_path(dxf_path)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    index = build_index(dxf_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return index


def _points(entity, scale: float) -> list[list[float]]:
    return [[round(float(p[0]) * scale, 4), round(float(p[1]) * scale, 4)] for p in entity.get_points()]


def _bbox(points: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _text_position(entity, scale: float) -> tuple[float, float]:
    # For aligned text the true anchor is the alignment point, not the insert point.
    _align, p1, _p2 = entity.get_placement()
    return float(p1.x) * scale, float(p1.y) * scale


def build_index(dxf_path: Path) -> dict:
    import ezdxf
    from shapely.geometry import LineString, Point, Polygon

    doc = ezdxf.readfile(str(dxf_path))
    scale = _UNIT_SCALE.get(doc.header.get("$INSUNITS", 6), 1.0)
    layers: dict[str, list] = defaultdict(list)
    for entity in doc.modelspace():
        layers[entity.dxf.layer].append(entity)

    def polylines(layer: str):
        return [e for e in layers.get(layer, []) if e.dxftype() == "LWPOLYLINE"]

    # ---- rooms: closed outlines, with the texts that sit inside each
    room_shapes = []
    for e in polylines("ROOM_OUTLINE"):
        pts = _points(e, scale)
        if len(pts) >= 3 and e.closed:
            poly = Polygon(pts)
            if poly.is_valid and poly.area > 0:
                room_shapes.append((poly, pts))
    # A shaft or void is drawn crossed out: two diagonal 2-point segments inside its outline.
    segments = [LineString(pts) for e in polylines("ROOM_OUTLINE")
                if len(pts := _points(e, scale)) == 2 and e.dxf.get("linetype", "BYLAYER") != "DASHED"]
    rooms = []
    for poly, pts in room_shapes:
        grown = poly.buffer(0.02)
        crossed = sum(1 for seg in segments
                      if grown.contains(seg) and poly.contains(seg.interpolate(0.5, normalized=True)))
        # A polyline cannot have a hole, so a room around another outline is drawn as two
        # outlines: its net area is its own minus the ones lying wholly inside it.
        holes = [other for other, _ in room_shapes
                 if other is not poly and other.area < poly.area
                 and poly.contains(other.representative_point()) and poly.buffer(0.01).contains(other)]
        rooms.append({"polygon": pts, "gross_area": round(poly.area, 4),
                      "area": round(poly.area - sum(h.area for h in holes), 4), "holes": len(holes),
                      "shaft": crossed >= 2, "number": None, "name": None, "printed_area": None,
                      "apartment_tag": None, "unit": None, "texts": []})

    unplaced_texts = []
    for e in layers.get("ROOM_TEXT", []):
        if e.dxftype() != "TEXT":
            continue
        text = e.dxf.text.strip()
        x, y = _text_position(e, scale)
        inside = [i for i, (poly, _) in enumerate(room_shapes) if poly.contains(Point(x, y))]
        if not inside:
            unplaced_texts.append(text)
            continue
        room = rooms[min(inside, key=lambda i: room_shapes[i][0].area)]  # the innermost outline
        room["texts"].append(text)
        area = _AREA.match(text)
        tag = _APARTMENT.match(text)
        if area:
            room["printed_area"] = float(area.group(1).replace(",", "."))
        elif tag:
            room["apartment_tag"] = tag.group(2)
        elif _NUMBER.match(text):
            room["number"] = text
        elif room["name"] is None:
            room["name"] = text

    # ---- units (apartments): outline, its rooms, the tag printed in one of them.
    # A unit can sit inside another one's outline (a shared corridor inside a flat's
    # outline), so a room belongs to the smallest unit that contains it.
    unit_shapes = []
    for e in polylines("UNIT_OUTLINE"):
        pts = _points(e, scale)
        if len(pts) < 3:
            continue
        poly = Polygon(pts)
        unit_shapes.append((poly if poly.is_valid else poly.buffer(0), pts))
    members: dict[int, list[int]] = {u: [] for u in range(len(unit_shapes))}
    for i, (room_poly, _) in enumerate(room_shapes):
        point = room_poly.representative_point()
        holding = [u for u, (poly, _) in enumerate(unit_shapes) if poly.contains(point)]
        if holding:
            unit_id = min(holding, key=lambda u: unit_shapes[u][0].area)
            rooms[i]["unit"] = unit_id
            members[unit_id].append(i)
    units = []
    for u, (poly, pts) in enumerate(unit_shapes):
        tags = sorted({rooms[i]["apartment_tag"] for i in members[u] if rooms[i]["apartment_tag"]})
        units.append({"polygon": pts, "area": round(poly.area, 4), "tag": tags[0] if len(tags) == 1 else None,
                      "tags": tags, "rooms": members[u]})

    # ---- doors and windows
    doors = [{"polygon": pts, "bbox": _bbox(pts)}
             for pts in (_points(e, scale) for e in polylines("DOOR")) if len(pts) == 5]
    door_arcs = sum(1 for e in layers.get("DOOR", []) if e.dxftype() == "ARC")
    windows = [{"polygon": pts, "bbox": _bbox(pts)}
               for e in polylines("WINDOW") if e.closed and len(pts := _points(e, scale)) == 4]

    # ---- sanitary fixtures, told apart by shape
    drains = [(float(e.dxf.center.x) * scale, float(e.dxf.center.y) * scale)
              for e in layers.get("FIXTURE", [])
              if e.dxftype() == "CIRCLE" and float(e.dxf.radius) * scale < DRAIN_MAX_RADIUS]
    fixture_lines = polylines("FIXTURE")
    ellipses = [_points(e, scale) for e in fixture_lines if len(e) == ELLIPSE_POINTS]
    tubs = [_points(e, scale) for e in fixture_lines if len(e) == TUB_POINTS]
    boxes = [_points(e, scale) for e in fixture_lines if len(e) in (4, 5)]

    def holds_drain(pts: list[list[float]], used: set[int]) -> int | None:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        for i, (x, y) in enumerate(drains):
            if i not in used and poly.contains(Point(x, y)):
                return i
        return None

    used: set[int] = set()
    fixtures = []
    for pts in tubs:  # a bathtub: the rounded inner outline (its drain sits inside)
        drain = holds_drain(pts, used)
        if drain is not None:
            used.add(drain)
        fixtures.append({"kind": "bathtub", "bbox": _bbox(pts)})
    for pts in ellipses:  # a bowl with a drain is a washbasin; without one, a toilet
        drain = holds_drain(pts, used)
        if drain is not None:
            used.add(drain)
            fixtures.append({"kind": "washbasin", "bbox": _bbox(pts)})
        else:
            fixtures.append({"kind": "toilet", "bbox": _bbox(pts)})
    for pts in boxes:  # a tray with a drain that belongs to nothing else is a shower
        drain = holds_drain(pts, used)
        if drain is not None:
            used.add(drain)
            fixtures.append({"kind": "shower", "bbox": _bbox(pts)})
    orphan_drains = len(drains) - len(used)

    annotation = [e.dxf.text.strip() for e in layers.get("ANNOTATION", []) if e.dxftype() == "TEXT"]
    return {
        "version": INDEX_VERSION,
        "metres_per_unit": scale,
        "layers": {name: len(items) for name, items in sorted(layers.items())},
        "rooms": rooms,
        "units": units,
        "doors": doors,
        "door_arcs": door_arcs,
        "windows": windows,
        "fixtures": fixtures,
        "orphan_drains": orphan_drains,
        "unplaced_texts": unplaced_texts,
        "annotation": annotation,
    }
