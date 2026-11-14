"""Counting by pointing: the vision model lists every instance, the code counts the list.

For raster plans with no vector data (CubiCasa). The plain image-only baseline lets the
model state a number. Here it must enumerate: one entry per instance with its place on
the image. The answer is the length of that list, so the number is computed from
enumerated objects, and every counted item can be drawn on the plan for a human to check.

A model says where an item is in the form it was trained on. Gemini returns a box around
each item as [ymin, xmin, ymax, xmax] on a 0-1000 grid (its documented detection format);
other models give the pixel coordinates of the item's centre.

Answers are state "check": this is still a model reading pixels.
"""

from __future__ import annotations

from typing import Callable

from . import baseline
from .answer import Answer
from .data import Dataset, Question
from .questions import measure_of
from .render import can_render, render
from .scoring import CHECK

SYSTEM = """You count things on a floor plan by pointing at them. You get the plan as an image and one question, in any language, asking how many of something are on it.

List every instance the question asks about: one entry per instance, with the pixel coordinates of its centre in this image (x from the left edge, y from the top edge) and a label of a few words.

- Apply the counting rules written in the question exactly. For example: a double-bowl sink is one item; a doorway without a door leaf is not a door.
- List each instance once. If there are none, return an empty list.
- Count only what belongs to the dwelling or building the plan is drawn for. When a plan of one dwelling shows cut-off parts of its neighbours at the edges, the neighbours' items do not count.
- The count is computed from your list, so the list must be complete and must not contain anything that does not count.
- If the image does not let you tell, set cannot_tell to true and say why in basis.
- confidence is your probability, from 0 to 1, that the list is exactly right (one item missing or extra makes it wrong).
- basis: one or two sentences on how you recognised the items."""

def _schema(item: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": item},
            "cannot_tell": {"type": "boolean"},
            "confidence": {"type": "number"},
            "basis": {"type": "string"},
        },
        "required": ["items", "cannot_tell", "confidence", "basis"],
        "additionalProperties": False,
    }


SCHEMA = _schema({
    "type": "object",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "label": {"type": "string"}},
    "required": ["x", "y", "label"],
    "additionalProperties": False,
})

# The same task for a model that locates things as boxes on a 0-1000 grid (Gemini).
SYSTEM_BOXES = SYSTEM.replace(
    "with the pixel coordinates of its centre in this image (x from the left edge, y from the top edge) and a label of a few words.",
    "with a box drawn tightly around the item itself (not around the room it is in) and a label of a few words. "
    "box_2d is [ymin, xmin, ymax, xmax], normalized to 0-1000: y from the top edge, x from the left edge.")
assert SYSTEM_BOXES != SYSTEM

SCHEMA_BOXES = _schema({
    "type": "object",
    "properties": {"box_2d": {"type": "array", "items": {"type": "integer"}}, "label": {"type": "string"}},
    "required": ["box_2d", "label"],
    "additionalProperties": False,
})

GRID = 1000.0
MIN_BOX = 8.0  # a box thinner than this on the 0-1000 grid is widened so it can be seen


def uses_boxes(vlm) -> bool:
    return bool(getattr(vlm, "native_boxes", False))


# The prompt's version is part of the method's name, so a run log says which prompt it measured.
# 1 (no suffix): the first prompt. 2: adds the rule that a neighbour's items at the edge do not count
# (three of the eight wrong answers on the development sample's scans were exactly that).
PROMPT = 2


def method_name(vlm) -> str:
    return f"vlm-pointing:{vlm.label}" + (":boxes" if uses_boxes(vlm) else "") + (f":p{PROMPT}" if PROMPT > 1 else "")


def _box(item: dict, width: float, height: float) -> dict | None:
    """One 0-1000 box as evidence in the pixels of the original file; None when it is not a box."""
    try:
        y0, x0, y1, x1 = (float(v) for v in item["box_2d"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(v == v and abs(v) < 1e6 for v in (x0, y0, x1, y1)):  # NaN or nonsense
        return None
    (x0, x1), (y0, y1) = sorted((x0, x1)), sorted((y0, y1))
    inside = -1 <= x0 and x1 <= GRID + 1 and -1 <= y0 and y1 <= GRID + 1
    if x1 - x0 < MIN_BOX:
        x0, x1 = (x0 + x1 - MIN_BOX) / 2, (x0 + x1 + MIN_BOX) / 2
    if y1 - y0 < MIN_BOX:
        y0, y1 = (y0 + y1 - MIN_BOX) / 2, (y0 + y1 + MIN_BOX) / 2
    bbox = [round(x0 / GRID * width, 1), round(y0 / GRID * height, 1),
            round(x1 / GRID * width, 1), round(y1 / GRID * height, 1)]
    return {"kind": "box", "space": "image_px", "bbox": bbox, "label": str(item.get("label", ""))[:60], "inside": inside}


def make_engine(vlm) -> Callable[[Question, Dataset], Answer]:
    method = method_name(vlm)
    plain = baseline.make_engine(vlm)  # areas and free text cannot be pointed at

    def answer(q: Question, dataset: Dataset) -> Answer:
        if measure_of(q.question) != "count" or not can_render(q.path):
            return plain(q, dataset)
        images = render(dataset.abspath(q.path), max_edge=vlm.max_edge,
                        max_tokens=vlm.max_visual_tokens, max_pages=1)
        image = images[0]
        boxes = uses_boxes(vlm)
        if boxes:
            reply = vlm.ask(SYSTEM_BOXES, images, f"Question: {q.question}", SCHEMA_BOXES)
        else:
            text = f"The image is {image.width} x {image.height} pixels.\nQuestion: {q.question}"
            reply = vlm.ask(SYSTEM, images, text, SCHEMA)
        meta = {**reply.usage(), "images": [im.describe() for im in images]}
        common = dict(method=method, cost_usd=reply.cost_usd, latency_ms=reply.latency_ms, meta=meta)
        if reply.error:
            return Answer.failed(reply.error, **common)
        if reply.data is None:
            return Answer.decline(reply.no_answer or "no usable reply", **common)
        data = reply.data
        basis = str(data.get("basis") or "")
        try:
            confidence = min(1.0, max(0.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        if data.get("cannot_tell"):
            return Answer.decline(f"model could not tell from the image: {basis}"[:300],
                                  confidence=confidence, **common)
        points = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            if boxes:  # a 0-1000 grid over the image; the original file is image / scale pixels
                box = _box(item, image.width / image.scale, image.height / image.scale)
                if box:
                    points.append(box)
                continue
            try:
                x, y = float(item["x"]), float(item["y"])
            except (KeyError, TypeError, ValueError):
                continue
            # Pixel coordinates of the image as sent; `scale` maps back to the original file.
            points.append({"kind": "point", "space": "image_px", "x": x / image.scale, "y": y / image.scale,
                           "label": str(item.get("label", ""))[:60],
                           "inside": 0 <= x <= image.width and 0 <= y <= image.height})
        count = len(points)
        evidence = points or [{"kind": "formula", "text": "the model pointed at nothing of this kind"}]
        how = "boxed" if boxes else "pointed at"
        return Answer(answer=count, unit="count", state=CHECK, confidence=confidence, evidence=evidence,
                      explanation=f"{count} item(s) {how} by the model, counted by the code. {basis}",
                      **common)

    return answer
