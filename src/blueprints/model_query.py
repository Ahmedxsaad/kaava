"""A free-form question about a building model, read as a query over the model's own contents.

The IFC engine reads questions with rules, which cover the dataset's phrasings. A person
asks otherwise: "total floor area", "the biggest room", "how many storeys", "area of all
the bathrooms". Restating such a question in one of the engine's own phrasings
(interpret.py) fails when no single phrasing fits, and the engine's refusal then reads
like nonsense to the person who asked.

Here the language model is shown the model's inventory (its storeys; how many elements of
each kind stand on each; every room with an id, number, name, storey and area) and returns
a query: what to count or measure, on which storeys or of which rooms, how to combine.
The engine runs it on the model. The language model never states a number; it selects.

An answer that rests on a model's reading of the question is never Verified: the query is
shown in words and the elements it selected are lit in the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .answer import Answer
from .scoring import CHECK

MEASURES = ("count_elements", "area", "count_rooms", "count_storeys", "none")
COMBINE = ("sum", "largest", "smallest", "average")
MAX_ROOMS = 700  # rooms listed to the language model; a larger model is cut short, and the answer says so

SYSTEM = """You turn a question about one building model (IFC) into a query over the model's own contents. You never compute or state a number, and you never answer the question: you say what is to be counted or measured, and a program does it on the model.

You get the question, in any language and phrasing; the name of the drawing it was asked on; and the model's inventory: its storeys (each with an id), how many elements of each kind stand on each storey, and every room (space) with an id, its number, its name, its storey and its floor area.

