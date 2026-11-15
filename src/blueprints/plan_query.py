"""A free-form question about a floor plan, read as a query over the plan's own rooms.

The DXF engine reads questions with rules, which cover the dataset's phrasings. A person
asks otherwise: "the area of the Essen and the Reduit and Zimmer", "which is the biggest
room", "how much skirting do I need in the Korridor". Restating such a question in one of
the engine's own phrasings (interpret.py) works only when one phrasing fits.

Here the language model is shown the plan's inventory (every room with an id, its number,
name, apartment and area; how many doors, windows and fixtures the plan holds) and returns
a query: which rooms, what to measure, how to combine. The engine runs the query on the
geometry. The model never sees the drawing and never states a number; it selects.

Because the answer rests on a model's reading of the question, it is never Verified: the
query is shown in words, and the rooms it selected are drawn, so a person can see what
was answered.
"""

from __future__ import annotations

import time

from dataclasses import dataclass, field

from .answer import Answer
from .scoring import CHECK

MEASURES = ("area", "perimeter", "baseboard", "count_rooms", "count_items", "none")
COMBINE = ("sum", "largest", "smallest", "average")
ITEMS = ("door", "window", "toilet", "washbasin", "bathtub", "shower", "apartment")

SYSTEM = """You turn a question about one floor plan into a query over the plan's own list of rooms and items. You never compute or state a number, and you never answer the question: you say which rooms it is about and what is to be measured. A program then measures them on the drawing.

You get the question, in any language and phrasing, and the plan's inventory: every room with an id, its number, its name as printed on the plan, the apartment it belongs to, whether it is outdoors (balcony, terrace, loggia), and its floor area; and how many doors, windows and fixtures the plan holds.

Reply with:
- measure: area (floor area of rooms), perimeter (length of the walls around rooms), baseboard (skirting: the perimeter less the door openings), count_rooms (how many rooms match), count_items (how many doors, windows or fixtures), or none.
- rooms: the ids of the rooms the question is about. A name the question gives ("the Zimmer", "bedrooms", "in Stanza") means every room with that name, unless the question singles one out by its number. An apartment means all its rooms; leave outdoor rooms out of an apartment's or floor's area unless the question asks for them. For count_items: empty when the question is about the whole plan; the rooms it names when it asks for the doors, windows or fixtures of certain rooms or of an apartment.
- item: for count_items, one of door, window, toilet, washbasin, bathtub, shower, apartment. Otherwise empty.
- combine: sum (add the rooms up; also for a single room), largest, smallest (the one room with the largest or smallest value), or average.
- cannot: true when the inventory cannot answer the question (a height, a material, a cost, something not on a floor plan). Then say in reading what is missing and what the drawing does offer instead (for a room name it does not have: the names it does have).
- reading: one short sentence saying how you read the question, naming the rooms by name."""

SCHEMA = {
    "type": "object",
    "properties": {
        "measure": {"type": "string", "enum": list(MEASURES)},
        "rooms": {"type": "array", "items": {"type": "integer"}},
        "item": {"type": "string", "enum": ["", *ITEMS]},
        "combine": {"type": "string", "enum": list(COMBINE)},
        "cannot": {"type": "boolean"},
        "reading": {"type": "string"},
    },
    "required": ["measure", "rooms", "item", "combine", "cannot", "reading"],
    "additionalProperties": False,
}

# The engine's own phrasing for a count over the whole plan, which its rules then read.
ITEM_QUESTIONS = {
    "door": "How many doors (interior doors and entrance doors together) are drawn on this floor plan?",
    "window": "How many windows are drawn on this floor plan?",
    "toilet": "How many toilets are drawn on this floor plan?",
    "washbasin": "How many washbasins are drawn on this floor plan?",
    "bathtub": "How many bathtubs are drawn on this floor plan?",
    "shower": "How many showers are drawn on this floor plan?",
    "apartment": "How many apartments (residential units) are shown on this floor plan?",
}
NEAR_M = 0.3  # a door this close to a room's outline opens into that room
ON_ROOM = 0.5  # ...when at least this share of its length runs along the room


