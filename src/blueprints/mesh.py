"""Triangle meshes for the 3D evidence view.

Two sources, one small binary format the browser loads in one request:

- an IFC model, tessellated by the geometry kernel, one mesh range per element so an
  answer's elements (by GlobalId) can be lit up;
- a Swiss floor plan, raised from its DXF: wall outlines extruded to storey height,
  doors and windows as blocks, room outlines as floor plates.

Format ("BPM1"): magic, uint32 header length, header JSON padded to 4 bytes,
float32 positions (x, y, z in metres, z up, centred on the model), uint32 triangle indices.
The header lists the elements with their ranges in both buffers.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import struct
import threading
from array import array
from pathlib import Path

from . import config

MESH_VERSION = 5
MAX_TRIANGLES = 6_000_000  # a model beyond this is not sent to a browser
# A model file beyond this is not meshed at all: opening it takes minutes and gigabytes of memory,
# and it ends over the triangle limit anyway. Its questions are still answered; it has no 3D view.
MAX_IFC_BYTES = 150 * 1024 * 1024
TOO_LARGE_EXIT = 3  # how a build in a process of its own says "too large"
SKIP_CLASSES = ("IfcOpeningElement", "IfcAnnotation", "IfcGrid", "IfcVirtualElement")
WALL_HEIGHT = 2.6


_build_lock = threading.Lock()  # one build at a time: the geometry kernel already uses every core
_hashes: dict[tuple[str, int, int], str] = {}


class MeshTooLarge(Exception):
    pass


def unpack(data: bytes) -> tuple[dict, memoryview, memoryview]:
    """Header, positions (float32) and triangle indices (uint32) of a packed mesh."""
    (length,) = struct.unpack_from("<I", data, 4)
    header = json.loads(data[8:8 + length])
    start = 8 + length
    middle = start + 12 * header["vertices"]
    return (header, memoryview(data)[start:middle].cast("f"),
            memoryview(data)[middle:middle + 12 * header["triangles"]].cast("I"))


def footprint(element: dict, positions, indices) -> list[list[float]]:
    """Outline of an element seen from above, in the mesh's coordinates (largest part if it has several)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    start, count = element["i"]
    triangles = []
    for k in range(start, start + count, 3):
        a, b, c = indices[k], indices[k + 1], indices[k + 2]
        tri = Polygon([(positions[3 * a], positions[3 * a + 1]), (positions[3 * b], positions[3 * b + 1]),
                       (positions[3 * c], positions[3 * c + 1])])
        if tri.area > 1e-6:
            triangles.append(tri)
    if not triangles:
        return []
    shape = unary_union(triangles).buffer(0.005).buffer(-0.005)  # close hairline gaps between triangles
    if shape.geom_type == "MultiPolygon":
        shape = max(shape.geoms, key=lambda g: g.area)
    if shape.is_empty or shape.geom_type != "Polygon":
        return []
    return [[round(x, 3), round(y, 3)] for x, y in shape.exterior.coords[:-1]]


