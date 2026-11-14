"""Working shown: the model says which printed figures matter and how they combine, the code computes.

For sheets no exact engine covers: a structural plan with dimension strings, a schedule,
an area table, a piping plan with tagged equipment. A vision model asked for the number
states one, and nothing shows where it came from. Here it may not state the number.

  1. Every text on the sheet is read at full size by a program (sheet_text.py): the
     PDF's own text layer, or the local OCR. The model gets the image and that list.
  2. The model answers in one of three forms:
       items      things to count, each identified by a printed text or a box
       figures    printed figures (dimension strings, table entries) and an expression
                  over them; it is told not to estimate sizes by eye
       text       a descriptive answer with the texts it was read from
  3. The code counts the items, evaluates the expression, converts the unit, and checks
     each figure against the text the program read at that place.

So the number is computed, every figure in it is a box on the sheet, and a figure the
model misread shows up as a disagreement with the program's reading. A person checks the
working, not the model. Answers are state "check": which figures matter is still the
model's judgement.
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path
from typing import Callable

from . import measure, sheet_text, tiles
from .answer import Answer
from .data import Dataset, Question
from .pointing import _box
from .render import can_render, render
from .scoring import CHECK

PROMPT = 4
MAX_TEXTS = 1100

SYSTEM = """You answer a question about one sheet of a construction drawing set. You get the sheet as an image and a list of the texts printed on it. The list was read by a program at full resolution; each line is `id | text | y x`, where y and x give the centre of the text on a 0-1000 grid (y from the top edge, x from the left edge). Small text may be unreadable in the image: trust the list for what a text says, and the image for where things are and how they belong together.

You do not state the final number. You give what you read and how it combines, and a program does the counting and the arithmetic. Reply in exactly one of five forms and leave the others empty.

1. items: for a question asking how many of something there are (rows of a schedule that match a condition, tagged pieces of equipment, symbols on a plan). List every instance once. text_id is the id of the printed text that identifies the instance (the door number of a schedule row, an equipment tag) when the list has it, otherwise -1. box_2d is a box around the instance in the image: [ymin, xmin, ymax, xmax] on the 0-1000 grid. label is a few words. The answer is the length of your list, so the list must be complete and hold nothing that does not count.

2. figures and expression: for a quantity (an area, a length, a volume, a total, a size). List every printed figure you use: name (a, b, c, ...), text_id (-1 when the list does not have it), box_2d around the figure, printed (exactly as printed), value (the printed figure as a plain number), unit (the unit the figure is printed in), and what it is in a few words. Do not convert units: a dimension printed as 6200 on a sheet dimensioned in millimetres has value 6200 and unit mm; 52'-8" has value 52.6667 and unit ft; 6,092 SF has value 6092 and unit ft2. The program converts. expression combines the names with + - * / and parentheses; write sum to add all the figures up. result_unit is the unit the expression's result comes out in, given the figures' own units (6200 mm times 9950 mm is in mm2): one of mm, cm, m, in, ft, mm2, cm2, m2, in2, ft2, m3, ft3, or none for a plain number. Use only figures printed on the sheet (dimension strings, schedule entries, tables, notes). Do not estimate a size by eye or from a scale bar: if the figures needed are not printed, say so.

3. text: for a descriptive answer, in the language of the question, short. sources lists the printed texts it was read from (text_id, box_2d).

4. look_closer: for a question asking how many drawn symbols there are on a plan when they are many and small and carry no identifying text (doors, windows, fixtures, columns, outlets). Do not list them. Give `what`: one sentence saying exactly what counts as one item and how it is drawn, and box_2d: the part of the sheet that holds the plan to search (leave out the title block, legends and notes). The program then looks at that part piece by piece at full size. In the other three forms, leave `what` empty.

