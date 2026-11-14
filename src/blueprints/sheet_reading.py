"""Working shown: the model says which printed figures matter and how they combine, the code computes.

For sheets no exact engine covers: a structural plan with dimension strings, a schedule,
an area table, a piping plan with tagged equipment. A vision model asked for the number
states one, and nothing shows where it came from. Here it may not state the number: its
reply has no field for one.

What the model is given, and what it can ask the program to do:

  the sheet's texts   every text on the sheet, read at full size by a program
                      (sheet_text.py): the PDF's own text layer, or the local OCR
  a calculator        the model writes an expression over the figures it names; the code
                      evaluates it (+ - * / and brackets, nothing else) and converts units
  a counter           the model lists the rows or tags to count; the code counts the list
  a closer look       many small symbols: the code cuts the sheet into tiles, has each
                      symbol boxed at full size, merges and counts the boxes (tiles.py)
  a ruler             a room with no printed size: the code finds its walls in the pixels
                      and measures it against the scale bar (measure.py)

What the program checks before it gives a number:

  * every figure the model names is read again by the program at the place marked; a
    figure the program reads differently means no number;
  * where there is something to work out (arithmetic over several figures, a long list
    to count) the question is worked out twice, independently; two workings that disagree
    mean no number, and both are shown;
  * what cannot be done is said: nothing printed for it, no scale bar, a room that is
    not a rectangle.

So the number is computed, every figure in it is a box on the sheet, and when the
working is not solid the answer is "Can't answer reliably" with the reason. What is
left to the model is judgement (which figures matter), which is why these answers are
"Check this" and never Verified.
"""

from __future__ import annotations

import ast
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import measure, progress, scale, sheet_text, tiles
from .answer import Answer
from .data import Dataset, Question
from .pointing import _box
from .render import can_render, render
from .scoring import CHECK

PROMPT = 6
MAX_TEXTS = 1100
# How many times a question is worked out, independently. Two workings that disagree give no number.
WORKINGS = max(1, int(os.environ.get("BLUEPRINTS_WORKINGS", "2")))
AGREE_WITHIN = 0.005  # two quantities this close (relative) are the same result