def _cache_file(source: Path, tag: str) -> Path:
    stat = source.stat()
    key = (str(source), stat.st_mtime_ns, stat.st_size)
    if key not in _hashes:
        h = hashlib.sha1()
        with open(source, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _hashes[key] = h.hexdigest()[:16]
    return config.REPO_ROOT / "cache" / "mesh" / f"{_hashes[key]}-{tag}-v{MESH_VERSION}.bpm"


def _pack(header: dict, positions: array, indices: array) -> bytes:
    head = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    head += b" " * (-len(head) % 4)
    return b"BPM1" + struct.pack("<I", len(head)) + head + positions.tobytes() + indices.tobytes()


def _centre(positions: array, elements: list[dict]) -> tuple[list[float], list[float]]:
    """Move the model to its own centre (georeferenced models sit kilometres from zero).

    Also gives every element `c`, the centre of its bounding box in the moved coordinates.
    """
    if not positions:
        return [0.0, 0.0, 0.0], [0.0] * 6
    lo = [min(positions[i::3]) for i in range(3)]
    hi = [max(positions[i::3]) for i in range(3)]
    mid = [(a + b) / 2 for a, b in zip(lo, hi)]
    mid[2] = lo[2]  # keep the ground at z = 0
    for axis in range(3):
        if mid[axis]:
            positions[axis::3] = array("f", (v - mid[axis] for v in positions[axis::3]))
    for element in elements:
        start, count = element["v"]
        centre = []
        for axis in range(3):
            values = positions[start * 3 + axis:(start + count) * 3:3]
            centre.append(round((min(values) + max(values)) / 2, 3))
        element["c"] = centre
    return mid, [lo[i] - mid[i] for i in range(3)] + [hi[i] - mid[i] for i in range(3)]


# ----------------------------------------------------------------- IFC


def _too_large_note(ifc_path: Path) -> Path:
    """Where it is noted that a model is too large to show: found out once, not on every visit."""
    return _cache_file(ifc_path, "ifc").with_suffix(".toolarge")


def is_built(ifc_path: Path) -> bool:
    """Whether a model's mesh is settled: in the cache, or known to be too large (building one can take minutes)."""
    return (ifc_path.stat().st_size > MAX_IFC_BYTES or _cache_file(ifc_path, "ifc").is_file()
            or _too_large_note(ifc_path).is_file())


def build_apart(ifc_path: Path) -> None:
    """Build a model's mesh in a process of its own, at low priority, and leave it in the cache.

    Built inside a server, the work holds the interpreter (opening a large model is one long call):
    every other request waits behind it for minutes. A process of its own keeps the server answering.
    """
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-I", "-m", "blueprints.mesh", str(ifc_path)],
                          capture_output=True, text=True)
    if done.returncode == TOO_LARGE_EXIT:
        note = _too_large_note(ifc_path)
        raise MeshTooLarge(note.read_text(encoding="utf-8") if note.is_file() else "too many triangles")
    if done.returncode != 0:
        raise RuntimeError(f"the model's mesh could not be built: {done.stderr.strip()[-300:]}")


def ifc_mesh(ifc_path: Path) -> bytes:
    """The whole model as one binary mesh, from cache when possible."""
    cache, note = _cache_file(ifc_path, "ifc"), _too_large_note(ifc_path)
    if cache.is_file():  # no need to wait behind another model that is being built
        return cache.read_bytes()
    if note.is_file():
        raise MeshTooLarge(note.read_text(encoding="utf-8"))
    size = ifc_path.stat().st_size
    if size > MAX_IFC_BYTES:
        raise MeshTooLarge(f"the model file is {size / 1e6:.0f} MB")
    with _build_lock:
        if cache.is_file():
            return cache.read_bytes()
        try:
            data = _build_ifc_mesh(ifc_path)
        except MeshTooLarge as exc:
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(str(exc), encoding="utf-8")
            raise
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data