5. measure: for the size of a room or of several rooms (floor area, perimeter, length of baseboard or skirting) when the sheet prints no figure for it but the plan is drawn to scale with a graphic scale bar. Do not estimate the size. Set quantity to area, perimeter or baseboard, and list the rooms: for each, text_id of the room's name on the plan (-1 when the list does not have it), name, box_2d: a box whose four sides lie on the inner faces of the room's walls, and rectangular: false when the room is not a plain rectangle. The program finds the walls along your box, measures the room against the scale bar, and for baseboard leaves out the door openings. In the other forms, leave quantity empty.

- If the sheet does not let you answer in one of these forms, set cannot_tell to true and say in basis what is missing.
- basis: one or two sentences on how you got there."""

_BOX = {"type": "array", "items": {"type": "integer"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"text_id": {"type": "integer"}, "box_2d": _BOX, "label": {"type": "string"}},
            "required": ["text_id", "box_2d", "label"], "additionalProperties": False}},
        "figures": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "text_id": {"type": "integer"}, "box_2d": _BOX,
                           "printed": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"},
                           "what": {"type": "string"}},
            "required": ["name", "text_id", "box_2d", "printed", "value", "unit", "what"],
            "additionalProperties": False}},
        "expression": {"type": "string"},
        "result_unit": {"type": "string"},
        "text": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object",
            "properties": {"text_id": {"type": "integer"}, "box_2d": _BOX},
            "required": ["text_id", "box_2d"], "additionalProperties": False}},
        "look_closer": {"type": "object", "properties": {"what": {"type": "string"}, "box_2d": _BOX},
                        "required": ["what", "box_2d"], "additionalProperties": False},
        "measure": {"type": "object", "properties": {
            "quantity": {"type": "string", "enum": ["", "area", "perimeter", "baseboard"]},
            "rooms": {"type": "array", "items": {
                "type": "object",
                "properties": {"text_id": {"type": "integer"}, "name": {"type": "string"}, "box_2d": _BOX,
                               "rectangular": {"type": "boolean"}},
                "required": ["text_id", "name", "box_2d", "rectangular"], "additionalProperties": False}}},
            "required": ["quantity", "rooms"], "additionalProperties": False},
        "cannot_tell": {"type": "boolean"},
        "basis": {"type": "string"},
    },
    "required": ["items", "figures", "expression", "result_unit", "text", "sources", "look_closer", "measure",
                 "cannot_tell", "basis"],
    "additionalProperties": False,
}


def method_name(vlm) -> str:
    return f"sheet-working:{vlm.label}" + (f":p{PROMPT}" if PROMPT > 1 else "")


# ----------------------------------------------------------------- units and figures

# Metres per unit, and the unit's dimension (1 length, 2 area, 3 volume).
_LINEAR = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}
UNITS: dict[str, tuple[float, int]] = {name: (factor, 1) for name, factor in _LINEAR.items()}
UNITS.update({f"{name}2": (factor ** 2, 2) for name, factor in _LINEAR.items()})
UNITS.update({f"{name}3": (factor ** 3, 3) for name, factor in _LINEAR.items()})
_ALIASES = {"sf": "ft2", "sqft": "ft2", "sq ft": "ft2", "sq. ft.": "ft2", "ft²": "ft2", "m²": "m2", "sqm": "m2",
            "sq m": "m2", "mm²": "mm2", "cm²": "cm2", "in²": "in2", "m³": "m3", "ft³": "ft3", "feet": "ft",
            "foot": "ft", "inch": "in", "inches": "in", "metre": "m", "metres": "m", "meter": "m", "meters": "m",
            "lf": "ft", "'": "ft", '"': "in"}


def unit_of(text: str | None) -> str | None:
    key = (text or "").strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in UNITS else None


_ASKED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"m²|\bm2\b|m\^2|square met|\bsq\.? ?m\b|neliömetr"), "m2"),
    (re.compile(r"ft²|\bft2\b|\bsq\.? ?ft\b|square f(ee|oo)t|\bsf\b|square footage"), "ft2"),
    (re.compile(r"m³|\bm3\b|cubic met"), "m3"),
    (re.compile(r"ft³|\bft3\b|cubic f(ee|oo)t"), "ft3"),
    (re.compile(r"\bmillimet|\bin mm\b|\(mm\)"), "mm"),
    (re.compile(r"linear met|running met|\bmet(er|re)s\b|\bin m\b|\(m\)|juoksumetr"), "m"),
    (re.compile(r"linear f(ee|oo)t|\bfeet\b|\bin ft\b|\(ft\)"), "ft"),
]


def asked_unit(question: str) -> str | None:
    """The unit the question asks for, when it names one."""
    text = question.lower()
    for pattern, unit in _ASKED:
        if pattern.search(text):
            return unit
    return None


def convert(value: float, unit: str | None, to: str | None) -> tuple[float, str | None]:
    """`value` in `unit`, expressed in `to` when both are units of the same dimension."""
    if unit is None or to is None or unit == to or UNITS[unit][1] != UNITS[to][1]:
        return value, unit
    return value * UNITS[unit][0] / UNITS[to][0], to


_FEET_INCHES = re.compile(r"""^\s*(?:(\d+)\s*['′’]\s*-?\s*)?(?:(\d+)(?:\s+|-)?)?(?:(\d+)\s*/\s*(\d+))?\s*(?:["″”]|''|in)?\s*$""")
_PLAIN = re.compile(r"[-+]?\d[\d,.]*\d|[-+]?\d")


def number_in(text: str, unit: str | None = None) -> float | None:
    """The number a printed figure states, or None when the text is not one figure.

    "6,092 SF" is 6092; "52'-8\\"" is 52.6667 feet (or 632 inches when `unit` is in);
    "5'-10 3/8\\"" is 5.8646 feet. A comma followed by exactly three digits separates thousands.
    """
    raw = text.strip()
    if "'" in raw or "′" in raw or "’" in raw or '"' in raw or "″" in raw:
        match = _FEET_INCHES.match(raw)
        if match and any(match.groups()):
            feet, inches, top, bottom = (int(g) if g else 0 for g in match.groups())
            total_inches = feet * 12 + inches + (top / bottom if bottom else 0.0)
            return total_inches if unit == "in" else total_inches / 12.0
    found = _PLAIN.findall(raw)
    if len(found) != 1:
        return None
    digits = found[0].replace(" ", "")
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", digits):
        digits = digits.replace(",", "")
    elif digits.count(",") == 1 and "." not in digits:
        digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-3, abs_tol=1e-6)


# ----------------------------------------------------------------- the expression

_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd,
            ast.Name, ast.Load, ast.Constant)