def lies_on(item: dict, shape) -> bool:
    """Whether a door or a window sits in a wall of this room.

    Being near the room is not enough: a window in the next wall along touches the room's corner
    with its end. So the test is on the item's length: the line along its middle, end to end, must
    run beside the room for at least half of it.
    """
    from shapely.geometry import LineString, Polygon, box

    outline = item.get("polygon") or []
    body = Polygon(outline) if len(outline) >= 3 else box(*item["bbox"])
    frame = body.minimum_rotated_rectangle
    if frame.geom_type != "Polygon":
        return shape.exterior.distance(body) < NEAR_M
    a, b, c, d = list(frame.exterior.coords)[:4]
    mid = lambda p, q: ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)  # noqa: E731
    axis = max(LineString([mid(a, b), mid(c, d)]), LineString([mid(b, c), mid(d, a)]), key=lambda line: line.length)
    if axis.length == 0:
        return shape.exterior.distance(body) < NEAR_M
    return axis.intersection(shape.buffer(NEAR_M)).length >= ON_ROOM * axis.length


@dataclass
class Query:
    measure: str = "none"
    rooms: list[int] = field(default_factory=list)
    item: str = ""
    combine: str = "sum"
    cannot: bool = False
    reading: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None


def rooms_of(index: dict) -> list[dict]:
    """The plan's rooms as the query sees them: shafts are not rooms."""
    return [r for r in index.get("rooms", []) if not r.get("shaft")]


def inventory(index: dict, is_outdoor) -> str:
    """The plan's rooms and items as text for the language model. A room's id is its place in `rooms_of`."""
    tags = {i: (unit.get("tags") or [unit.get("tag")] or [""])[0] for unit in index.get("units", [])
            for i in unit.get("rooms", [])}
    all_rooms = index.get("rooms", [])
    lines = ["Rooms (id | number | name | apartment | outdoors | area in m²):"]
    for n, room in enumerate(rooms_of(index)):
        place = all_rooms.index(room)
        lines.append(f"{n} | {room.get('number') or '-'} | {room.get('name') or '(no name)'} | "
                     f"{tags.get(place) or '-'} | {'yes' if is_outdoor(room) else 'no'} | {room['area']:.2f}")
    fixtures: dict[str, int] = {}
    for fixture in index.get("fixtures", []):
        fixtures[fixture["kind"]] = fixtures.get(fixture["kind"], 0) + 1
    lines += ["", f"Items on the whole plan: {len(index.get('doors', []))} door outlines, "
                  f"{len(index.get('windows', []))} window outlines"
                  + "".join(f", {count} {kind}" for kind, count in sorted(fixtures.items()))
                  + f", {len(index.get('units', []))} apartments."]
    return "\n".join(lines)


def ask(question: str, index: dict, llm, is_outdoor) -> Query:
    """Have `llm` (a text model adapter from vlm.py) read the question as a query."""
    reply = llm.ask(SYSTEM, [], f"Question: {question}\n\n{inventory(index, is_outdoor)}", SCHEMA)
    common = dict(model=getattr(reply, "model", "") or getattr(llm, "model", ""),
                  cost_usd=reply.cost_usd or 0.0, latency_ms=reply.latency_ms or 0.0)
    if reply.error:
        return Query(cannot=True, reading="The language model could not be reached just now (a connection problem, "
                                          "not something about this drawing). Ask again.", error=reply.error, **common)
    data = reply.data or {}
    count = len(rooms_of(index))
    rooms = sorted({i for i in data.get("rooms") or [] if isinstance(i, int) and 0 <= i < count})
    measure = data.get("measure") if data.get("measure") in MEASURES else "none"
    return Query(measure=measure, rooms=rooms, item=data.get("item") if data.get("item") in ITEMS else "",
                 combine=data.get("combine") if data.get("combine") in COMBINE else "sum",
                 cannot=bool(data.get("cannot")) or measure == "none", reading=str(data.get("reading") or ""), **common)


def same_reading(a: Query, b: Query) -> bool:
    """Whether two readings of a question ask the engine for the same thing."""
    if a.cannot or b.cannot:
        return a.cannot and b.cannot
    if a.measure != b.measure or sorted(a.rooms) != sorted(b.rooms):
        return False
    if a.measure == "count_items":
        return a.item == b.item
    return a.measure == "count_rooms" or a.combine == b.combine


