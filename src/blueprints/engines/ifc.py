"""IFC engine: counts, areas and volumes straight from the building model.

A question about an IFC model (or about a floor plan rendered from one) is a query
on structured data: count the elements of a class on a storey, read a space's
footprint, sum volumes. No language model is involved anywhere, the cost is zero,
and the evidence is the list of model elements (GlobalIds) behind the number, which
the 3D viewer can highlight.

What it answers:
  * How many <doors|windows|walls|rooms|stairs|columns|beams|...> on storey 'S' / in the model
  * Net floor area of room 'NAME (NUMBER)' on storey 'S'
  * Total net floor area of all rooms on storey 'S'
  * Total net volume of all <slabs|walls> on storey 'S'
Anything else is declined with the reason.

Every answer is state "check" for now: Verified needs thresholds calibrated on the
calibration split (RELIABILITY.md section 4), which comes with the verifier. The
agreement between independent sources is already computed and stored in `checks`.
"""

from __future__ import annotations

import threading
from pathlib import Path

from ..answer import Answer
from ..data import Dataset, Question
from ..questions import asks_about_whole_model, kinds_in, measure_of, norm, quoted_strings
from ..scoring import CHECK
from . import ifc_index

METHOD = "ifc-engine:v1"
AGREE = 0.005  # two sources "agree" within 0.5%
# Kinds whose counting convention matched the reference answers on dev drawings.
VALIDATED_KINDS = {"door", "window", "wall", "space", "stair", "column", "beam", "sanitary", "light"}
# Kinds whose placement height reliably tells the storey (their origin sits on the
# storey's floor). Walls, beams, slabs and columns are placed with offsets, so their
# height is not a usable second opinion.
HEIGHT_CHECKED_KINDS = {"door", "window", "stair"}
HEIGHT_TOLERANCE = 0.05  # metres


def _count_by_height(index: dict, storey: int, elements: list[dict]) -> int | None:
    """How many of the kind's elements physically sit in a storey's height band.

    Independent of the model's containment relations: it only uses where each element
    is placed and how high each storey is.
    """
    levels = sorted((s["z"], i) for i, s in enumerate(index["storeys"]) if s.get("z") is not None)
    if len(levels) != len(index["storeys"]) or any(e.get("z") is None for e in elements):
        return None
    position = next(k for k, (_, i) in enumerate(levels) if i == storey)
    low = levels[position][0] - HEIGHT_TOLERANCE
    high = levels[position + 1][0] - HEIGHT_TOLERANCE if position + 1 < len(levels) else float("inf")
    if position == 0:
        low = float("-inf")
    return sum(1 for e in elements if low <= e["z"] < high)

_indexes: dict[tuple[str, bool], dict] = {}
_lock = threading.Lock()


def _index(path: Path, with_volumes: bool = False) -> dict:
    key = (str(path), with_volumes)
    with _lock:  # one build at a time: the geometry kernel already uses every core
        if key not in _indexes:
            _indexes[key] = ifc_index.load_index(path, with_volumes=with_volumes)
        return _indexes[key]


def ifc_file_for(q: Question, dataset: Dataset) -> str | None:
    """The IFC model a question is about: the file itself, or the model a plan was rendered from."""
    if q.path.lower().endswith(".ifc"):
        return q.path if q.path in dataset.files else None
    model = dataset.ifc_model_of(q.path)
    return f"{model}.ifc" if model and f"{model}.ifc" in dataset.files else None


def _space_labels(space: dict) -> set[str]:
    name, long_name = space["name"], space["long_name"]
    labels = {name, long_name, f"{long_name} ({name})", f"{name} {long_name}", f"{name} - {long_name}"}
    return {norm(label) for label in labels if label.strip()}


def _element(item: dict, **extra) -> dict:
    name, long_name = item.get("name", ""), item.get("long_name", "")
    label = f"{long_name} ({name})" if long_name and name else long_name or name  # rooms: as the questions name them
    return {"kind": "ifc_element", "id": item["id"], "class": item.get("cls", "IfcSpace"),
            "label": label, **extra}


def _agreement(values: dict[str, float | None]) -> tuple[list[str], float | None]:
    """Sources that agree with the first one within AGREE, and the widest relative gap."""
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return [], None
    first_key = next(iter(present))
    base = present[first_key]
    agreeing = [first_key]
    worst = 0.0
    for key, value in list(present.items())[1:]:
        gap = abs(value - base) / abs(base) if base else float(value != base)
        worst = max(worst, gap)
        if gap <= AGREE:
            agreeing.append(key)
    return agreeing, worst


