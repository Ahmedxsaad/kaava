"""Restate a free-form question so that an exact engine can read it.

The engines read questions with rules. A question in an unexpected phrasing or
language is declined rather than guessed. Here a language model is asked to restate
such a question in the style the engine understands, using only names that exist on
the drawing; the engine then answers the restated question from geometry.

The model never produces the answer and never sees the drawing. Because the answer
now depends on its reading of the question, such an answer is never marked Verified:
the restated question is shown, so a person can see what was actually answered.
"""

from __future__ import annotations

from dataclasses import dataclass

SYSTEM = """You restate questions about one construction drawing so that a rule-based engine can read them.

You get: a question someone asked, in any language and phrasing; example questions the engine understands for this drawing; and the names that exist on the drawing.

Write the one question, in the style of the examples, that asks for exactly what the person asked.
- Keep the meaning. Do not answer the question. Do not add conditions the person did not ask for.
- Use only room names, room numbers, apartment letters, storey names and element kinds that appear in the examples or the lists. Copy them exactly, including quotes and brackets.
- If an example already asks exactly this, return that example unchanged and set matches_example to true.
- If no question in the engine's style asks for what the person wants (for example a height on a 2D plan, a material, a date, an opinion), set restated to null and say why in reason. A wrong restatement is worse than none.
- reason: one short sentence."""

SCHEMA = {
    "type": "object",
    "properties": {
        "restated": {"type": ["string", "null"]},
        "matches_example": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["restated", "matches_example", "reason"],
    "additionalProperties": False,
}

MAX_EXAMPLES = 40
MAX_NAMES = 150


@dataclass
class Restated:
    question: str | None  # the restated question, or None when the model found none
    reason: str
    model: str
    cost_usd: float
    latency_ms: float
    error: str | None = None


def build_prompt(question: str, examples: list[str], names: dict[str, list[str]]) -> str:
    lines = [f"Question asked: {question}", "", "Questions the engine understands for this drawing:"]
    lines += [f"- {e}" for e in examples[:MAX_EXAMPLES]] or ["- (none on file)"]
    for title, values in names.items():
        if values:
            shown = values[:MAX_NAMES]
            more = f" (and {len(values) - len(shown)} more)" if len(values) > len(shown) else ""
            lines += ["", f"{title}: " + "; ".join(shown) + more]
    return "\n".join(lines)


def restate(question: str, examples: list[str], names: dict[str, list[str]], llm) -> Restated:
    """Ask `llm` (any adapter from vlm.py) for the engine-style form of `question`."""
    reply = llm.ask(SYSTEM, [], build_prompt(question, examples, names), SCHEMA)
    common = dict(model=getattr(reply, "model", "") or getattr(llm, "model", ""),
                  cost_usd=reply.cost_usd or 0.0, latency_ms=reply.latency_ms or 0.0)
    if reply.error:
        return Restated(None, "the language model could not be reached", error=reply.error, **common)
    data = reply.data or {}
    restated = data.get("restated")
    if not isinstance(restated, str) or not restated.strip():
        return Restated(None, str(data.get("reason") or reply.no_answer or "no restatement"), **common)
    restated = " ".join(restated.split())
    if restated == " ".join(question.split()):
        return Restated(None, "the model returned the question unchanged", **common)
    return Restated(restated, str(data.get("reason") or ""), **common)


def names_on_dxf(index: dict) -> dict[str, list[str]]:
    """What a floor-plan DXF calls its rooms and apartments."""
    rooms = [r for r in index.get("rooms", []) if not r.get("shaft")]
    labels = sorted({r["name"] for r in rooms if r.get("name")})
    numbers = sorted({r["number"] for r in rooms if r.get("number")})
    tags = sorted({u["tag"] for u in index.get("units", []) if u.get("tag")})
    # The engine reads one room as: room 2.21 ('Pièce'). Give the pairs, so the label is not left out.
    pairs = sorted({f"{r['number']} ('{r['name']}')" for r in rooms if r.get("number") and r.get("name")})
    return {"Room labels on the plan": labels, "Room numbers on the plan": numbers,
            "Rooms, as number ('label')": pairs, "Apartments on the plan": tags,
            "Things the engine can count": ["doors", "windows", "toilets", "washbasins", "bathtubs", "showers",
                                            "apartments", "rooms with a given label"]}


def names_in_ifc(index: dict) -> dict[str, list[str]]:
    """Storeys, element kinds and rooms of an IFC model."""
    storeys = [s["name"] for s in index.get("storeys", []) if s.get("name")]
    kinds = sorted(kind for kind, items in index.get("kinds", {}).items() if items)
    rooms = []
    for space in index.get("spaces", []):
        name, long_name = space.get("name", ""), space.get("long_name", "")
        label = f"{long_name} ({name})" if long_name and name else long_name or name
        storey = storeys[space["storey"]] if space.get("storey") is not None and space["storey"] < len(storeys) else ""
        if label:
            rooms.append(f"'{label}' on '{storey}'" if storey else f"'{label}'")
    return {"Storeys of the model": [f"'{s}'" for s in storeys], "Element kinds in the model": kinds,
            "Rooms of the model": rooms}
