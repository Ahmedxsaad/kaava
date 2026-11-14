"""DXF engine: counts and areas of a floor plan from its vector drawing.

Works on plans that come with a DXF of the same floor (Swiss Dwellings: a PDF, a DXF
and a PNG per floor). No language model, zero cost. Every counted item and measured
room comes back as geometry in drawing coordinates, ready to be drawn on the plan.

What it answers:
  * How many rooms labelled 'X'                       rooms whose printed name is X
  * Net floor area of room N ('X')                    outline of the room numbered N
  * Total net area of all rooms labelled 'X'          sum of those outlines
  * Total net floor area of all numbered indoor rooms numbered rooms minus outdoor ones
  * Net floor area of apartment A                     the unit tagged A, minus outdoor rooms
  * How many apartments / doors / windows / toilets / washbasins / bathtubs / showers

Second source: the PDF of the same floor is read separately (pdf_labels). Where the
DXF geometry and the PDF's printed labels agree, the answer gets a high score; where a
single source is all there is, a lower one; where the drawing does not say what a room
is (a numbered room with no name), the affected totals are scored low and say why.

State is "check" for everything until thresholds are calibrated (see verify.py).
"""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

from ..answer import Answer
from ..data import Dataset, Question
from ..questions import (apartment_in, asks_about_numbered_rooms, kinds_in, measure_of, norm,
                         quoted_strings, room_ref_in)
from ..scoring import CHECK
from . import dxf_index, pdf_labels

METHOD = "dxf-engine:v1"
AGREE_REL = 0.005   # areas from two sources agree within 0.5%...
AGREE_ABS = 0.011   # ...or within the rounding of a label printed to two decimals
# Room names the reference treats as outdoor space, as prefixes (DE / FR / IT / EN).
# Seating areas and winter gardens were added from dev drawings of the full dataset.
OUTDOOR = ("balkon", "balcon", "balcony", "loggia", "terras", "terraz", "terrace",
           "sitzplatz", "jardin d'hiver", "wintergarten", "giardino d'inverno", "veranda")
FIXTURE_KINDS = ("toilet", "washbasin", "bathtub", "shower")

_indexes: dict[str, dict] = {}
_lock = threading.Lock()


def _index(path: Path) -> dict:
    key = str(path)
    with _lock:
        if key not in _indexes:
            _indexes[key] = dxf_index.load_index(path)
        return _indexes[key]


def _sibling(q: Question, dataset: Dataset, ext: str) -> str | None:
    return next((f for f in dataset.formats(q.path) if PurePosixPath(f).suffix.lower() == ext), None)


def is_outdoor(room: dict) -> bool:
    return norm(room["name"] or "").startswith(OUTDOOR)


def _same_area(a: float, b: float) -> bool:
    return abs(a - b) <= max(AGREE_ABS, AGREE_REL * abs(b))


def _room_label(room: dict) -> str:
    return " ".join(x for x in (room["name"], room["number"]) if x) or "unnamed"


def _room_evidence(room: dict) -> dict:
    return {"kind": "polygon", "space": "dxf_m", "points": room["polygon"],
            "label": _room_label(room), "area_m2": round(room["area"], 2)}


def _box_evidence(item: dict, label: str) -> dict:
    return {"kind": "box", "space": "dxf_m", "bbox": item["bbox"], "label": label}


def _sum_answer(rooms: list[dict], what: str, pdf: dict | None, common: dict, *,
                unknown_rooms: list[dict] = (), interpreted: str) -> Answer:
    """Sum room outlines; cross-check against the areas printed for the same rooms."""
    total = sum(r["area"] for r in rooms)
    # Compare every room with the area printed for it, in the DXF and in the PDF.
    checked = mismatched = 0
    for r in rooms:
        printed = [r["printed_area"]] if r["printed_area"] is not None else []
        if pdf is not None and r["number"] in pdf["areas"]:
            printed.append(pdf["areas"][r["number"]])
        if printed:
            checked += 1
            mismatched += any(not _same_area(r["area"], v) for v in printed)
    checks = {"outlines in the DXF": round(total, 3),
              "rooms with a printed area": checked, "of which differ from their outline": mismatched}
    if unknown_rooms:
        confidence = 0.6
        note = (f" {len(unknown_rooms)} numbered room(s) have no name on the drawing "
                f"({', '.join(r['number'] for r in unknown_rooms)}): they are counted as indoor rooms, "
                "which the drawing does not confirm.")
    elif mismatched:
        confidence, note = 0.5, f" {mismatched} room(s) have a printed area that differs from their outline."
    elif rooms and checked == len(rooms):
        confidence, note = 0.99, " Every room's printed area agrees with its outline."
    else:
        confidence = 0.85
        note = (f" {checked} of {len(rooms)} rooms have a printed area to compare with (all agree)."
                if checked else " No areas are printed on this drawing to compare with.")
    parts = " + ".join(f"{r['area']:.2f}" for r in rooms[:12]) + (" + ..." if len(rooms) > 12 else "")
    return Answer(
        answer=round(total, 2), unit="m2", state=CHECK, confidence=confidence,
        evidence=[_room_evidence(r) for r in rooms]
                 + [{"kind": "formula", "text": f"{parts} = {total:.2f} m²"}],
        explanation=f"{what}: {len(rooms)} room outline(s), {total:.2f} m² in total.{note}",
        meta={"interpreted": interpreted, "checks": checks}, **common)