def ask_twice(question: str, index: dict, llm, is_outdoor, other=None) -> tuple[Query, Query]:
    """Two independent readings of the question, side by side. Only readings that agree are run.

    `other`, when given, is a different model for the second reading (Gemma beside Gemini): two
    unrelated models that come to the same query are stronger evidence than one model agreeing
    with itself. When it cannot be reached, the second reading falls back to the first model.
    """
    from concurrent.futures import ThreadPoolExecutor

    def reading(n: int):
        if n == 1 and other is not None:
            got = ask(question, index, other, is_outdoor)
            if not got.error:
                return got  # read by the second, different model
        got = ask(question, index, llm, is_outdoor)
        for pause in (1.5, 4.0):  # a dropped connection is not a reason to say "can't answer"
            if not got.error:
                break
            time.sleep(pause)
            again = ask(question, index, llm, is_outdoor)
            again.cost_usd += got.cost_usd
            got = again
        return got

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(reading, range(2))
    first.cost_usd += second.cost_usd
    first.latency_ms = max(first.latency_ms, second.latency_ms)
    return first, second


def in_words(query: Query, index: dict) -> str:
    """The query as a sentence: what was actually answered."""
    rooms = rooms_of(index)
    names = [f"{rooms[i].get('number') or ''} {rooms[i].get('name') or ''}".strip() or f"room {i}" for i in query.rooms]
    listed = ", ".join(names[:10]) + (f" and {len(names) - 10} more" if len(names) > 10 else "")
    if query.measure == "count_items":
        return f"count of {query.item}s on the whole plan" if not query.rooms else f"count of the {query.item}s of: {listed}"
    if query.measure == "count_rooms":
        return f"count of rooms: {listed}"
    what = {"area": "floor area", "perimeter": "perimeter", "baseboard": "baseboard length"}[query.measure]
    how = {"sum": "sum of the", "largest": "the largest", "smallest": "the smallest", "average": "average"}[query.combine]
    return f"{how} {what} of: {listed}"


def run(query: Query, index: dict, dxf, common: dict) -> Answer:
    """Run a query over rooms on the plan's geometry. `dxf` is the DXF engine module (its room evidence and sums)."""
    rooms = [rooms_of(index)[i] for i in query.rooms]
    words = in_words(query, index)
    if not rooms:
        return Answer.decline("the question names no room that is on this plan", **common)
    answer = _run(query, rooms, index, dxf, words, common)
    if answer.answer is not None:
        # What was done, in order, for whoever wants to follow it.
        names = sorted({r.get("name") or "(no name)" for r in rooms})
        searched = [f"Looked through the plan's {len(rooms_of(index))} rooms",
                    f"{len(rooms)} match: {', '.join(names[:8])}" + (" …" if len(names) > 8 else "")]
        answer.meta = {**answer.meta, "trace": [["Read the question as a query, twice", f"{words}. Both readings ask for the same thing."],
                                                searched, *answer.meta.get("trace", [])]}
    return answer


