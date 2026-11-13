"""One-time index of an IFC model: storeys, spaces with areas, elements per storey.

Parsing a model and running the geometry kernel takes seconds; answering from the
index is a dictionary lookup. The index is cached on disk next to nothing secret
(cache/ifc/, git-ignored) and keyed by the file's content hash, so a changed model
is re-indexed and the same model is never parsed twice.

Every number here is computed from the model: counts from enumerated elements,
areas and volumes from geometry or from the model's own quantity sets.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
from pathlib import Path

from .. import config

INDEX_VERSION = 5

# Element kinds we can count, and the IFC classes that define them. by_type() also
# returns subtypes (IfcWall includes IfcWallStandardCase).
KIND_CLASSES: dict[str, list[str]] = {
    "door": ["IfcDoor"],
    "window": ["IfcWindow"],
    "wall": ["IfcWall"],
    "space": ["IfcSpace"],
    "stair": ["IfcStair"],
    "column": ["IfcColumn"],
    "beam": ["IfcBeam"],
    "slab": ["IfcSlab"],
    "roof": ["IfcRoof"],
    "railing": ["IfcRailing"],
    "ramp": ["IfcRamp"],
    "curtain_wall": ["IfcCurtainWall"],
    "covering": ["IfcCovering"],
    "footing": ["IfcFooting"],
    "furniture": ["IfcFurnishingElement"],
    # IFC4 has its own classes; IFC2X3 models type a generic flow terminal instead.
    "sanitary": ["IfcSanitaryTerminal"],
    "light": ["IfcLightFixture"],
}
_TYPED_FLOW_TERMINALS = {"sanitary": "IfcSanitaryTerminalType", "light": "IfcLightFixtureType"}
VOLUME_KINDS = ("slab", "wall")


def cache_path(ifc_path: Path) -> Path:
    h = hashlib.sha1()
    with open(ifc_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return config.REPO_ROOT / "cache" / "ifc" / f"{h.hexdigest()[:16]}-v{INDEX_VERSION}.json"


def load_index(ifc_path: Path, *, with_volumes: bool = False) -> dict:
    """The index of a model, from cache when possible."""
    path = cache_path(ifc_path)
    index = None
    if path.is_file():
        index = json.loads(path.read_text(encoding="utf-8"))
    if index is None or (with_volumes and index.get("volumes") is None):
        index = build_index(ifc_path, with_volumes=with_volumes or bool(index and index.get("volumes")))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return index


# ----------------------------------------------------------------- building


def _unit_scale(model, unit_type: str) -> float:
    import ifcopenshell.util.unit as uu

    try:
        return float(uu.calculate_unit_scale(model, unit_type))
    except Exception:
        return 1.0


def _storey_of(element):
    """The building storey an element belongs to, through containment or aggregation."""
    import ifcopenshell.util.element as ue

    seen = 0
    current = element
    while current is not None and seen < 12:
        if current.is_a("IfcBuildingStorey"):
            return current
        parent = ue.get_container(current)
        if parent is None or parent == current:
            parent = ue.get_aggregate(current)
        current = parent
        seen += 1
    return None


def _quantity(element, names: tuple[str, ...]) -> float | None:
    """A named quantity from any of the element's quantity sets."""
    import ifcopenshell.util.element as ue

    for qto in ue.get_psets(element, qtos_only=True).values():
        for name in names:
            value = qto.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                return float(value)
    return None


def _curve_points(curve) -> list[tuple[float, float]] | None:
    if curve.is_a("IfcPolyline"):
        return [(p.Coordinates[0], p.Coordinates[1]) for p in curve.Points]
    if curve.is_a("IfcIndexedPolyCurve") and curve.Segments is None:
        return [(c[0], c[1]) for c in curve.Points.CoordList]
    return None


def _footprint_curve_area(space, length_scale: float) -> float | None:
    """Area of the space's 2D FootPrint outline: an outline drawn separately from the body."""
    if not space.Representation:
        return None
    for rep in space.Representation.Representations:
        if rep.RepresentationIdentifier != "FootPrint":
            continue
        total, found = 0.0, False
        for item in rep.Items:
            curves = item.Elements if item.is_a("IfcGeometricSet") else [item]
            for curve in curves:
                if not curve.is_a("IfcCurve"):
                    continue
                pts = _curve_points(curve)
                if not pts or len(pts) < 4 or pts[0] != pts[-1]:
                    return None  # an open or unsupported outline: no independent area
                area = 0.0
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    area += x0 * y1 - x1 * y0
                total += abs(area) / 2
                found = True
        return total * length_scale * length_scale if found else None
    return None