SYSTEM = """You answer a question about one sheet of a construction drawing set. You get the sheet as an image and a list of the texts printed on it. The list was read by a program at full resolution; each line is `id | text | y x`, where y and x give the centre of the text on a 0-1000 grid (y from the top edge, x from the left edge). Small text may be unreadable in the image: trust the list for what a text says, and the image for where things are and how they belong together.

You do not state the final number. You give what you read and how it combines, and a program does the counting and the arithmetic. Reply in exactly one of five forms and leave the others empty.

1. items: for a question asking how many of something there are (rows of a schedule that match a condition, tagged pieces of equipment, symbols on a plan). List every instance once. text_id is the id of the printed text that identifies the instance (the door number of a schedule row, an equipment tag) when the list has it, otherwise -1. box_2d is a box around the instance in the image: [ymin, xmin, ymax, xmax] on the 0-1000 grid. label is a few words. The answer is the length of your list, so the list must be complete and hold nothing that does not count.

2. figures and expression: for a quantity (an area, a length, a volume, a total, a size). List every printed figure you use: name (a, b, c, ...), text_id (-1 when the list does not have it), box_2d around the figure, printed (exactly as printed), value (the printed figure as a plain number), unit (the unit the figure is printed in), and what it is in a few words. Do not convert units: a dimension printed as 6200 on a sheet dimensioned in millimetres has value 6200 and unit mm; 52'-8" has value 52.6667 and unit ft; 6,092 SF has value 6092 and unit ft2. The program converts. expression combines the names with + - * / and parentheses; write sum to add all the figures up. result_unit is the unit the expression's result comes out in, given the figures' own units (6200 mm times 9950 mm is in mm2): one of mm, cm, m, in, ft, mm2, cm2, m2, in2, ft2, m3, ft3, or none for a plain number. Use only figures printed on the sheet (dimension strings, schedule entries, tables, notes). Do not estimate a size by eye or from a scale bar: if the figures needed are not printed, say so.

3. text: for a descriptive answer, in the language of the question, short. sources lists the printed texts it was read from (text_id, box_2d).

4. look_closer: for a question asking how many drawn symbols there are on a plan when they are many and small and carry no identifying text (doors, windows, fixtures, columns, outlets). Do not list them. Give `what`: one sentence saying exactly what counts as one item and how it is drawn, and box_2d: the part of the sheet that holds the plan to search (leave out the title block, legends and notes). The program then looks at that part piece by piece at full size. In the other three forms, leave `what` empty.

5. measure: for the size of a room or of several rooms (floor area, perimeter, length of baseboard or skirting) when the sheet prints no figure for it but the plan is drawn to scale. Do not estimate the size. Set quantity to area, perimeter or baseboard, and list the rooms: for each, text_id of the room's name on the plan (-1 when the list does not have it), name, box_2d: a box whose four sides lie on the inner faces of the room's walls (for a room that is not a rectangle: the box around the whole room), and rectangular: false when the room is not a plain rectangle. The program finds the room inside its walls whatever its shape, takes out what stands in it (columns, shafts, cores), and for baseboard leaves out the door openings. It needs the sheet's scale and takes it itself from the first of these that the sheet has: (a) a graphic scale bar: then leave reference and doors empty; (b) reference: one dimension printed on the plan, given as text_id of its figure, unit (mm, cm, m, in or ft), and from and to, the two ends of its dimension line, each as [y, x] on the grid; choose a long one whose line runs straight across or straight down the sheet; (c) only when the sheet has neither: doors, boxes around up to eight ordinary single doors on the plan, each box tight around the door leaf and its swing; the program measures them against the standard width of a door and gives an estimate. The rooms are added up unless you say otherwise: to combine them another way (a hall without the stage that stands in it, one wing less another), the rooms are a, b, c, ... in the order you list them, and combine is an expression over those letters with + and - only, such as a - b; leave combine empty to add them all up. In the other forms, leave quantity empty.

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
                "required": ["text_id", "name", "box_2d", "rectangular"], "additionalProperties": False}},
            # Where the scale comes from when the sheet has no scale bar: a printed dimension, else the doors.
            "reference": {"type": "object", "properties": {
                "text_id": {"type": "integer"}, "unit": {"type": "string"}, "from": _BOX, "to": _BOX},
                "required": ["text_id", "unit", "from", "to"], "additionalProperties": False},
            "doors": {"type": "array", "items": _BOX},
            # How the measured rooms combine when it is not a plain sum: "a - b" over the rooms in the order listed.
            "combine": {"type": "string"}},
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
        progress.say(q.qid, "Reading every text on the sheet at full size (once per sheet, about half a minute)")
        index = sheet_text.read(path)
        image = render(path, max_edge=vlm.max_edge, max_tokens=vlm.max_visual_tokens, max_pages=1)[0]
        listing, cut = _listing(index)
        how_read = "the PDF's own text layer" if index["source"] == "pdf" else "the local OCR at full size"
        prompt = f"Texts printed on the sheet ({how_read}):\n{listing}\n\nQuestion: {q.question}"
        progress.say(q.qid, f"{len(index['items'])} texts read. The model is choosing what to read, count or measure")
        replies = [vlm.ask(SYSTEM, [image], prompt, SCHEMA)]
        # Where there is something to work out (arithmetic over several figures, a long list to count),
        # the question is worked out a second time, independently. One printed figure, which the
        # program reads again itself, is looked up once.
        if WORKINGS > 1 and replies[0].data and worth_working_twice(replies[0].data):
            progress.say(q.qid, "Working it out a second time, independently, to see that both come to the same result")
            with ThreadPoolExecutor(max_workers=WORKINGS - 1) as pool:
                replies += list(pool.map(lambda _: vlm.ask(SYSTEM, [image], prompt, SCHEMA), range(WORKINGS - 1)))
        progress.say(q.qid, "Counting, computing and checking each figure against the sheet")
        read = (f"{len(index['items'])} texts, from {how_read}" + (" (the list was cut short)" if cut else ""))
        # Many small symbols are boxed tile by tile: that is done once, whichever working asks for it.
        closer = next((r for r in replies if r.data and _form(r.data) == "tiles"), None)
        if closer is not None:
            done = work(q, path, index, image, cut, closer, read)
            done.cost_usd = (done.cost_usd or 0.0) + sum(r.cost_usd or 0.0 for r in replies if r is not closer)
            return done
        workings = [work(q, path, index, image, cut, reply, read) for reply in replies]
        return settle(workings)

    def work(q: Question, path: Path, index: dict, image, cut: bool, reply, read: str) -> Answer:
        """One working: what one reply of the model comes to, once the code has counted, computed and checked."""
        meta = {**reply.usage(), "images": [image.describe()], "texts": len(index["items"]), "texts_from": index["source"],
                "texts_cut": cut, "trace": [["A program read the sheet", read]]}
        common = dict(method=method, cost_usd=reply.cost_usd, latency_ms=reply.latency_ms, meta=meta)
        if reply.error:
            return Answer.failed(reply.error, **common)
        if reply.data is None:
            return Answer.decline(reply.no_answer or "no usable reply", **common)
        data = reply.data
        basis = str(data.get("basis") or "")
        if data.get("cannot_tell"):
            meta["trace"].append(["The model said it cannot be worked out from this sheet", basis])
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
            meta["trace"].append(["The model pointed at the rooms and asked for the ruler",
                                  f"{wanted['quantity']} of: " + ", ".join(str(r.get("name") or "room") for r in rooms)])
            common["method"] = method + ":measured"
            return _measured(q, path, index, wanted["quantity"], rooms, width, height, common,
                             reference=wanted.get("reference") if isinstance(wanted.get("reference"), dict) else None,
                             doors=[d for d in wanted.get("doors") or [] if isinstance(d, list)],
                             combine=str(wanted.get("combine") or "").strip())

        if what and not figures and not items:
            # Many small symbols: the part of the sheet that holds the plan is searched tile by tile.
            region = _box(closer, width, height)
            picture, zoom, _ = as_pixels(path, index)
            asked = f"{q.question}\nWhat counts as one item: {what}"
            where = [v * zoom for v in region["bbox"]] if region else None
            # The tiles are looked at twice, independently. A symbol both passes boxed is counted; one
            # that only one pass boxed is drawn as doubtful and left out of the count.
            passes = max(1, min(WORKINGS, 2))
            with ThreadPoolExecutor(max_workers=passes) as pool:
                runs = list(pool.map(lambda n: tiles.count(vlm, picture, asked, where,
                                                           note=(lambda text: progress.say(q.qid, text)) if n == 0 else None),
                                     range(passes)))
            result = runs[0]
            once: list[dict] = []
            if passes == 2:
                result["boxes"], once = tiles.agree(runs[0]["boxes"], runs[1]["boxes"])
                result["cost_usd"] += runs[1]["cost_usd"]
                result["errors"] = runs[0]["errors"] + runs[1]["errors"]
                result["sent"] = runs[0]["sent"]
            result["boxes"] = [_to_source(box, zoom) for box in result["boxes"]]
            once = [{**_to_source(box, zoom), "role": "boxed in one pass only"} for box in once]
            common["cost_usd"] = (reply.cost_usd or 0.0) + result["cost_usd"]
            common["latency_ms"] = (reply.latency_ms or 0.0) + result["latency_ms"]
            meta["interpreted"] = f"count of symbols boxed tile by tile. Counted as: {what}"
            meta["basis"] = basis
            meta["trace"] += [["The model asked the program to look closer", what],
                              ["The program had the symbols boxed tile by tile, twice, and counted the boxes both passes drew",
                               f"{result['sent']} tiles at full size; {len(result['boxes'])} boxed in both passes, "
                               f"{len(once)} in one pass only"]]
            common["method"] = method + ":tiles"
            if passes == 2:
                meta["checks"] = {"tiles looked at, twice": result["sent"], "boxed in both passes (counted)": len(result["boxes"]),
                                  "boxed in one pass only (not counted)": len(once)}
            else:
                meta["checks"] = {"tiles looked at": result["sent"], "boxes after merging tiles": len(result["boxes"])}
            if result["errors"] and len(result["errors"]) >= max(1, passes * result["sent"] // 2):
                return Answer.decline(f"the model failed on {len(result['errors'])} of {result['sent']} tiles: {result['errors'][0]}"[:300], **common)
            if result["errors"]:
                meta["checks"]["tiles that failed"] = len(result["errors"])
            found = result["boxes"]
            evidence = (found or [{"kind": "formula", "text": "the model marked nothing of this kind"}]) + once
            doubt = (f" {len(once)} more {'was' if len(once) == 1 else 'were'} boxed in only one of the two passes: "
                     f"{'it is' if len(once) == 1 else 'they are'} drawn as doubtful and not counted." if once else
                     " Both passes boxed the same items." if passes == 2 else "")
            return Answer(answer=len(found), unit="count", state=CHECK,
                          confidence=0.4 if result["errors"] or len(once) > 0.08 * max(1, len(found)) else 0.6,
                          evidence=evidence,
                          explanation=f"{len(found)} boxed by the model on {result['sent']} tiles of the sheet at full size"
                                      f"{', in both of two independent passes' if passes == 2 else ''}; merged and counted "
                                      f"by the code.{doubt}",
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
            meta["trace"] += [["The model listed what to count, not the count",
                               ", ".join(str(e.get("read") or e.get("label") or "item") for e in evidence[:14])
                               + (" …" if len(evidence) > 14 else "")],
                              ["The program counted the list", f"{len(evidence)} items, {named} named by a text printed on the sheet"]]
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


def _form(data: dict) -> str:
    """Which of the five forms a reply is in."""
    if data.get("cannot_tell"):
        return "cannot"
    if data.get("figures"):
        return "figures"
    if data.get("items"):
        return "items"
    if isinstance(data.get("measure"), dict) and data["measure"].get("quantity") and data["measure"].get("rooms"):
        return "measure"
    if isinstance(data.get("look_closer"), dict) and str(data["look_closer"].get("what") or "").strip():
        return "tiles"
    return "text" if str(data.get("text") or "").strip() else "none"


LONG_LIST = 12  # a list longer than this is counted twice: a row is easily left out


def worth_working_twice(data: dict) -> bool:
    """Whether a reply holds something that can go wrong in the working: arithmetic, or a long list."""
    form = _form(data)
    if form == "figures":
        figures = data.get("figures") or []
        return len(figures) > 1 or any(op in str(data.get("expression") or "") for op in "+-*/×÷")
    if form == "items":
        return len(data.get("items") or []) > LONG_LIST
    return False


def _said(ans: Answer) -> str:
    working = ans.meta.get("working")
    value = f"{_fmt(ans.answer)} {pretty(ans.unit)}".strip() if isinstance(ans.answer, (int, float)) else str(ans.answer)
    return f"{value} ({working})" if working else value


def _same_result(a: Answer, b: Answer) -> bool:
    if isinstance(a.answer, (int, float)) and isinstance(b.answer, (int, float)):
        if (a.unit or "") != (b.unit or ""):
            return False
        if a.unit == "count":
            return a.answer == b.answer
        return math.isclose(a.answer, b.answer, rel_tol=AGREE_WITHIN, abs_tol=1e-9)
    left, right = " ".join(str(a.answer).casefold().split()), " ".join(str(b.answer).casefold().split())
    return left == right or left in right or right in left


def settle(workings: list[Answer]) -> Answer:
    """One answer from the independent workings of a question: the same result, or no number."""
    first = workings[0]
    if len(workings) == 1:
        return first
    cost = sum(w.cost_usd or 0.0 for w in workings)
    latency = max(w.latency_ms or 0.0 for w in workings)  # they ran side by side
    failed = [w for w in workings if w.error]
    if failed:
        workings = [w for w in workings if not w.error]
        if not workings:
            failed[0].cost_usd, failed[0].latency_ms = cost, latency
            return failed[0]
        only = workings[0]  # one call failed: one working is all there is, and the answer says so
        only.cost_usd, only.latency_ms = cost, latency
        only.meta["trace"] = [*only.meta.get("trace", []), ["A second working", "the second call failed, so there is only one"]]
        return only
    first, second = workings[0], workings[1]
    trace = list(first.meta.get("trace", []))
    common = dict(method=first.method, cost_usd=cost, latency_ms=latency)
    if first.answer is None and second.answer is None:
        first.cost_usd, first.latency_ms = cost, latency
        first.meta["trace"] = [*trace, ["A second working", "also found no way to answer"]]
        return first
    if first.answer is None or second.answer is None:
        gave, none = (second, first) if first.answer is None else (first, second)
        reason = (f"Two workings of this question did not agree: one came to {_said(gave)}, the other found no way "
                  f"to answer ({none.reason}). No number is given.")
        return Answer.decline(reason[:600], meta={**gave.meta, "trace": [*gave.meta.get("trace", []), ["A second working", f"found no way to answer: {none.reason}"]]}, **common)
    if _same_result(first, second):
        first.cost_usd, first.latency_ms = cost, latency
        if isinstance(first.answer, (int, float)):
            first.confidence = min(0.95, (first.confidence or 0.6) + 0.05)
            first.explanation += " A second, independent working came to the same result."
        first.meta["checks"] = {**(first.meta.get("checks") or {}), "a second, independent working": "the same result"}
        first.meta["trace"] = [*trace, ["The question was worked out a second time, independently", f"the same result: {_said(second)}"]]
        return first
    if not isinstance(first.answer, (int, float)):  # two descriptions in different words: shown, and flagged
        first.cost_usd, first.latency_ms = cost, latency
        first.confidence = 0.4
        first.explanation = f"{first.explanation} A second reading said: {second.answer}.".strip()
        first.meta["trace"] = [*trace, ["The question was worked out a second time, independently", f"in other words: {second.answer}"]]
        return first
    reason = (f"Two independent workings gave different results: {_said(first)} and {_said(second)}. "
              f"No number is given.")
    return Answer.decline(reason[:700], meta={**first.meta, "trace": [*trace, ["The question was worked out a second time, independently", f"a different result: {_said(second)}"]]}, **common)


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
    named = "; ".join(f"{f['name']} = {f['printed']}" + (f" {f.get('unit')}" if f.get("unit") and str(f.get("unit")) not in f["printed"] else "")
                      + (f" ({f.get('what')})" if f.get("what") else "") for f in figures)
    trace = common["meta"].setdefault("trace", [])
    trace.append(["The model named the figures and wrote the expression, not the result", f"{named}. Expression: {expression}"])
    reread = (f"{confirmed} of {len(figures)} match" + (f", {differs} read differently" if differs else "")
              + (f", {len(figures) - confirmed - differs} could not be read" if len(figures) - confirmed - differs else ""))
    trace.append(["The program read each figure again at the place marked", reread])
    if differs:
        wrong = next(f"{label}: {note}" for label, note in checks.items() if note.startswith("the program reads ") and "same" not in note)
        common["meta"]["checks"] = checks
        return Answer.decline(f"A figure could not be confirmed: {wrong}. A figure the program reads differently is "
                              f"not computed with, so no number is given."[:600], **common)
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
    trace.append(["The calculator computed", working])
    confidence = 0.85 if confirmed == len(figures) else 0.6
    common["meta"] = {**common["meta"], "checks": checks, "working": working, "basis": basis,
                      "interpreted": "computed from figures printed on the sheet"}
    note = ("The figure was read again by the program and matches." if confirmed == len(figures) == 1 else
            f"All {len(figures)} figures were read again by the program and match."
            if confirmed == len(figures) else
            f"{confirmed} of {len(figures)} figures were read again by the program and match; "
            f"the program could not read the other{'s' if len(figures) - confirmed > 1 else ''} at the place marked.")
    return Answer(answer=round(shown, 2), unit=shown_unit, state=CHECK, confidence=confidence, evidence=evidence,
                  explanation=f"{working}. Computed from {len(figures)} figure{'' if len(figures) == 1 else 's'} "
                              f"printed on the sheet. {note}",
                  **common)


def _poly_to_source(points: list[list[float]], zoom: float) -> list[list[float]]:
    return [[x / zoom, y / zoom] for x, y in points]


def _scale_of(path, index: dict, texts: dict, reference: dict | None, doors: list, width: float, height: float,
              zoom: float) -> dict | None:
    """The sheet's scale from the best thing on it that has a known size (scale.py): bar, printed dimension, doors."""
    found = scale.from_bar(path, index)
    if found is not None:
        return found
    grid = max(width, height) * zoom / 1000.0  # one step of the model's grid, in pixels

    def point(yx) -> tuple[float, float] | None:
        if not isinstance(yx, list) or len(yx) != 2:
            return None
        return float(yx[1]) / 1000.0 * width * zoom, float(yx[0]) / 1000.0 * height * zoom

    if reference:
        item = texts.get(reference.get("text_id", -1))
        unit = unit_of(str(reference.get("unit") or "")) or "mm"
        start, end = point(reference.get("from")), point(reference.get("to"))
        if item is not None and start and end and unit in _LINEAR:
            value = number_in(item["text"], unit)  # the figure as the program reads it, not as the model does
            if value:
                found = scale.from_dimension(path, start, end, value * _LINEAR[unit], f"{item['text'].strip()} {unit}", grid)
                if found is not None:
                    found["evidence"].append({"kind": "box", "space": "image_px", "bbox": [v * zoom for v in item["box"]],
                                              "role": "read here", "label": "the dimension used as the scale"})
                    return found
    boxes = []
    for door in doors:
        box = _box({"box_2d": door}, width, height)
        if box:
            boxes.append([v * zoom for v in box["bbox"]])
    return scale.from_doors(path, boxes)