def answer(q: Question, dataset: Dataset) -> Answer:
    common = dict(method=METHOD, cost_usd=0.0)

    def decline(reason: str) -> Answer:
        return Answer.decline(reason, **common)

    ifc_rel = ifc_file_for(q, dataset)
    if ifc_rel is None:
        return decline("the IFC model for this file is not in the dataset")
    measure = measure_of(q.question)
    if measure is None:
        return decline("could not tell what the question measures")
    index = _index(dataset.abspath(ifc_rel), with_volumes=(measure == "volume"))
    storeys = index["storeys"]
    quoted = quoted_strings(q.question)
    kinds = kinds_in(q.question)

    # Which quoted name is a storey of this model?
    storey_hits = []
    for text in quoted:
        matches = [i for i, s in enumerate(storeys) if norm(text) in {norm(s["name"]), norm(s["long_name"])} - {""}]
        if len(matches) > 1:
            return decline(f"several storeys are named '{text}'")
        if matches:
            storey_hits.append((text, matches[0]))
    storey = storey_hits[-1][1] if storey_hits else None
    storey_name = storeys[storey]["name"] if storey is not None else None
    other_quoted = [t for t in quoted if storey is None or norm(t) != norm(storey_hits[-1][0])]
    names = ", ".join(repr(s["name"]) for s in storeys)

    # ---- wall length and wall side area, from the model's own wall quantities ----
    if measure == "length" or (measure == "area" and kinds == ["wall"]):
        if kinds != ["wall"]:
            return decline("only the total length of walls is supported for lengths")
        if storey is None:
            return decline(f"no storey of this model matches {quoted}; its storeys are {names}")
        walls = [e for e in index["kinds"]["wall"] if e["storey"] == storey]
        if not walls:
            return decline(f"no walls on storey '{storey_name}'")
        field, unit, what = (("length", "m", "length") if measure == "length"
                             else ("net_side_area", "m2", "net side area"))
        values = [index["wall_quantities"].get(e["id"], {}).get(field) for e in walls]
        missing = sum(v is None for v in values)
        if missing:
            return decline(f"{missing} of {len(walls)} walls on '{storey_name}' state no {what} in the model")
        total = sum(values)
        symbol = "m" if unit == "m" else "m²"
        return Answer(
            answer=round(total, 2), unit=unit, state=CHECK, confidence=0.9,
            evidence=[_element(e, **{f"{field}": round(v, 3)}) for e, v in zip(walls, values)]
                     + [{"kind": "formula", "text": f"sum of {len(walls)} wall {what}s = {total:.2f} {symbol}"}],
            explanation=f"Total {what} of the {len(walls)} walls on storey '{storey_name}': {total:.2f} {symbol}, "
                        "summed from each wall's base quantities in the model.",
            meta={"interpreted": f"sum of wall {what} (model quantity) on storey '{storey_name}'",
                  "checks": {"walls on the storey": len(walls), f"walls stating a {what}": len(walls)},
                  "ifc": ifc_rel},
            **common)

    # ---- areas -------------------------------------------------------------
    if measure == "area":
        if storey is None:
            return decline(f"no storey of this model matches {quoted}; its storeys are {names}")
        on_storey = [s for s in index["spaces"] if s["storey"] == storey]

        if len(other_quoted) == 1:  # one named room on a storey
            label = other_quoted[0]
            hits = [s for s in on_storey if norm(label) in _space_labels(s)]
            if not hits:
                return decline(f"no room '{label}' on storey '{storey_name}'")
            areas = [s["area_geometry"] if s["area_geometry"] is not None else s["area_qto_net"] for s in hits]
            if any(a is None for a in areas):
                return decline(f"room '{label}' has no usable geometry or quantity")
            if max(areas) - min(areas) > 0.02 * max(areas):
                return decline(f"{len(hits)} rooms on '{storey_name}' are called '{label}' and their areas differ")
            space = hits[0]
            sources = {"body geometry": space["area_geometry"], "footprint outline": space["area_outline"],
                       "model quantity (NetFloorArea)": space["area_qto_net"]}
            agreeing, gap = _agreement(sources)
            value = round(areas[0], 2)
            if len(agreeing) >= 2:
                confidence, note = 0.99, f"{' and '.join(agreeing)} agree"
            elif gap is not None and gap > AGREE:
                confidence, note = 0.5, f"sources differ by {gap:.1%}"
            else:
                confidence, note = 0.9, "single source"
            return Answer(
                answer=value, unit="m2", state=CHECK, confidence=confidence,
                evidence=[_element(s, area_m2=round(a, 3)) for s, a in zip(hits, areas)],
                explanation=f"Net floor area of room '{label}' on storey '{storey_name}': "
                            f"{value} m² from the space's {agreeing[0] if agreeing else 'geometry'} ({note}).",
                meta={"interpreted": f"area of space '{label}' on storey '{storey_name}'",
                      "checks": {k: v for k, v in sources.items() if v is not None},
                      "ifc": ifc_rel},
                **common)

        if not other_quoted:  # all rooms on a storey
            if kinds and "space" not in kinds:
                return decline(f"area of {kinds[0]} elements is not supported")
            missing = [s for s in on_storey if s["area_geometry"] is None and s["area_qto_net"] is None]
            if not on_storey or missing:
                return decline(f"{len(missing)} of {len(on_storey)} rooms on '{storey_name}' have no usable area")
            parts = [s["area_geometry"] if s["area_geometry"] is not None else s["area_qto_net"] for s in on_storey]
            total = sum(parts)
            qto = [s["area_qto_net"] for s in on_storey]
            outline = [s["area_outline"] for s in on_storey]
            sources = {"body geometry": total,
                       "footprint outlines": sum(outline) if all(v is not None for v in outline) else None,
                       "model quantities": sum(qto) if all(v is not None for v in qto) else None}
            agreeing, gap = _agreement(sources)
            confidence = 0.99 if len(agreeing) >= 2 else (0.5 if gap and gap > AGREE else 0.9)
            return Answer(
                answer=round(total, 2), unit="m2", state=CHECK, confidence=confidence,
                evidence=[_element(s, area_m2=round(a, 3)) for s, a in zip(on_storey, parts)]
                         + [{"kind": "formula", "text": f"sum of {len(parts)} room areas = {total:.2f} m²"}],
                explanation=f"Total net floor area of the {len(parts)} rooms on storey '{storey_name}': "
                            f"{total:.2f} m².",
                meta={"interpreted": f"sum of space areas on storey '{storey_name}'",
                      "checks": {k: v for k, v in sources.items() if v is not None}, "ifc": ifc_rel},
                **common)
        return decline("could not match the quoted names to one room and one storey")

    # ---- counts and volumes need exactly one kind ----------------------------
    if len(kinds) != 1:
        found = ", ".join(kinds) if kinds else "none"
        return decline(f"could not tell which element kind to {measure} (recognised: {found})")
    kind = kinds[0]
    if storey is not None:
        scope, in_scope = f"on storey '{storey_name}'", lambda e: e["storey"] == storey
    elif asks_about_whole_model(q.question):
        scope, in_scope = "in the whole model", lambda e: True
    else:
        return decline(f"no storey of this model matches {quoted}; its storeys are {names}")

    source = index["spaces"] if kind == "space" else index["kinds"].get(kind, [])
    elements = [e for e in source if in_scope(e)]
    classes = "/".join(ifc_index.KIND_CLASSES[kind])

    if measure == "count":
        validated = kind in VALIDATED_KINDS
        confidence, note = (0.95, "") if validated else (
            0.7, " Counting rule for this kind not yet checked on dev drawings.")
        checks = {"elements the model assigns to the storey" if storey is not None
                  else "elements in the model": len(elements)}
        if storey is not None and kind in HEIGHT_CHECKED_KINDS:
            by_height = _count_by_height(index, storey, source)
            if by_height is not None:
                checks["elements placed within the storey's height"] = by_height
                if by_height == len(elements):
                    confidence, note = 0.99, " The same number are physically placed within that storey's height."
                else:
                    confidence = 0.6
                    note = (f" But {by_height} are physically placed within that storey's height: "
                            "the model's storey assignment and the geometry differ.")
        return Answer(
            answer=len(elements), unit="count", state=CHECK, confidence=confidence,
            evidence=[_element(e) for e in elements],
            explanation=f"Counted {len(elements)} {kind} elements ({classes}) {scope}.{note}",
            meta={"interpreted": f"count of {classes} {scope}", "checks": checks, "ifc": ifc_rel},
            **common)

    # measure == "volume"
    if kind not in ifc_index.VOLUME_KINDS:
        return decline(f"volume of {kind} elements is not supported")
    if not elements:
        return decline(f"no {kind} elements {scope}")
    volumes = index["volumes"]
    geometry = [volumes.get(e["id"], {}).get("geometry") for e in elements]
    qto = [volumes.get(e["id"], {}).get("qto_net") for e in elements]
    sources = {"model quantities (NetVolume)": sum(qto) if all(v is not None for v in qto) else None,
               "body geometry": sum(geometry) if all(v is not None for v in geometry) else None}
    agreeing, gap = _agreement(sources)
    if not agreeing:
        return decline(f"some {kind} elements {scope} have neither a volume quantity nor usable geometry")
    total = sources[agreeing[0]]
    per_element = qto if agreeing[0].startswith("model") else geometry
    return Answer(
        answer=round(total, 2), unit="m3", state=CHECK,
        confidence=0.8 if len(agreeing) >= 2 else 0.6,
        evidence=[_element(e, volume_m3=round(v, 4)) for e, v in zip(elements, per_element)]
                 + [{"kind": "formula", "text": f"sum of {len(elements)} {kind} volumes = {total:.2f} m³"}],
        explanation=f"Total net volume of the {len(elements)} {kind} elements {scope}: {total:.2f} m³ "
                    f"from {agreeing[0]}. Volume convention not yet checked against dev answers.",
        meta={"interpreted": f"sum of net volumes of {classes} {scope}",
              "checks": {k: v for k, v in sources.items() if v is not None}, "ifc": ifc_rel},
        **common)