Reply with:
- measure: count_elements (how many doors, windows, walls and so on), area (floor area of rooms), count_rooms (how many rooms match), count_storeys (how many storeys the model has), or none.
- kind: for count_elements, one of the element kinds in the inventory (door, window, wall, space, stair, column, beam, slab, roof, railing, ...). Otherwise empty.
- storeys: the ids of the storeys the question is about, for count_elements. If the drawing is the plan of one storey and the question says "this floor", "here" or names no storey, use that storey. If the question asks about the whole building or model, list every storey.
- rooms: the ids of the rooms the question is about, for area and count_rooms. A name the question gives ("bathrooms", "the corridors") means every room with that name or that use, on the storeys the question is about (the drawing's own storey when it names none). "Total floor area" of a floor means all its rooms.
- combine: sum (add up; also for one room), largest, smallest (the one room with the largest or smallest area), or average.
- cannot: true when the inventory cannot answer the question (a cost, a material, a height, a kind of element the model does not hold). Then say in reading what is missing and what the drawing does offer instead (for a room name it does not have: the names it does have).
- reading: one short sentence saying how you read the question, naming the storeys or rooms."""

SCHEMA = {
    "type": "object",
    "properties": {
        "measure": {"type": "string", "enum": list(MEASURES)},
        "kind": {"type": "string"},
        "storeys": {"type": "array", "items": {"type": "integer"}},
        "rooms": {"type": "array", "items": {"type": "integer"}},
        "combine": {"type": "string", "enum": list(COMBINE)},
        "cannot": {"type": "boolean"},
        "reading": {"type": "string"},
    },
    "required": ["measure", "kind", "storeys", "rooms", "combine", "cannot", "reading"],
    "additionalProperties": False,
}


@dataclass
class Query:
    measure: str = "none"
    kind: str = ""
    storeys: list[int] = field(default_factory=list)
    rooms: list[int] = field(default_factory=list)
    combine: str = "sum"
    cannot: bool = False
    reading: str = ""
    cut: bool = False  # the model has more rooms than were listed
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None


def area_of(space: dict) -> float | None:
    """A room's floor area as the engine takes it: from its geometry, else the model's own quantity."""
    return space["area_geometry"] if space.get("area_geometry") is not None else space.get("area_qto_net")


def _label(space: dict) -> str:
    name, long_name = space.get("name", ""), space.get("long_name", "")
    return f"{long_name} ({name})" if long_name and name else long_name or name or "room"


def inventory(index: dict, drawing_name: str) -> tuple[str, bool]:
    storeys = index.get("storeys", [])
    lines = [f"Drawing the question was asked on: {drawing_name}", "", "Storeys (id | name):"]
    lines += [f"{n} | {storey.get('name') or '(no name)'}" for n, storey in enumerate(storeys)]
    lines += ["", "Elements on each storey (kind: storey id = how many):"]
    for kind, items in index.get("kinds", {}).items():
        if not items:
            continue
        per: dict[int, int] = {}
        for item in items:
            if item.get("storey") is not None:
                per[item["storey"]] = per.get(item["storey"], 0) + 1
        lines.append(f"{kind}: " + ", ".join(f"{s} = {count}" for s, count in sorted(per.items()))
                     + f" (whole model: {len(items)})")
    spaces = index.get("spaces", [])
    cut = len(spaces) > MAX_ROOMS
    lines += ["", "Rooms (id | number | name | storey id | floor area in m²):"]
    for n, space in enumerate(spaces[:MAX_ROOMS]):
        area = area_of(space)
        lines.append(f"{n} | {space.get('name') or '-'} | {space.get('long_name') or '-'} | "
                     f"{space.get('storey') if space.get('storey') is not None else '-'} | "
                     f"{f'{area:.2f}' if area is not None else '-'}")
    if cut:
        lines.append(f"(and {len(spaces) - MAX_ROOMS} more rooms not listed)")
    return "\n".join(lines), cut


def ask(question: str, index: dict, drawing_name: str, llm) -> Query:
    """Have `llm` (a text model adapter from vlm.py) read the question as a query."""
    listing, cut = inventory(index, drawing_name)
    reply = llm.ask(SYSTEM, [], f"Question: {question}\n\n{listing}", SCHEMA)
    common = dict(model=getattr(reply, "model", "") or getattr(llm, "model", ""), cut=cut,
                  cost_usd=reply.cost_usd or 0.0, latency_ms=reply.latency_ms or 0.0)
    if reply.error:
        return Query(cannot=True, reading="the language model could not be reached", error=reply.error, **common)
    data = reply.data or {}
    storeys = sorted({i for i in data.get("storeys") or [] if isinstance(i, int) and 0 <= i < len(index.get("storeys", []))})
    rooms = sorted({i for i in data.get("rooms") or [] if isinstance(i, int) and 0 <= i < min(MAX_ROOMS, len(index.get("spaces", [])))})
    measure = data.get("measure") if data.get("measure") in MEASURES else "none"
    kind = str(data.get("kind") or "").strip().lower()
    return Query(measure=measure, kind=kind, storeys=storeys, rooms=rooms,
                 combine=data.get("combine") if data.get("combine") in COMBINE else "sum",
                 cannot=bool(data.get("cannot")) or measure == "none", reading=str(data.get("reading") or ""), **common)


def same_reading(a: Query, b: Query) -> bool:
    """Whether two readings of a question ask the engine for the same thing."""
    if a.cannot or b.cannot:
        return a.cannot and b.cannot
    if a.measure != b.measure:
        return False
    if a.measure == "count_storeys":
        return True
    if a.measure == "count_elements":
        return a.kind == b.kind and sorted(a.storeys) == sorted(b.storeys)
    return sorted(a.rooms) == sorted(b.rooms) and (a.measure == "count_rooms" or a.combine == b.combine)


def ask_twice(question: str, index: dict, drawing_name: str, llm) -> tuple[Query, Query]:
    """Two independent readings of the question, side by side. Only readings that agree are run."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: ask(question, index, drawing_name, llm), range(2))
    first.cost_usd += second.cost_usd
    first.latency_ms = max(first.latency_ms, second.latency_ms)
    return first, second


def in_words(query: Query, index: dict) -> str:
    """The query as a sentence: what was actually answered."""
    storeys = index.get("storeys", [])
    where = ", ".join(f"'{storeys[i].get('name')}'" for i in query.storeys)
    if query.measure == "count_storeys":
        return "count of the model's storeys"
    if query.measure == "count_elements":
        everywhere = len(query.storeys) == len(storeys) or not query.storeys
        return f"count of {query.kind} elements " + ("in the whole model" if everywhere else f"on {where}")
    spaces = index.get("spaces", [])
    names = [_label(spaces[i]) for i in query.rooms]
    listed = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
    if query.measure == "count_rooms":
        return f"count of rooms: {listed}"
    how = {"sum": "sum of the floor areas", "largest": "the largest floor area", "smallest": "the smallest floor area",
           "average": "average floor area"}[query.combine]
    return f"{how} of {len(names)} room{'' if len(names) == 1 else 's'}: {listed}"


def run(query: Query, index: dict, element, common: dict) -> Answer:
    """Run a query on the model's index. `element` makes an evidence entry from an indexed item (ifc._element)."""
    answer = _run(query, index, element, common)
    if answer.answer is not None:
        # What was done, in order, for whoever wants to follow it.
        words = in_words(query, index)
        if query.measure == "count_elements":
            held = len(index.get("kinds", {}).get(query.kind, []))
            steps = [[f"Went through the model's {held} {query.kind} elements", f"kept the ones the query asks for: {answer.answer}"],
                     ["Counted what was kept", f"{answer.answer} {query.kind} elements"]]
        elif query.measure == "count_storeys":
            steps = [["Listed the model's storeys", answer.evidence[0].get("text", "")]]
        else:
            steps = [[f"Looked through the model's {len(index.get('spaces', []))} rooms", f"{len(query.rooms)} selected"],
                     ["Read each selected room's floor area from its geometry", answer.explanation]]
        answer.meta = {**answer.meta, "trace": [["Read the question as a query, twice", f"{words}. Both readings ask for the same thing."], *steps]}
    return answer


def _run(query: Query, index: dict, element, common: dict) -> Answer:
    storeys = index.get("storeys", [])
    words = in_words(query, index)
    cut = " The model has more rooms than could be listed to the language model." if query.cut else ""

    if query.measure == "count_storeys":
        names = [storey.get("name") or "(no name)" for storey in storeys]
        return Answer(answer=len(names), unit="count", state=CHECK, confidence=0.6,
                      evidence=[{"kind": "formula", "text": "storeys in the model: " + ", ".join(names)}],
                      explanation=f"The model holds {len(names)} storeys: {', '.join(names)}. A foundation or a roof "
                                  f"level kept as a storey in the model is counted with them.",
                      meta={"interpreted": words, "checks": {"storeys in the model": len(names)}}, **common)

    if query.measure == "count_elements":
        items = index.get("kinds", {}).get(query.kind)
        if items is None:
            known = ", ".join(kind for kind, held in index.get("kinds", {}).items() if held)
            return Answer.decline(f"the model holds no element kind called '{query.kind}' (it holds: {known})", **common)
        scope = set(query.storeys) if query.storeys and len(query.storeys) < len(storeys) else None
        hits = [item for item in items if scope is None or item.get("storey") in scope]
        where = "in the whole model" if scope is None else "on " + ", ".join(f"'{storeys[i].get('name')}'" for i in sorted(scope))
        cls = hits[0].get("cls") if hits else f"Ifc{query.kind.title()}"
        evidence = [element(item) for item in hits] or [{"kind": "formula", "text": f"no {query.kind} elements {where}"}]
        return Answer(answer=len(hits), unit="count", state=CHECK, confidence=0.6, evidence=evidence,
                      explanation=f"Counted {len(hits)} {query.kind} elements ({cls}) {where}.",
                      meta={"interpreted": words, "checks": {f"{query.kind} elements {where}": len(hits),
                                                             f"{query.kind} elements in the whole model": len(items)}},
                      **common)

    spaces = index.get("spaces", [])
    rooms = [spaces[i] for i in query.rooms]
    if not rooms:
        return Answer.decline("the question names no room that is in this model", **common)
    if query.measure == "count_rooms":
        return Answer(answer=len(rooms), unit="count", state=CHECK, confidence=0.6,
                      evidence=[element(room) for room in rooms],
                      explanation=f"{len(rooms)} room{'' if len(rooms) == 1 else 's'} match.{cut}",
                      meta={"interpreted": words, "checks": {"rooms selected": len(rooms)}}, **common)

    measured = [(room, area_of(room)) for room in rooms]
    missing = [room for room, area in measured if area is None]
    if missing:
        return Answer.decline(f"{len(missing)} of the {len(rooms)} rooms have no usable geometry or quantity "
                              f"(for example {_label(missing[0])})", **common)

    def storey_of(room: dict) -> str:
        return storeys[room["storey"]].get("name", "") if room.get("storey") is not None and room["storey"] < len(storeys) else ""

    if query.combine in ("largest", "smallest"):
        room, area = (max if query.combine == "largest" else min)(measured, key=lambda pair: pair[1])
        return Answer(answer=round(area, 2), unit="m2", state=CHECK, confidence=0.6,
                      evidence=[element(room, area_m2=round(area, 3), storey=storey_of(room))],
                      explanation=f"The {query.combine} of the {len(rooms)} rooms asked about is {_label(room)} "
                                  f"on '{storey_of(room)}': {area:.2f} m².{cut}",
                      meta={"interpreted": words, "checks": {"rooms compared": len(rooms)}}, **common)
    total = sum(area for _, area in measured)
    if query.combine == "average":
        total /= len(measured)
        working = f"average of {len(measured)} room areas = {total:.2f} m²"
    else:
        parts = " + ".join(f"{area:.2f}" for _, area in measured[:10]) + (" + ..." if len(measured) > 10 else "")
        working = f"{parts} = {total:.2f} m²" if len(measured) > 1 else f"{total:.2f} m²"
    # Where the model also states its own quantities for these rooms, they are a second source.
    stated = [room.get("area_qto_net") for room in rooms]
    checks = {"sum of the rooms' areas from geometry": round(sum(area for _, area in measured), 3), "rooms measured": len(rooms)}
    if all(value is not None for value in stated):
        checks["sum of the model's own quantities (NetFloorArea)"] = round(sum(stated), 3)
    return Answer(answer=round(total, 2), unit="m2", state=CHECK, confidence=0.6,
                  evidence=[element(room, area_m2=round(area, 3), storey=storey_of(room)) for room, area in measured]
                           + [{"kind": "formula", "text": working}],
                  explanation=f"{working}, from the geometry of {len(measured)} room{'' if len(measured) == 1 else 's'}.{cut}",
                  meta={"interpreted": words, "checks": checks}, **common)
