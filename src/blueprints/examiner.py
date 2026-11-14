"""Examiners: second readers that check the working of a quantity for what lies inside the thing measured.

A working that multiplies a slab's printed length by its printed width is right about the
rectangle and can still be wrong about the slab: a trench cut into it, a pit, a shaft, a zone
of another thickness are inside those dimensions and are not slab. Whoever wrote the working
looked for the dimensions; nobody looked inside.

So when a quantity is an area or a volume computed by multiplying, two examiners are asked,
independently, to look inside the thing measured on an enlarged view of that part of the sheet.
They get the working and are asked only for what must be taken out of it or added to it. Like
every reader here they never state a result:

  - a part whose size is printed comes back as figures and a term (length times width); the
    program reads those figures again itself and the calculator recomputes. Only a correction
    both examiners make, to the same result, is applied;
  - a part whose size is not printed is not estimated by anyone's eye. Where it is drawn as a
    rectangle in solid lines, the program measures that rectangle in the pixels, against a
    dimension printed nearby that the examiner points at (scale.from_dimension), and takes it
    out as a measured deduction, said as one, when both examiners' measurements agree;
  - otherwise it is marked on the sheet, and the answer says what was not taken out: the number
    is then an upper bound, and says so.

sheet_reading.py runs them; this module holds what they are told and how their replies are read.
"""

from __future__ import annotations

import io
from pathlib import Path

from .render import Rendered

SIZED = {"mm2", "cm2", "m2", "in2", "ft2", "m3", "ft3"}  # units of a quantity that has an inside

SYSTEM = """You check another reader's working of a quantity on one sheet of a construction drawing set. You get two images: the whole sheet, and an enlarged view of the part of the sheet the working is about. You also get the texts printed on the sheet (each line is `id | text | y x`, the centre of the text on a 0-1000 grid of the WHOLE sheet, y from the top edge and x from the left edge), the question, and the working: the printed figures it used and the expression over them. A program does all arithmetic. You never state a result.

Decide whether the working measures exactly what the question asks. Look inside the thing being measured, on the enlarged view, for:
- parts to deduct: things that lie inside it and are not part of it, such as an opening, a pit, a trench, a shaft, a void, a column, or a zone of another type (another slab thickness, another finish);
- parts to add: things that belong to it and that the working left out (an alcove, a second zone carrying the same label).
Do not list what is already accounted for in the working, nor what merely lies beside the thing measured.

For each part give: what (a few words, with the label printed for it when there is one); box_2d around it as [ymin, xmin, ymax, xmax] on the grid of the WHOLE sheet; effect (deduct or add); and, only when every dimension needed for its size is printed on the sheet, figures (name: d1, d2, ...; text_id: the id of the printed text, or -1; box_2d around the figure; printed: exactly as printed; value: as a plain number; unit: the unit it is printed in; what: a few words) and term: an expression over those names for the part's size in the unit of the working's result, with + - * / and parentheses (for an area: length times width). When a needed dimension is not printed (only a length, say, and no width), leave figures empty and term empty: do not estimate a size from the drawing.

When you list a part whose size is not printed, also give scale_from: one dimension printed on the enlarged view whose dimension line runs straight across or straight down the sheet, as text_id of its figure, unit (mm, cm, m, in or ft), and from and to: the two ends of that dimension line, each as [y, x] on the grid of the WHOLE sheet. Choose a long one. The program uses it as the sheet's scale to measure the part where it is drawn. When every part is sized from printed figures, or there are no parts, give scale_from with text_id -1 and empty from and to.

If nothing is to be deducted or added, return an empty list of parts. basis: one or two sentences on what you looked at."""

_BOX = {"type": "array", "items": {"type": "integer"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "what": {"type": "string"}, "box_2d": _BOX, "effect": {"type": "string", "enum": ["deduct", "add"]},
                "figures": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "text_id": {"type": "integer"}, "box_2d": _BOX,
                                   "printed": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"},
                                   "what": {"type": "string"}},
                    "required": ["name", "text_id", "box_2d", "printed", "value", "unit", "what"],
                    "additionalProperties": False}},
                "term": {"type": "string"}},
            "required": ["what", "box_2d", "effect", "figures", "term"], "additionalProperties": False}},
        "scale_from": {"type": "object", "properties": {
            "text_id": {"type": "integer"}, "unit": {"type": "string"}, "from": _BOX, "to": _BOX},
            "required": ["text_id", "unit", "from", "to"], "additionalProperties": False},
        "basis": {"type": "string"},
    },
    "required": ["parts", "scale_from", "basis"],
    "additionalProperties": False,
}


def worth_examining(data: dict, unit: str | None) -> bool:
    """A quantity with an inside, worked out by multiplying: not a total read off a table."""
    return bool(data.get("figures")) and "*" in str(data.get("expression") or "") and unit in SIZED


def enlarged(picture: Path, boxes: list[list[float]], max_edge: int) -> tuple[Rendered, list[float]] | None:
    """The part of the sheet the working's figures stand in, with room around them, as an image for a reader.

    The figures of an area are its dimensions: they stand along its sides or outside it, so the view
    reaches twice their spread beyond them. Returns the image and the box it shows, in the picture's pixels.
    """
    from PIL import Image

    if not boxes:
        return None
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(picture) as im:
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad = max(2.0 * max(x1 - x0, y1 - y0), 0.08 * max(im.size))
        view = [max(0.0, x0 - pad), max(0.0, y0 - pad), min(float(im.width), x1 + pad), min(float(im.height), y1 + pad)]
        if view[2] - view[0] < 8 or view[3] - view[1] < 8:
            return None
        crop = im.crop(tuple(int(v) for v in view)).convert("RGB")
    scale = min(1.0, max_edge / max(crop.size))
    if scale < 1.0:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    return Rendered(buffer.getvalue(), "image/png", crop.width, crop.height, 0, scale), view


def same_place(a: list[float], b: list[float]) -> bool:
    """Two boxes that mark the same thing: they share a good part of the smaller one."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return False
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return smaller > 0 and w * h >= 0.3 * smaller