def answer(q: Question, dataset: Dataset) -> Answer:
    common = dict(method=METHOD, cost_usd=0.0)

    def decline(reason: str) -> Answer:
        return Answer.decline(reason, **common)

    dxf_rel = _sibling(q, dataset, ".dxf")
    if dxf_rel is None:
        return decline("this drawing has no DXF")
    measure = measure_of(q.question)
    if measure not in ("count", "area"):
        return decline("only counts and areas are read from a floor-plan DXF")

    index = _index(dataset.abspath(dxf_rel))
    pdf_rel = _sibling(q, dataset, ".pdf")
    pdf = pdf_labels.labels_for(dataset.abspath(pdf_rel) if pdf_rel else None)
    rooms = index["rooms"]
    numbered = [r for r in rooms if r["number"]]
    unnamed = [r for r in numbered if not r["name"]]
    quoted = quoted_strings(q.question)
    label = quoted[0] if quoted else None

    def named(text: str) -> list[dict]:
        return [r for r in rooms if r["name"] and norm(r["name"]) == norm(text)]

    # ------------------------------------------------------------------ areas
    if measure == "area":
        if label is not None:
            ref = room_ref_in(q.question)
            if ref is not None:  # "room N ('X')": one room, by its number
                hits = [r for r in numbered if norm(r["number"]) == norm(ref)]
                if len(hits) != 1:
                    return decline(f"{len(hits)} rooms on this drawing carry the number {ref}")
                room = hits[0]
                asked = [room["number"]]
                if room["name"] and norm(room["name"]) != norm(label):
                    return decline(f"room {asked[0]} is labelled '{room['name']}' on the drawing, not '{label}'")
                checks = {"outline in the DXF": round(room["area"], 3)}
                if room["printed_area"] is not None:
                    checks["area printed in the DXF"] = room["printed_area"]
                if pdf is not None and asked[0] in pdf["areas"]:
                    checks["area printed in the PDF"] = pdf["areas"][asked[0]]
                others = [v for k, v in checks.items() if k != "outline in the DXF"]
                agree = [v for v in others if _same_area(room["area"], v)]
                if others and len(agree) == len(others):
                    confidence = 0.99
                    note = f"the area printed in the room ({others[0]:.2f} m²) agrees"
                elif others:
                    confidence, note = 0.5, f"the area printed in the room is {others[0]:.2f} m²: they differ"
                else:
                    confidence, note = 0.85, "no area is printed in this room to compare with"
                return Answer(
                    answer=round(room["area"], 2), unit="m2", state=CHECK, confidence=confidence,
                    evidence=[_room_evidence(room)],
                    explanation=f"Room {asked[0]} ('{label}'): outline area {room['area']:.2f} m²; {note}.",
                    meta={"interpreted": f"net area of the room numbered {asked[0]}", "checks": checks},
                    **common)
            return _sum_answer(named(label), f"Rooms labelled '{label}'", pdf, common,
                               interpreted=f"sum of the areas of rooms named '{label}'")

        apartment = apartment_in(q.question)
        if apartment is not None:
            units = [u for u in index["units"] if apartment in u["tags"]]
            if len(units) != 1:
                return decline(f"{len(units)} units on this drawing are tagged '{apartment}'")
            members = [rooms[i] for i in units[0]["rooms"] if rooms[i]["number"]]
            indoor = [r for r in members if not is_outdoor(r)]
            return _sum_answer(indoor, f"Apartment {apartment}, rooms without balconies, loggias and terraces",
                               pdf, common, unknown_rooms=[r for r in indoor if not r["name"]],
                               interpreted=f"sum of the numbered indoor rooms inside unit {apartment}")

        if asks_about_numbered_rooms(q.question):
            indoor = [r for r in numbered if not is_outdoor(r)]
            return _sum_answer(indoor, "Numbered indoor rooms of this floor", pdf, common,
                               unknown_rooms=[r for r in indoor if not r["name"]],
                               interpreted="sum of all numbered rooms except balconies, loggias and terraces")
        return decline("could not tell which rooms the area is asked for")

    # ------------------------------------------------------------------ counts
    if label is not None:  # rooms labelled 'X'
        hits = named(label)
        checks = {"room names in the DXF": len(hits)}
        if pdf is not None:
            checks["room names in the PDF"] = sum(n for name, n in pdf["names"].items() if norm(name) == norm(label))
        agree = len(set(checks.values())) == 1
        confidence = (0.99 if len(checks) == 2 else 0.9) if agree else 0.5
        return Answer(
            answer=len(hits), unit="count", state=CHECK, confidence=confidence,
            evidence=[_room_evidence(r) for r in hits],
            explanation=f"{len(hits)} room(s) are labelled '{label}'."
                        + ("" if agree else f" The PDF shows {checks['room names in the PDF']}: they differ."),
            meta={"interpreted": f"count of rooms whose printed name is '{label}'", "checks": checks},
            **common)

    kinds = kinds_in(q.question)
    if len(kinds) != 1:
        found = ", ".join(kinds) if kinds else "none"
        return decline(f"could not tell what to count (recognised: {found})")
    kind = kinds[0]

    if kind == "apartment":
        tagged = [u for u in index["units"] if u["tags"]]
        checks = {"tagged unit outlines in the DXF": len(tagged),
                  "apartment tags in the DXF": len({r["apartment_tag"] for r in rooms if r["apartment_tag"]})}
        if pdf is not None:
            checks["apartment tags in the PDF"] = len(set(pdf["tags"]))
        agree = len(set(checks.values())) == 1
        return Answer(
            answer=len(tagged), unit="count", state=CHECK,
            confidence=(0.99 if len(checks) == 3 else 0.9) if agree else 0.5,
            evidence=[{"kind": "polygon", "space": "dxf_m", "points": u["polygon"],
                       "label": f"apartment {u['tags'][0]}"} for u in tagged],
            explanation=f"{len(tagged)} unit outline(s) carry an apartment tag "
                        f"({', '.join(sorted(u['tags'][0] for u in tagged))}).",
            meta={"interpreted": "count of unit outlines that carry an apartment tag", "checks": checks},
            **common)

    if kind in ("door", "window"):
        items = index["doors"] if kind == "door" else index["windows"]
        checks = {f"{kind} outlines in the DXF": len(items)}
        sane = True
        if kind == "door":  # each door frame has one swing arc per leaf: one or two
            checks["door swing arcs in the DXF"] = index["door_arcs"]
            sane = len(items) <= index["door_arcs"] <= 2 * len(items) or not items
        # The same outline can be in the file more than once (a door between two units, listed for each).
        # The count is of outlines, as the dataset counts; how many distinct places they mark is said too.
        distinct = len({tuple(item["bbox"]) for item in items})
        twice = len(items) - distinct
        if twice:
            checks[f"distinct {kind} places (outlines lying exactly on another counted once)"] = distinct
        # Outlines that coincide: the answer stays as the dataset counts it, and agrees with the dataset,
        # so no score measured against the dataset can tell it apart. But a person ordering doors needs
        # the distinct places. Such an answer is therefore marked as one that is never Verified.
        meta = {"interpreted": f"count of {kind} outlines on the {kind.upper()} layer", "checks": checks}
        if twice:
            meta["not_verifiable"] = f"{twice} of the {len(items)} outlines lie exactly on another: {distinct} distinct places"
        return Answer(
            answer=len(items), unit="count", state=CHECK, confidence=0.9 if sane else 0.5,
            evidence=[_box_evidence(item, kind) for item in items],
            explanation=f"{len(items)} {kind} outline(s) on the {kind.upper()} layer."
                        + (f" {twice} of them lie exactly on another outline: {distinct} distinct places." if twice else "")
                        + ("" if sane else " The number of swing arcs does not fit that count."),
            meta=meta,
            **common)

    if kind in FIXTURE_KINDS:
        items = [f for f in index["fixtures"] if f["kind"] == kind]
        sane = index["orphan_drains"] == 0
        return Answer(
            answer=len(items), unit="count", state=CHECK, confidence=0.9 if sane else 0.5,
            evidence=[_box_evidence(item, kind) for item in items],
            explanation=f"{len(items)} {kind} symbol(s) recognised by shape on the FIXTURE layer."
                        + ("" if sane else f" {index['orphan_drains']} drain(s) belong to no recognised fixture."),
            meta={"interpreted": f"count of {kind} symbols (by shape) on the FIXTURE layer",
                  "checks": {"fixture symbols by kind": {k: sum(1 for f in index['fixtures'] if f['kind'] == k)
                                                         for k in FIXTURE_KINDS},
                             "unassigned drains": index["orphan_drains"]}},
            **common)
    return decline(f"counting {kind} elements is not supported on a floor-plan DXF")