def _run(query: Query, rooms: list[dict], index: dict, dxf, words: str, common: dict) -> Answer:
    from shapely.geometry import Polygon

    if query.measure == "count_items":
        return _items_of(query, rooms, index, dxf, words, common)
    if query.measure == "count_rooms":
        return Answer(answer=len(rooms), unit="count", state=CHECK, confidence=0.6,
                      evidence=[dxf._room_evidence(r) for r in rooms],
                      explanation=f"{len(rooms)} room{'' if len(rooms) == 1 else 's'} match: "
                                  + ", ".join(f"{r.get('number') or ''} {r.get('name') or ''}".strip() for r in rooms[:12]) + ".",
                      meta={"interpreted": words, "checks": {"rooms selected": len(rooms)}}, **common)

    def length_of(room: dict) -> tuple[float, list[dict]]:
        """A room's wall length (its outline and the rings of its holes), and the doors that open into it."""
        shape = Polygon(room["polygon"])
        total = shape.exterior.length + sum(Polygon(ring).exterior.length for ring in room.get("hole_rings", []))
        doors, seen = [], set()
        for door in index.get("doors", []):
            key = tuple(door["bbox"])
            if key in seen:  # an outline the file holds twice is one door
                continue
            seen.add(key)
            if lies_on(door, shape):
                x0, y0, x1, y1 = door["bbox"]
                doors.append({"bbox": door["bbox"], "width": max(x1 - x0, y1 - y0)})
        return total, doors

    if query.measure == "area":
        values = [(r, r["area"]) for r in rooms]
    else:
        values = []
        for r in rooms:
            total, doors = length_of(r)
            values.append((r, total - (sum(d["width"] for d in doors) if query.measure == "baseboard" else 0.0)))
    unit = "m2" if query.measure == "area" else "m"
    sign = "m²" if unit == "m2" else "m"

    # What was taken out of an outline is said wherever an area is given (a perimeter says its own, below).
    by_area = query.measure == "area"
    if query.combine in ("largest", "smallest"):
        room, value = (max if query.combine == "largest" else min)(values, key=lambda pair: pair[1])
        name = f"{room.get('number') or ''} {room.get('name') or ''}".strip()
        holes, hole_checks, hole_steps = dxf.holes_note([room]) if by_area else ("", {}, [])
        compared = dxf.holes_note(rooms)[1] if by_area else {}
        among = (f" {sum(1 for r in rooms if r.get('holes'))} of the rooms compared are drawn around something; "
                 f"each was compared by its area with that taken out." if compared and not holes else "")
        return Answer(answer=round(value, 2), unit=unit, state=CHECK, confidence=0.6,
                      evidence=[dxf._room_evidence(room)],
                      explanation=f"The {query.combine} of the {len(rooms)} rooms asked about is {name}: {value:.2f} {sign}.{holes}{among}",
                      meta={"interpreted": words, "checks": {"rooms compared": len(rooms), **hole_checks},
                            "trace": [[f"Compared the {len(rooms)} rooms by {'floor area' if by_area else 'length'}",
                                       f"{name} is the {query.combine}: {value:.2f} {sign}"], *hole_steps]}, **common)
    if query.measure == "area" and query.combine == "sum":
        # The engine's own sum, with its check of every room against the area printed in it.
        summed = dxf._sum_answer(rooms, "Rooms selected", None, common, interpreted=words)
        summed.confidence = min(summed.confidence or 0.6, 0.85)
        return summed
    total = sum(value for _, value in values)
    if query.combine == "average":
        total /= len(values)
    parts = (" + ".join((dxf.area_in_parts(room) if by_area else f"{value:.2f}") for room, value in values[:12])
             + (" + ..." if len(values) > 12 else ""))
    working = (f"({parts}) / {len(values)} = {total:.2f} {sign}" if query.combine == "average"
               else f"{parts} = {total:.2f} {sign}" if len(values) > 1 else f"{total:.2f} {sign}")
    evidence = [dxf._room_evidence(r) for r in rooms]
    note = ""
    if query.measure == "baseboard":
        openings = [d for r in rooms for d in length_of(r)[1]]
        walls, gone = sum(length_of(r)[0] for r in rooms), sum(d["width"] for d in openings)
        evidence += [{"kind": "box", "space": "dxf_m", "bbox": d["bbox"], "role": "door opening, left out",
                      "label": f"{d['width']:.2f} m"} for d in openings]
        if query.combine == "sum":
            working = (f"{walls:.2f} m of wall − {gone:.2f} m of {len(openings)} door opening"
                       f"{'' if len(openings) == 1 else 's'} = {total:.2f} m")
        note = " The wall length is each room's outline (and the rings of any holes in it); a door opens into a room when it sits in one of the room's walls."
    elif query.measure == "perimeter":
        note = " The length of each room's outline, with the rings of any holes in it."
    evidence.append({"kind": "formula", "text": working})
    holes, hole_checks, hole_steps = dxf.holes_note(rooms) if by_area else ("", {}, [])
    return Answer(answer=round(total, 2), unit=unit, state=CHECK, confidence=0.6, evidence=evidence,
                  explanation=f"{working}.{note}{holes}",
                  meta={"interpreted": words, "checks": {"rooms measured": len(rooms), **hole_checks},
                        "trace": [["Measured each room on its outline", working], *hole_steps]}, **common)