def evaluate(expression: str, values: dict[str, float]) -> float:
    """The value of an arithmetic expression over named figures. Raises ValueError on anything else."""
    text = expression.strip().replace("×", "*").replace("÷", "/").replace("−", "-")
    if text.lower() in ("sum", "sum()", "sum(all)"):
        return float(sum(values.values()))
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"not an arithmetic expression: {expression!r}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"not allowed in an expression: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numbers and figure names are allowed")
        if isinstance(node, ast.Name) and node.id not in values:
            raise ValueError(f"the expression names a figure that is not listed: {node.id}")

    def walk(node) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            return -walk(node.operand) if isinstance(node.op, ast.USub) else walk(node.operand)
        left, right = walk(node.left), walk(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise ValueError("division by zero")
        return left / right

    return walk(tree)


def spelled_out(expression: str, figures: list[dict]) -> str:
    """The expression with each name replaced by its printed figure."""
    if expression.strip().lower().startswith("sum"):
        return " + ".join(figure["printed"] for figure in figures)
    by_name = {figure["name"]: figure["printed"] for figure in figures}
    return re.sub(r"[A-Za-z_]\w*", lambda m: by_name.get(m.group(0), m.group(0)), expression).replace("*", "×")


# ----------------------------------------------------------------- the engine


PIXELS_LONG_EDGE = 6000  # a PDF page is rendered this large when its pixels are needed


def as_pixels(path: Path, index: dict) -> tuple[Path, float, dict]:
    """The sheet as an image file, how many of its pixels make one unit of the source, and the text index in pixels.

    An image is itself. A PDF page is rendered once (kept in the cache) so that symbols can be boxed
    tile by tile and walls can be measured; what is found in pixels is turned back into the page's points.
    """
    if path.suffix.lower() != ".pdf":
        return path, 1.0, index
    import hashlib

    import pymupdf

    from . import config

    with pymupdf.open(path) as doc:
        page = doc[0]
        zoom = PIXELS_LONG_EDGE / max(page.rect.width, page.rect.height)
        digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
        picture = config.REPO_ROOT / "cache" / "sheets" / f"{digest}-{PIXELS_LONG_EDGE}.png"
        if not picture.is_file():
            picture.parent.mkdir(parents=True, exist_ok=True)
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(picture)
    scaled = {**index, "width": index["width"] * zoom, "height": index["height"] * zoom,
              "items": [{**item, "box": [v * zoom for v in item["box"]]} for item in index["items"]]}
    return picture, zoom, scaled


def _to_source(box: dict, zoom: float) -> dict:
    return box if zoom == 1.0 else {**box, "bbox": [round(v / zoom, 2) for v in box["bbox"]]}


def _listing(index: dict) -> tuple[str, bool]:
    width, height = index["width"], index["height"]
    items = index["items"]
    cut = len(items) > MAX_TEXTS
    lines = []
    for item in items[:MAX_TEXTS]:
        x0, y0, x1, y1 = item["box"]
        lines.append(f"{item['id']} | {item['text']} | {round(500 * (y0 + y1) / height)} {round(500 * (x0 + x1) / width)}")
    return "\n".join(lines), cut


_PRETTY = {"mm2": "mm²", "cm2": "cm²", "m2": "m²", "in2": "in²", "ft2": "ft²", "m3": "m³", "ft3": "ft³"}


def pretty(unit: str | None) -> str:
    return _PRETTY.get(unit or "", unit or "")


def _fmt(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") if abs(value) < 1e15 else f"{value:.6g}"


def make_engine(vlm) -> Callable[[Question, Dataset], Answer]:
    method = method_name(vlm)

    def answer(q: Question, dataset: Dataset) -> Answer:
        if not can_render(q.path):
            return Answer.decline("no image of this file to read", method=method, cost_usd=0.0, latency_ms=0.0)
        path = dataset.abspath(q.path)
        index = sheet_text.read(path)
        image = render(path, max_edge=vlm.max_edge, max_tokens=vlm.max_visual_tokens, max_pages=1)[0]
        listing, cut = _listing(index)
        how_read = "the PDF's own text layer" if index["source"] == "pdf" else "the local OCR at full size"
        prompt = f"Texts printed on the sheet ({how_read}):\n{listing}\n\nQuestion: {q.question}"
        reply = vlm.ask(SYSTEM, [image], prompt, SCHEMA)
        meta = {**reply.usage(), "images": [image.describe()], "texts": len(index["items"]), "texts_from": index["source"],
                "texts_cut": cut}
        common = dict(method=method, cost_usd=reply.cost_usd, latency_ms=reply.latency_ms, meta=meta)
        if reply.error:
            return Answer.failed(reply.error, **common)
        if reply.data is None:
            return Answer.decline(reply.no_answer or "no usable reply", **common)
        data = reply.data
        basis = str(data.get("basis") or "")
        if data.get("cannot_tell"):
            return Answer.decline(f"the sheet does not print what this needs: {basis}"[:400], **common)

        texts = {item["id"]: item for item in index["items"]}
        width, height = image.width / image.scale, image.height / image.scale  # the source's own units

        def place(entry: dict, label: str, role: str | None = None) -> dict | None:
            """Where an entry is: the printed text it names when there is one, else the model's box."""
            item = texts.get(entry.get("text_id", -1))
            if item is not None:
                box = {"kind": "box", "space": "image_px", "bbox": item["box"], "label": label[:80], "read": item["text"]}
            else:
                box = _box(entry, width, height)
                if box is None:
                    return None
                box["label"] = label[:80]
                box.pop("inside", None)
            if role:
                box["role"] = role
            return box

        figures = [f for f in data.get("figures") or [] if isinstance(f, dict)]
        items = [i for i in data.get("items") or [] if isinstance(i, dict)]
        said = str(data.get("text") or "").strip()
        closer = data.get("look_closer") if isinstance(data.get("look_closer"), dict) else {}
        what = str(closer.get("what") or "").strip()
        wanted = data.get("measure") if isinstance(data.get("measure"), dict) else {}
        rooms = [r for r in wanted.get("rooms") or [] if isinstance(r, dict)]

        if wanted.get("quantity") and rooms and not figures and not items:
            meta["basis"] = basis
            common["method"] = method + ":measured"
            return _measured(q, path, index, wanted["quantity"], rooms, width, height, common)

        if what and not figures and not items:
            # Many small symbols: the part of the sheet that holds the plan is searched tile by tile.
            region = _box(closer, width, height)
            picture, zoom, _ = as_pixels(path, index)
            result = tiles.count(vlm, picture, f"{q.question}\nWhat counts as one item: {what}",
                                 [v * zoom for v in region["bbox"]] if region else None)
            result["boxes"] = [_to_source(box, zoom) for box in result["boxes"]]
            common["cost_usd"] = (reply.cost_usd or 0.0) + result["cost_usd"]
            common["latency_ms"] = (reply.latency_ms or 0.0) + result["latency_ms"]
            meta["interpreted"] = f"count of symbols boxed tile by tile. Counted as: {what}"
            meta["basis"] = basis
            common["method"] = method + ":tiles"
            meta["checks"] = {"tiles looked at": result["sent"], "boxes before merging tiles": result["found_before_merge"],
                              "boxes after merging": len(result["boxes"])}
            if result["errors"] and len(result["errors"]) >= max(1, result["sent"] // 2):
                return Answer.decline(f"the model failed on {len(result['errors'])} of {result['sent']} tiles: {result['errors'][0]}"[:300], **common)
            if result["errors"]:
                meta["checks"]["tiles that failed"] = len(result["errors"])
            found = result["boxes"]
            evidence = found or [{"kind": "formula", "text": "the model marked nothing of this kind"}]
            return Answer(answer=len(found), unit="count", state=CHECK, confidence=0.5 if result["errors"] else 0.6,
                          evidence=evidence,
                          explanation=f"{len(found)} boxed by the model on {result['sent']} tiles of the sheet at full size, "
                                      f"merged and counted by the code.",
                          **common)

        if figures:
            return _quantity(q, figures, str(data.get("expression") or ""), data.get("result_unit"),
                             texts, index, place, basis, common)
        if items:
            seen: set[int] = set()
            evidence = []
            for entry in items:
                text_id = entry.get("text_id", -1)
                if text_id in texts:
                    if text_id in seen:  # the same printed text named twice is one item
                        continue
                    seen.add(text_id)
                box = place(entry, str(entry.get("label") or ""))
                if box:
                    evidence.append(box)
            named = len(seen)
            meta["interpreted"] = "count of the items the model listed on the sheet"
            meta["basis"] = basis
            meta["checks"] = {"items listed": len(evidence), "identified by a printed text": named}
            return Answer(answer=len(evidence), unit="count", state=CHECK,
                          confidence=0.8 if named == len(evidence) else 0.6, evidence=evidence,
                          explanation=f"{len(evidence)} listed on the sheet by the model, counted by the code; "
                                      f"{named} of them are identified by a text printed on the sheet.",
                          **common)
        if said:
            sources = [box for entry in data.get("sources") or [] if isinstance(entry, dict)
                       for box in [place(entry, "read here", role="read here")] if box]
            return Answer(answer=said, state=CHECK, confidence=0.7 if sources else 0.5,
                          evidence=sources, explanation=basis, **common)
        return Answer.decline(f"the model gave nothing to count or compute: {basis}"[:400], **common)

    return answer


def _quantity(q: Question, figures: list[dict], expression: str, result_unit, texts: dict, index: dict,
              place, basis: str, common: dict) -> Answer:
    values: dict[str, float] = {}
    evidence = []
    checks: dict[str, str] = {}
    confirmed = differs = 0
    for figure in figures:
        name = str(figure.get("name") or "").strip()
        try:
            value = float(figure["value"])
        except (KeyError, TypeError, ValueError):
            return Answer.decline(f"figure {name or '?'} has no usable value", **common)
        if not re.fullmatch(r"[A-Za-z_]\w*", name) or name in values:
            return Answer.decline(f"figure names must be distinct plain names (got {name!r})", **common)
        values[name] = value
        unit = unit_of(figure.get("unit"))
        printed = str(figure.get("printed") or "")
        figure["printed"] = printed or _fmt(value)
        label = f"{name} = {figure['printed']}" + (f" · {figure.get('what')}" if figure.get("what") else "")
        # The program's own reading of this place: the text the model named, or the texts under its box.
        item = texts.get(figure.get("text_id", -1))
        box = place(figure, label)
        candidates = [item] if item is not None else (sheet_text.at(index, box["bbox"], pad=4.0) if box else [])
        readings = [number_in(c["text"], unit) for c in candidates]
        readings = [r for r in readings if r is not None]
        if any(same(r, value) for r in readings):
            confirmed += 1
            checks[label] = "the program reads the same figure here"
        elif unit in _LINEAR and any(same(r * _LINEAR[other] / _LINEAR[unit], value) for r in readings for other in _LINEAR):
            confirmed += 1  # the model gave the printed figure in another unit of length (6200 mm as 6.2 m)
            checks[label] = "the program reads the same figure here (the model gave it in another unit)"
        elif readings:
            differs += 1
            checks[label] = f"the program reads {_fmt(readings[0])} here, the model {_fmt(value)}"
        else:
            checks[label] = "the program could not read a figure here"
        if box:
            evidence.append(box)
    try:
        result = evaluate(expression, values)
    except ValueError as exc:
        return Answer.decline(f"the working could not be computed: {exc}", **common)
    unit = unit_of(result_unit)
    shown, shown_unit = convert(result, unit, asked_unit(q.question))
    working = f"{spelled_out(expression, figures)} = {_fmt(result)}" + (f" {pretty(unit)}" if unit else "")
    if shown_unit != unit:
        working += f" = {_fmt(shown)} {pretty(shown_unit)}"
    evidence.append({"kind": "formula", "text": working})
    confidence = 0.3 if differs else (0.85 if confirmed == len(figures) else 0.6)
    common["meta"] = {**common["meta"], "checks": checks, "working": working, "basis": basis,
                      "interpreted": "computed from figures printed on the sheet"}
    note = ("The figure was read again by the program and matches." if confirmed == len(figures) == 1 else
            f"All {len(figures)} figures were read again by the program and match."
            if confirmed == len(figures) else
            f"{confirmed} of {len(figures)} figures were read again by the program and match"
            + (f"; {differs} differ" if differs else "") + ".")
    return Answer(answer=round(shown, 2), unit=shown_unit, state=CHECK, confidence=confidence, evidence=evidence,
                  explanation=f"{working}. Computed from {len(figures)} figure{'' if len(figures) == 1 else 's'} "
                              f"printed on the sheet. {note}",
                  **common)


def _measured(q: Question, path, index: dict, quantity: str, rooms: list[dict], width: float, height: float,
              common: dict) -> Answer:
    """Rooms measured on the sheet itself: wall pixels against the scale bar (measure.py)."""
    source_index = index
    path, zoom, index = as_pixels(path, source_index)
    bar = measure.scale_bar(path, index)
    if bar is None:
        return Answer.decline("no graphic scale bar was found on the sheet to measure against", **common)
    evidence, parts, checks = [], [], {}
    checks["scale bar"] = f"{bar['label']} = {bar['length_px']:,} px ({bar['px_per_m']:.1f} px per metre)"
    checks["scale bar labels standing at their marks"] = f"{bar['labels_in_place']} of {bar['labels']}"
    total = 0.0
    left_out: list[dict] = []
    for room in rooms:
        name = str(room.get("name") or "room").strip()[:60]
        if room.get("rectangular") is False:
            return Answer.decline(f"{name} is not a plain rectangle, and only rectangular rooms are measured so far", **common)
        rough = _box(room, width, height)
        if rough is None:
            return Answer.decline(f"no usable box for {name}", **common)
        found = measure.measure_room(path, [v * zoom for v in rough["bbox"]], bar["px_per_m"])
        if not found["ok"]:
            return Answer.decline(f"{name} could not be measured: {found['why']}", **common)
        w, h = found["width_m"], found["height_m"]
        checks[f"wall along each side of {name} (share of the side that is wall)"] = ", ".join(
            f"{side} {share:.0%}" for side, share in found["sides"].items())
        if quantity == "area":
            total += found["area_m2"]
            parts.append(f"{w:.2f} m × {h:.2f} m")
            label = f"{name}: {w:.2f} m × {h:.2f} m = {found['area_m2']:.2f} m²"
        else:
            openings = found["openings"] if quantity == "baseboard" else []
            gone = sum(o["width_m"] for o in openings)
            total += found["perimeter_m"] - gone
            parts.append(f"2 × ({w:.2f} m + {h:.2f} m)" + (f" − {gone:.2f} m of door openings" if openings else ""))
            label = f"{name}: {w:.2f} m × {h:.2f} m, perimeter {found['perimeter_m']:.2f} m"
            left_out.extend({**o, "room": name} for o in openings)
        evidence.append(_to_source({"kind": "box", "space": "image_px", "bbox": found["bbox"], "label": label}, zoom))
    for opening in left_out:
        evidence.append(_to_source({"kind": "box", "space": "image_px", "bbox": opening["bbox"],
                                    "role": "door opening, left out", "label": f"{opening['width_m']:.2f} m"}, zoom))
    evidence.append(_to_source({"kind": "box", "space": "image_px", "bbox": [float(v) for v in bar["bbox"]],
                                "role": "scale bar", "label": f"{bar['label']} = {bar['length_px']:,} px"}, zoom))
    unit = "m2" if quantity == "area" else "m"
    shown, shown_unit = convert(total, unit, asked_unit(q.question))
    working = " + ".join(parts) + f" = {_fmt(total)} {pretty(unit)}"
    if shown_unit != unit:
        working += f" = {_fmt(shown)} {pretty(shown_unit)}"
    evidence.append({"kind": "formula", "text": working})
    what = {"area": "floor area", "perimeter": "perimeter", "baseboard": "baseboard length: the perimeter less the door openings"}[quantity]
    common["meta"] = {**common["meta"], "checks": checks, "working": working,
                      "interpreted": f"{what} of {len(rooms)} room{'' if len(rooms) == 1 else 's'}, measured on the sheet"}
    doors = f" {len(left_out)} door opening{'' if len(left_out) == 1 else 's'} left out." if quantity == "baseboard" else ""
    return Answer(answer=round(shown, 2), unit=shown_unit, state=CHECK, confidence=0.6, evidence=evidence,
                  explanation=f"{working}. Measured on the sheet: each side of the room was moved onto the inner face "
                              f"of its wall, and the lengths were read against the scale bar ({bar['label']} = "
                              f"{bar['length_px']:,} px).{doors}",
                  **common)