def _build_ifc_mesh(ifc_path: Path) -> bytes:
    import ifcopenshell
    import ifcopenshell.geom

    from .engines.ifc_index import _storey_of

    model = ifcopenshell.open(str(ifc_path))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    products = [p for p in model.by_type("IfcProduct")
                if p.Representation and not any(p.is_a(c) for c in SKIP_CLASSES)]
    import ifcopenshell.util.placement as up
    import ifcopenshell.util.unit as uu

    try:
        length_scale = float(uu.calculate_unit_scale(model, "LENGTHUNIT"))
    except Exception:
        length_scale = 1.0
    storeys = {s.id(): i for i, s in enumerate(model.by_type("IfcBuildingStorey"))}
    storey_list = []
    for s in model.by_type("IfcBuildingStorey"):
        try:
            z = float(up.get_local_placement(s.ObjectPlacement)[2][3]) * length_scale
        except Exception:
            z = None
        storey_list.append({"name": s.Name or "", "z": z})

    positions, indices = array("f"), array("I")
    elements = []
    if products:
        # Half the cores: the machine that builds a mesh is also answering questions.
        iterator = ifcopenshell.geom.iterator(
            settings, model, max(1, multiprocessing.cpu_count() // 2), include=products)
        if iterator.initialize():
            while True:
                shape = iterator.get()
                verts, faces = shape.geometry.verts, shape.geometry.faces
                if verts and faces:
                    base = len(positions) // 3
                    element = model.by_guid(shape.guid)
                    storey = _storey_of(element)
                    elements.append({
                        "id": shape.guid, "cls": element.is_a(), "name": element.Name or "",
                        "storey": storeys.get(storey.id()) if storey is not None else None,
                        "v": [base, len(verts) // 3], "i": [len(indices), len(faces)],
                    })
                    positions.extend(verts)
                    indices.extend(f + base for f in faces)
                    if len(indices) // 3 > MAX_TRIANGLES:
                        raise MeshTooLarge(f"more than {MAX_TRIANGLES:,} triangles")
                if not iterator.next():
                    break
    offset, bbox = _centre(positions, elements)
    for storey in storey_list:  # heights in the moved coordinates, like the vertices
        if storey["z"] is not None:
            storey["z"] = round(storey["z"] - offset[2], 3)
    header = {"version": MESH_VERSION, "kind": "ifc", "elements": elements, "storeys": storey_list,
              "vertices": len(positions) // 3, "triangles": len(indices) // 3,
              "offset": offset, "bbox": bbox}
    return _pack(header, positions, indices)


# ----------------------------------------------------------------- DXF plan raised to 3D


def _prism(positions: array, indices: array, ring: list[tuple[float, float]], z0: float, z1: float,
           triangles: list[tuple[int, int, int]] | None) -> None:
    """A vertical prism over a closed ring; `triangles` index into `ring` for the caps."""
    n = len(ring)
    base = len(positions) // 3
    for z in (z0, z1):
        for x, y in ring:
            positions.extend((x, y, z))
    for a in range(n):  # side faces
        b = (a + 1) % n
        indices.extend((base + a, base + b, base + n + b, base + a, base + n + b, base + n + a))
    for a, b, c in triangles or ():
        indices.extend((base + n + a, base + n + b, base + n + c))  # top
        indices.extend((base + c, base + b, base + a))  # bottom


def _triangulate(ring: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangles of a simple polygon as indices into `ring` (ear clipping via mapbox_earcut-like fallback)."""
    from shapely.geometry import Polygon
    from shapely.ops import triangulate

    polygon = Polygon(ring)
    if not polygon.is_valid or polygon.area <= 0:
        return []
    lookup = {(round(x, 6), round(y, 6)): i for i, (x, y) in enumerate(ring)}
    out = []
    for tri in triangulate(polygon):  # Delaunay over the ring's own vertices
        if not polygon.contains(tri.representative_point()):
            continue
        corners = [lookup.get((round(x, 6), round(y, 6))) for x, y in list(tri.exterior.coords)[:3]]
        if None not in corners:
            out.append(tuple(corners))
    return out


def _clean_ring(points) -> list[tuple[float, float]]:
    ring = [(float(x), float(y)) for x, y, *_ in points]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()
    return ring


def plan_mesh(dxf_path: Path) -> bytes:
    """A Swiss floor plan raised to 3D from its DXF, from cache when possible."""
    cache = _cache_file(dxf_path, "plan")
    with _build_lock:
        if cache.is_file():
            return cache.read_bytes()
        data = _build_plan_mesh(dxf_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data


def _build_plan_mesh(dxf_path: Path) -> bytes:
    import ezdxf

    from .engines import dxf_index

    index = dxf_index.load_index(dxf_path)
    scale = index.get("metres_per_unit", 1.0)
    doc = ezdxf.readfile(str(dxf_path))
    positions, indices = array("f"), array("I")
    elements = []

    def add(kind: str, ident: str, ring: list[tuple[float, float]], z0: float, z1: float, **extra) -> None:
        if len(ring) < 3:
            return
        v0, i0 = len(positions) // 3, len(indices)
        _prism(positions, indices, ring, z0, z1, _triangulate(ring))
        elements.append({"id": ident, "cls": kind, "name": extra.pop("name", ""), "storey": 0,
                         "v": [v0, len(positions) // 3 - v0], "i": [i0, len(indices) - i0], **extra})

    walls = 0
    for entity in doc.modelspace().query('LWPOLYLINE[layer=="WALL"]'):
        if not entity.closed and len(entity) < 4:
            continue
        ring = _clean_ring((x * scale, y * scale) for x, y, *_ in entity.get_points())
        add("wall", f"wall-{walls}", ring, 0.0, WALL_HEIGHT)
        walls += 1
    # Identifiers match the evidence the engine returns: its bounding box, so a counted
    # door or window can be found again without the two modules sharing a numbering.
    for kind, z0, z1 in (("door", 0.0, 2.05), ("window", 0.9, 2.2)):
        for n, item in enumerate(index[f"{kind}s"]):
            add(kind, f"{kind}-{n}", _clean_ring(item["polygon"]), z0, z1, bbox=item["bbox"])
    for n, fixture in enumerate(index["fixtures"]):
        x0, y0, x1, y1 = fixture["bbox"]
        add(fixture["kind"], f"fixture-{n}", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.0, 0.45,
            bbox=fixture["bbox"])
    for n, room in enumerate(index["rooms"]):
        if room.get("shaft"):
            continue
        add("room", f"room-{n}", _clean_ring(room["polygon"]), -0.05, 0.0,
            name=" ".join(x for x in (room["name"], room["number"]) if x), number=room["number"],
            area=round(room["area"], 2))
    offset, bbox = _centre(positions, elements)
    header = {"version": MESH_VERSION, "kind": "plan", "elements": elements,
              "storeys": [{"name": "plan", "z": 0.0}],
              "vertices": len(positions) // 3, "triangles": len(indices) // 3,
              "offset": offset, "bbox": bbox, "wall_height": WALL_HEIGHT}
    return _pack(header, positions, indices)


# ----------------------------------------------------------------- a scan stood up from its pixels

SCAN_WALL_M = 0.22        # a scan has no scale: its walls are taken to be this thick...
SCAN_SPAN_M = (7.0, 60.0)  # ...as long as the page's longer side then comes out between these


class NoScanWalls(Exception):
    """The walls of this scan could not be told from the rest of the drawing: it has no 3D view."""


def _prism_with_holes(positions: array, indices: array, outer: list[tuple[float, float]],
                      holes: list[list[tuple[float, float]]], z0: float, z1: float) -> None:
    """A vertical prism over a polygon with holes (a ring of walls around a room is one).

    Every ring gets its side faces; the caps come from a triangulation that keeps to the polygon.
    """
    import shapely
    from shapely.geometry import Polygon

    polygon = Polygon(outer, holes)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    for part in getattr(polygon, "geoms", [polygon]):
        if part.is_empty or part.geom_type != "Polygon" or part.area <= 0:
            continue
        rings = [_clean_ring(part.exterior.coords)] + [_clean_ring(ring.coords) for ring in part.interiors]
        base = len(positions) // 3
        count = sum(len(ring) for ring in rings)
        for z in (z0, z1):
            for ring in rings:
                for x, y in ring:
                    positions.extend((x, y, z))
        lookup: dict[tuple[float, float], int] = {}
        start = 0
        for ring in rings:
            n = len(ring)
            for a in range(n):
                b = (a + 1) % n
                lookup.setdefault((round(ring[a][0], 6), round(ring[a][1], 6)), start + a)
                lo_a, lo_b = base + start + a, base + start + b
                indices.extend((lo_a, lo_b, lo_b + count, lo_a, lo_b + count, lo_a + count))  # side face
            start += n
        if hasattr(shapely, "constrained_delaunay_triangles"):
            triangles = list(getattr(shapely.constrained_delaunay_triangles(part), "geoms", []))
        else:  # an older shapely: Delaunay over the corners, keeping the triangles inside
            from shapely.ops import triangulate

            triangles = [t for t in triangulate(part) if part.contains(t.representative_point())]
        for triangle in triangles:
            corners = [lookup.get((round(x, 6), round(y, 6))) for x, y in list(triangle.exterior.coords)[:3]]
            if None in corners:
                continue
            a, b, c = corners
            indices.extend((base + count + a, base + count + b, base + count + c))  # top
            indices.extend((base + c, base + b, base + a))  # bottom


def scan_mesh(image_path: Path) -> bytes:
    """A scanned plan raised to 3D: its walls, found in the pixels, standing on the sheet.

    The header's "scan" entry says how an image pixel maps into the mesh (x = px * unit_per_px,
    y = (height - py) * unit_per_px, both less the header's offset), so that what an answer marked
    on the scan can be stood in the model at the same place. The scale is assumed, and says so.
    Raises NoScanWalls when the walls cannot be told from the drawing.
    """
    from . import scan_plan

    plan = scan_plan.load(image_path)
    if not plan.get("ok"):
        raise NoScanWalls(plan.get("why") or "no walls found")
    cache = _cache_file(image_path, f"scan{scan_plan.VERSION}")
    if cache.is_file():
        return cache.read_bytes()
    with _build_lock:
        if cache.is_file():
            return cache.read_bytes()
        data = _build_scan_mesh(plan)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
        return data


def _build_scan_mesh(plan: dict) -> bytes:
    width, height = plan["width"], plan["height"]
    unit = SCAN_WALL_M / max(plan["thickness_px"], 1.0)
    span = max(width, height) * unit
    unit *= min(max(span, SCAN_SPAN_M[0]), SCAN_SPAN_M[1]) / span

    def place(points) -> list[tuple[float, float]]:  # an image's y runs down, a model's y runs up
        return [(x * unit, (height - y) * unit) for x, y in points]

    positions, indices = array("f"), array("I")
    elements = []

    def element(kind: str, ident: str, build) -> None:
        v0, i0 = len(positions) // 3, len(indices)
        build()
        if len(indices) > i0:
            elements.append({"id": ident, "cls": kind, "name": "", "storey": 0,
                             "v": [v0, len(positions) // 3 - v0], "i": [i0, len(indices) - i0]})

    sheet = place([(0, 0), (width, 0), (width, height), (0, height)])
    element("IfcSlab", "sheet", lambda: _prism(positions, indices, sheet, -0.05, 0.0, [(0, 1, 2), (0, 2, 3)]))
    for n, polygon in enumerate(plan["polygons"]):
        outer, holes = place(polygon["outer"]), [place(hole) for hole in polygon["holes"]]
        element("wall", f"wall-{n}", lambda: _prism_with_holes(positions, indices, outer, holes, 0.0, WALL_HEIGHT))
    if len(elements) < 2:
        raise NoScanWalls("no wall outline could be raised")
    offset, bbox = _centre(positions, elements)
    header = {"version": MESH_VERSION, "kind": "scan", "elements": elements,
              "storeys": [{"name": "plan", "z": 0.0}],
              "vertices": len(positions) // 3, "triangles": len(indices) // 3,
              "offset": offset, "bbox": bbox, "wall_height": WALL_HEIGHT,
              "scan": {"width": width, "height": height, "unit_per_px": round(unit, 6),
                       "walls": plan["quality"].get("how"), "scale": "assumed",
                       "assumed_wall_m": SCAN_WALL_M}}
    return _pack(header, positions, indices)


if __name__ == "__main__":  # python -m blueprints.mesh <model.ifc>: build one model's mesh into the cache
    import os
    import sys

    try:
        os.nice(10)
    except OSError:
        pass
    try:
        ifc_mesh(Path(sys.argv[1]))
    except MeshTooLarge:
        sys.exit(TOO_LARGE_EXIT)