def _measured(q: Question, path, index: dict, quantity: str, rooms: list[dict], width: float, height: float,
              common: dict, reference: dict | None = None, doors: list | None = None, combine: str = "") -> Answer:
    """Rooms measured on the sheet itself: the free space inside their walls, against the sheet's scale.

    A plain rectangular room is measured as its rectangle (measure.measure_room) and checked against
    its own outline (measure.room_shape); a room of another shape is measured by its outline. What
    stands inside a room (a column, a shaft, a core) is taken out of its area and drawn.
    """
    source_index = index
    texts = {item["id"]: item for item in source_index["items"]}
    path, zoom, index = as_pixels(path, source_index)
    ruler = _scale_of(path, index, texts, reference, doors or [], width, height, zoom)
    if ruler is None:
        return Answer.decline("the sheet has nothing to take its scale from: no graphic scale bar was found, and no printed "
                              "dimension or doors were pointed at", **common)
    ppm = ruler["px_per_m"]
    evidence, parts, checks = [], [], dict(ruler["checks"])
    total = 0.0
    left_out: list[dict] = []
    openings_seen: list[dict] = []
    holes_taken = 0
    by_outline = 0
    unconfirmed = 0
    each: list[float] = []  # every room's own value, in the order listed: a, b, c, ...
    for room in rooms:
        name = str(room.get("name") or "room").strip()[:60]
        rough = _box(room, width, height)
        if rough is None:
            return Answer.decline(f"no usable box for {name}", **common)
        box = [v * zoom for v in rough["bbox"]]
        def both(how: str) -> tuple[dict, dict]:
            by_rectangle = (measure.measure_room(path, box, ppm, how) if room.get("rectangular") is not False
                            else {"ok": False, "why": "it is not a plain rectangle"})
            return by_rectangle, measure.room_shape(path, by_rectangle["bbox"] if by_rectangle["ok"] else box, ppm,
                                                    texts=index["items"], how=how)

        rect, shape = both("ink")
        if not rect["ok"] and not shape["ok"] and measure.is_scan(path):
            # A scanned plan whose walls are not plain black strokes: found the way its 3D view finds them.
            rect, shape = both("scan")
            if rect["ok"] or shape["ok"]:
                checks["walls"] = "found in the scan's pixels as its 3D view finds them (coloured, grey or double-line walls)"
        if not rect["ok"] and not shape["ok"]:
            why = shape["why"] if room.get("rectangular") is False else rect["why"]
            return Answer.decline(f"{name} could not be measured: {why}", **common)
        confirmed = True
        if rect["ok"] and shape["ok"] and abs(shape["gross_m2"] - rect["area_m2"]) > 0.03 * rect["area_m2"]:
            # The outline does not come to the rectangle's size: it is not used, and that is said.
            confirmed = False
            unconfirmed += 1
            checks[f"{name} by its own outline"] = (f"{shape['gross_m2']:.2f} m², against {rect['area_m2']:.2f} m² for the rectangle: "
                                                    "the outline is not used, and holes in the room were not looked for")
        holes = shape["holes"] if shape["ok"] and confirmed else []
        hole_m2 = sum(h["area_m2"] for h in holes)
        holes_taken += len(holes)
        cut = f" − {hole_m2:.2f} m² standing in it ({len(holes)} hole{'' if len(holes) == 1 else 's'})" if holes else ""
        if rect["ok"]:
            w, h = rect["width_m"], rect["height_m"]
            checks[f"wall along each side of {name} (share of the side that is wall)"] = ", ".join(
                f"{side} {share:.0%}" for side, share in rect["sides"].items())
            if shape["ok"] and confirmed:
                checks[f"{name} by its own outline, holes left in"] = f"{shape['gross_m2']:.2f} m² (the rectangle: {rect['area_m2']:.2f} m²)"
            area = rect["area_m2"] - hole_m2
            perimeter = rect["perimeter_m"] + sum(hole["perimeter_m"] for hole in holes)
            openings = rect["openings"]
            size = f"{w:.2f} m × {h:.2f} m"
            outline = [[rect["bbox"][0], rect["bbox"][1]], [rect["bbox"][2], rect["bbox"][1]],
                       [rect["bbox"][2], rect["bbox"][3]], [rect["bbox"][0], rect["bbox"][3]]]
        else:
            by_outline += 1
            area, perimeter, openings = shape["area_m2"], shape["perimeter_m"], shape["openings"]
            size = f"{shape['gross_m2']:.2f} m² inside its walls"
            outline = shape["polygon"]
            checks[f"{name}: measured by its outline (not a rectangle)"] = (
                f"{shape['gross_m2']:.2f} m² enclosed, openings narrower than {shape['seal_m']:.1f} m closed off")
        openings_seen += openings
        each.append(area if quantity == "area" else perimeter - (sum(o["width_m"] for o in openings) if quantity == "baseboard" else 0.0))
        if quantity == "area":
            total += area
            parts.append(f"{size}{cut}" if rect["ok"] or holes else f"{area:.2f} m²")
            label = f"{name}: {size}{cut} = {area:.2f} m²"
        else:
            taken = openings if quantity == "baseboard" else []
            gone = sum(o["width_m"] for o in taken)
            total += perimeter - gone
            around = (f"2 × ({w:.2f} m + {h:.2f} m)" if rect["ok"] else f"{shape['perimeter_m']:.2f} m around")
            if rect["ok"] and holes:
                around += f" + {sum(hole['perimeter_m'] for hole in holes):.2f} m around {len(holes)} hole{'' if len(holes) == 1 else 's'}"
            parts.append(around + (f" − {gone:.2f} m of door openings" if taken else ""))
            label = f"{name}: {size}, {perimeter:.2f} m around"
            left_out.extend({**o, "room": name} for o in taken)
        if holes or not rect["ok"]:
            # Drawn as the room's own outline with its holes cut out of it, so what was taken out can be seen.
            evidence.append({"kind": "polygon", "space": "image_px", "points": _poly_to_source(outline, zoom),
                             "holes": [_poly_to_source(hole["ring"], zoom) for hole in holes], "label": label,
                             "area_m2": round(area, 2)})
        else:
            evidence.append(_to_source({"kind": "box", "space": "image_px", "bbox": rect["bbox"], "label": label}, zoom))
    for opening in left_out:
        evidence.append(_to_source({"kind": "box", "space": "image_px", "bbox": opening["bbox"],
                                    "role": "door opening, left out", "label": f"{opening['width_m']:.2f} m"}, zoom))
    evidence += [_to_source(dict(item), zoom) for item in ruler["evidence"]]

    # A standard as a check: door openings measured at this scale must come out as wide as doors are.
    doorways = scale.door_check(openings_seen) if ruler["source"] != "doors" else None
    if doorways is not None:
        checks["door leaves at this scale (a door is 0.8 to 1.0 m)"] = f"{doorways['leaf_m']:.2f} m ({doorways['leaves']} leaves)"
        if not doorways["ok"] and doorways["leaves"] >= 2:
            common["meta"] = {**common["meta"], "checks": checks}
            return Answer.decline(f"at the scale read from {ruler['said']} the doors of the room come out "
                                  f"{doorways['leaf_m']:.2f} m wide, which no door is: the scale is in doubt, so nothing is measured",
                                  **common)

    unit = "m2" if quantity == "area" else "m"
    combined = ""
    if combine and len(rooms) > 1:
        # The model said how the rooms combine; the calculator does it, over the values the program measured.
        if not re.fullmatch(r"[a-z+\-() ]+", combine):
            return Answer.decline(f"the rooms can only be added and subtracted, and the model asked for '{combine[:40]}'", **common)
        letters = "abcdefghijklmnopqrstuvwxyz"
        values = {letters[i]: value for i, value in enumerate(each[:26])}
        try:
            total = evaluate(combine, values)
        except Exception as exc:  # a letter with no room, a malformed expression
            return Answer.decline(f"the rooms could not be combined as '{combine[:40]}': {exc}", **common)
        if total <= 0:
            return Answer.decline(f"combining the rooms as '{combine[:40]}' gives nothing to measure ({total:.2f})", **common)
        combined = "; ".join(f"{letters[i]} = {part}" for i, part in enumerate(parts)) + f"; {combine}"
    shown, shown_unit = convert(total, unit, asked_unit(q.question))
    working = (combined or " + ".join(parts)) + f" = {_fmt(total)} {pretty(unit)}"
    if shown_unit != unit:
        working += f" = {_fmt(shown)} {pretty(shown_unit)}"
    evidence.append({"kind": "formula", "text": working})
    how = ("The program measured the room by its own outline" if by_outline == len(rooms) else
           "The program moved each side of the box onto its wall and measured")
    steps = [ruler["trace"], [how, working]]
    if holes_taken:
        steps.append(["Took out what stands inside the room", f"{holes_taken} hole{'' if holes_taken == 1 else 's'} "
                      "(columns, shafts or cores enclosed by the room): drawn on the sheet, their area is not floor"])
    if combined:
        steps.append(["The calculator combined the rooms as the model said", f"{combine} = {_fmt(total)} {pretty(unit)}"])
    if doorways is not None:
        steps.append(["Checked the scale against a standard: the width of a door",
                      f"the room's door leaves come out {doorways['leaf_m']:.2f} m wide at this scale; a door is 0.8 to 1.0 m"])
    common["meta"].setdefault("trace", []).extend(steps)
    what = {"area": "floor area", "perimeter": "perimeter", "baseboard": "baseboard length: the perimeter less the door openings"}[quantity]
    common["meta"] = {**common["meta"], "checks": checks, "working": working,
                      "interpreted": f"{what} of {len(rooms)} room{'' if len(rooms) == 1 else 's'}, measured on the sheet"}
    said_doors = f" {len(left_out)} door opening{'' if len(left_out) == 1 else 's'} left out." if quantity == "baseboard" else ""
    said_holes = (f" {holes_taken} hole{'' if holes_taken == 1 else 's'} standing in the room "
                  f"{'was' if holes_taken == 1 else 'were'} taken out." if holes_taken else "")
    measured_how = ("the room was measured by its own outline, the free space inside its walls" if by_outline else
                    "each side of the room was moved onto the inner face of its wall")
    confidence = 0.45 if unconfirmed else 0.6
    estimate = ""
    if ruler["estimate"]:
        # The scale is not printed: it rests on a standard or on where the model pointed. Said, with the range it leaves.
        power = 2 if quantity == "area" else 1
        low, high = sorted((shown * (ppm / ruler["high"]) ** power, shown * (ppm / ruler["low"]) ** power))
        confidence = 0.3
        common["meta"]["estimate"] = {"low": round(low, 2), "high": round(high, 2), "from": ruler["said"],
                                      "unit": pretty(shown_unit)}
        common["meta"]["not_verifiable"] = True
        estimate = (f" This is an estimate: no scale is printed on the sheet. Between {_fmt(low)} and {_fmt(high)} "
                    f"{pretty(shown_unit)} over the range that leaves open.")
    return Answer(answer=round(shown, 2), unit=shown_unit, state=CHECK, confidence=confidence, evidence=evidence,
                  explanation=f"{working}. Measured on the sheet: {measured_how}, and the lengths were read against "
                              f"{ruler['said']}.{said_doors}{said_holes}{estimate}",
                  **common)
