"""Counting by pointing: the vision model lists every instance, the code counts the list.

For raster plans with no vector data (CubiCasa). The plain image-only baseline lets the
model state a number. Here it must enumerate: one entry per instance with pixel
coordinates. The answer is the length of that list, so the number is computed from
enumerated objects, and every counted item can be drawn on the plan for a human to check.

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
- The count is computed from your list, so the list must be complete and must not contain anything that does not count.
- If the image does not let you tell, set cannot_tell to true and say why in basis.
- confidence is your probability, from 0 to 1, that the list is exactly right (one item missing or extra makes it wrong).
- basis: one or two sentences on how you recognised the items."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "label": {"type": "string"}},
                "required": ["x", "y", "label"],
                "additionalProperties": False,
            },
        },
        "cannot_tell": {"type": "boolean"},
        "confidence": {"type": "number"},
        "basis": {"type": "string"},
    },
    "required": ["items", "cannot_tell", "confidence", "basis"],
    "additionalProperties": False,
}


def method_name(vlm) -> str:
    return f"vlm-pointing:{vlm.label}"


def make_engine(vlm) -> Callable[[Question, Dataset], Answer]:
    method = method_name(vlm)
    plain = baseline.make_engine(vlm)  # areas and free text cannot be pointed at

    def answer(q: Question, dataset: Dataset) -> Answer:
        if measure_of(q.question) != "count" or not can_render(q.path):
            return plain(q, dataset)
        images = render(dataset.abspath(q.path), max_edge=vlm.max_edge,
                        max_tokens=vlm.max_visual_tokens, max_pages=1)
        image = images[0]
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
        return Answer(answer=count, unit="count", state=CHECK, confidence=confidence, evidence=evidence,
                      explanation=f"{count} item(s) pointed at by the model, counted by the code. {basis}",
                      **common)

    return answer