def _items_of(query: Query, rooms: list[dict], index: dict, dxf, words: str, common: dict) -> Answer:
    """The doors, windows or fixtures that belong to certain rooms.

    A door or a window belongs to a room when it sits in one of the room's walls (a door between two
    rooms belongs to both, and is counted once). A fixture belongs to the room it stands in.
    """
    from shapely.geometry import Point, Polygon, box

    shapes = [Polygon(room["polygon"]) for room in rooms]
    names = ", ".join(f"{r.get('number') or ''} {r.get('name') or ''}".strip() for r in rooms[:8]) + (
        f" and {len(rooms) - 8} more" if len(rooms) > 8 else "")
    if query.item in ("door", "window"):
        source = index.get("doors" if query.item == "door" else "windows", [])
        found, aside, seen = [], [], set()
        for item in source:
            key = tuple(item["bbox"])
            if key in seen:  # an outline the file holds twice is one item
                continue
            seen.add(key)
            if any(lies_on(item, shape) for shape in shapes):
                found.append(item)
            elif any(shape.exterior.distance(box(*item["bbox"])) < NEAR_M for shape in shapes):
                aside.append(item)  # it ends at a corner of one of the rooms, and sits in another room's wall
        how = f"that sit in a wall of {'that room' if len(rooms) == 1 else 'one of those rooms'}"
    elif query.item == "apartment":
        return Answer.decline("apartments are counted for the whole plan, not for certain rooms", **common)
    else:
        found, aside = [], []
        for item in index.get("fixtures", []):
            if item["kind"] != query.item:
                continue
            x0, y0, x1, y1 = item["bbox"]
            if any(shape.contains(Point((x0 + x1) / 2, (y0 + y1) / 2)) for shape in shapes):
                found.append(item)
        how = f"standing in {'that room' if len(rooms) == 1 else 'those rooms'}"
    evidence = [{"kind": "box", "space": "dxf_m", "bbox": item["bbox"], "label": query.item} for item in found]
    # The rooms that were searched are drawn too, so the search can be seen and not only its result.
    searched = [{**dxf._room_evidence(room), "role": "searched"} for room in rooms]
    whole = (len({tuple(i["bbox"]) for i in index.get("doors" if query.item == "door" else "windows", [])})
             if query.item in ("door", "window") else sum(1 for f in index.get("fixtures", []) if f["kind"] == query.item))
    trace = [[f"Went through the plan's {whole} {query.item}s", f"kept the ones {how}: {len(found)}"]]
    left = ""
    if aside:
        # Shown on the drawing as well: what was looked at and left out is part of the working.
        left = (f" {len(aside)} more only touch{'es' if len(aside) == 1 else ''} a corner of "
                f"{'that room' if len(rooms) == 1 else 'those rooms'} and sit{'s' if len(aside) == 1 else ''} "
                f"in another room's wall: left out.")
        trace.append([f"Left out {len(aside)} that only touch{'es' if len(aside) == 1 else ''} a corner",
                      f"a {query.item} belongs to a room when at least half of its length runs along one of the room's walls; "
                      f"{'this one does' if len(aside) == 1 else 'these do'} not"])
        searched += [{"kind": "box", "space": "dxf_m", "bbox": item["bbox"], "role": "left out: only touches a corner"}
                     for item in aside]
    trace.append(["Counted what was kept", f"{len(found)} {query.item}{'' if len(found) == 1 else 's'}"])
    checks = {"rooms asked about": len(rooms), f"{query.item}s on the whole floor": whole,
              f"{query.item}s that belong to the rooms asked about": len(found)}
    if aside:
        checks[f"{query.item}s that only touch a corner, left out"] = len(aside)
    return Answer(answer=len(found), unit="count", state=CHECK, confidence=0.6,
                  evidence=(evidence or [{"kind": "formula", "text": f"no {query.item} belongs to {names}"}]) + searched,
                  explanation=f"{len(found)} {query.item}{'' if len(found) == 1 else 's'} {how}: {names}.{left} "
                              f"The plan holds {whole} on the whole floor.",
                  meta={"interpreted": words, "trace": trace, "checks": checks},
                  **common)