def _geometry(model, elements, want: str) -> dict[str, float]:
    """Footprint area or volume per GlobalId from the geometry kernel, in metres."""
    import ifcopenshell.geom
    import ifcopenshell.util.shape as us

    out: dict[str, float] = {}
    elements = [e for e in elements if e.Representation]
    if not elements:
        return out
    settings = ifcopenshell.geom.settings()
    iterator = ifcopenshell.geom.iterator(
        settings, model, max(1, multiprocessing.cpu_count() - 1), include=elements)
    if not iterator.initialize():
        return out
    while True:
        shape = iterator.get()
        try:
            value = (us.get_footprint_area(shape.geometry) if want == "footprint"
                     else us.get_volume(shape.geometry))
            if math.isfinite(value):
                out[shape.guid] = float(value)
        except Exception:
            pass  # degenerate geometry: no value, the engine will say so
        if not iterator.next():
            break
    return out


def build_index(ifc_path: Path, *, with_volumes: bool = False) -> dict:
    import ifcopenshell
    import ifcopenshell.util.element as ue

    model = ifcopenshell.open(str(ifc_path))
    length_scale = _unit_scale(model, "LENGTHUNIT")
    area_scale = _unit_scale(model, "AREAUNIT")
    volume_scale = _unit_scale(model, "VOLUMEUNIT")

    def height(element) -> float | None:
        """World height of an element's placement origin, in metres."""
        import ifcopenshell.util.placement as up

        try:
            return float(up.get_local_placement(element.ObjectPlacement)[2][3]) * length_scale
        except Exception:
            return None

    storeys = []
    storey_idx: dict[int, int] = {}
    for s in model.by_type("IfcBuildingStorey"):
        storey_idx[s.id()] = len(storeys)
        storeys.append({
            "id": s.GlobalId, "name": s.Name or "", "long_name": getattr(s, "LongName", None) or "",
            "elevation": (s.Elevation * length_scale) if isinstance(s.Elevation, (int, float)) else None,
            "z": height(s),
        })

    def storey_index(element) -> int | None:
        storey = _storey_of(element)
        return storey_idx.get(storey.id()) if storey is not None else None

    all_spaces = model.by_type("IfcSpace")
    footprints = _geometry(model, all_spaces, "footprint")
    spaces = []
    for sp in all_spaces:
        net = _quantity(sp, ("NetFloorArea",))
        gross = _quantity(sp, ("GrossFloorArea",))
        spaces.append({
            "id": sp.GlobalId, "name": sp.Name or "", "long_name": getattr(sp, "LongName", None) or "",
            "storey": storey_index(sp),
            "area_geometry": footprints.get(sp.GlobalId),
            "area_outline": _footprint_curve_area(sp, length_scale),
            "area_qto_net": net * area_scale if net is not None else None,
            "area_qto_gross": gross * area_scale if gross is not None else None,
        })

    kinds: dict[str, list[dict]] = {}
    for kind, classes in KIND_CLASSES.items():
        found = []
        for cls in classes:
            try:
                found.extend(model.by_type(cls))
            except RuntimeError:
                pass  # the class does not exist in this schema version
        typed = _TYPED_FLOW_TERMINALS.get(kind)
        if typed:
            try:
                for terminal in model.by_type("IfcFlowTerminal"):
                    element_type = ue.get_type(terminal)
                    if element_type is not None and element_type.is_a(typed) and terminal not in found:
                        found.append(terminal)
            except RuntimeError:
                pass
        kinds[kind] = [
            {"id": e.GlobalId, "cls": e.is_a(), "name": e.Name or "", "storey": storey_index(e),
             "z": height(e)}
            for e in found
        ]

    # Wall quantities the model states itself (its base quantity set), in metres.
    wall_quantities = {}
    for wall in model.by_type("IfcWall"):
        length = _quantity(wall, ("Length",))
        side = _quantity(wall, ("NetSideArea",))
        wall_quantities[wall.GlobalId] = {
            "length": length * length_scale if length is not None else None,
            "net_side_area": side * area_scale if side is not None else None,
        }

    index = {
        "version": INDEX_VERSION,
        "schema": model.schema,
        "project": model.by_type("IfcProject")[0].Name if model.by_type("IfcProject") else "",
        "length_unit_m": length_scale,
        "storeys": storeys,
        "spaces": spaces,
        "kinds": kinds,
        "wall_quantities": wall_quantities,
        "volumes": None,
    }
    if with_volumes:
        volumes: dict[str, dict] = {}
        for kind in VOLUME_KINDS:
            elements = []
            for cls in KIND_CLASSES[kind]:
                try:
                    elements.extend(model.by_type(cls))
                except RuntimeError:
                    pass
            from_geometry = _geometry(model, elements, "volume")
            for e in elements:
                net = _quantity(e, ("NetVolume",))
                gross = _quantity(e, ("GrossVolume",))
                volumes[e.GlobalId] = {
                    "geometry": from_geometry.get(e.GlobalId),
                    "qto_net": net * volume_scale if net is not None else None,
                    "qto_gross": gross * volume_scale if gross is not None else None,
                }
        index["volumes"] = volumes
    return index
