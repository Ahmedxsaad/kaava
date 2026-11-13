"""Baseline: a frontier vision model answering from the image alone.

This is the number to beat, and the one place where a language model produces the
number itself. It gets the drawing as a single image at the model's full resolution
and the question text, nothing else: no question type, no unit, no tools, no tiling.
Its answers are never Verified (there is nothing to verify them against); its
self-reported confidence is logged so we can show how often it is confidently wrong.
"""

from __future__ import annotations

from typing import Callable

from .answer import Answer
from .data import Dataset, Question
from .render import can_render, planned_tokens, render
from .scoring import CHECK

SYSTEM = """You answer quantity take-off questions about a construction drawing. You get the drawing as an image and one question, which can be in any language. Answer only from what is drawn in the image.

How to fill the reply:
- If the question asks for a quantity (a count, an area, a length or a volume), put the number in answer_number, in the unit the question asks for, and leave answer_text null. A count is a whole number.
- If the question asks for something descriptive, put the answer in answer_text, in the language of the question, and leave answer_number null.
- If the image does not let you determine the answer, set both to null and say why in basis.
- confidence is your probability, from 0 to 1, that the answer is exactly right. A count that is off by one is wrong; an area, length or volume more than 2% off is wrong.
- basis: one or two sentences on what you counted, read or measured, and where on the drawing."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer_number": {"type": ["number", "null"]},
        "answer_text": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "basis": {"type": "string"},
    },
    "required": ["answer_number", "answer_text", "confidence", "basis"],
    "additionalProperties": False,
}

# Tokens the system prompt, the reply schema and the question add around the image.
PROMPT_OVERHEAD_TOKENS = 450


def method_name(vlm) -> str:
    return f"vlm-image-only:{vlm.label}"


def make_engine(vlm, max_pages: int = 4) -> Callable[[Question, Dataset], Answer]:
    method = method_name(vlm)

    def answer(q: Question, dataset: Dataset) -> Answer:
        if not can_render(q.path):
            ext = q.path.rsplit(".", 1)[-1].lower()
            return Answer.decline(
                f"no image: a .{ext} file is not a picture a vision model can read",
                method=method, cost_usd=0.0, latency_ms=0.0,
            )
        images = render(dataset.abspath(q.path), max_edge=vlm.max_edge,
                        max_tokens=vlm.max_visual_tokens, max_pages=max_pages)
        reply = vlm.ask(SYSTEM, images, f"Question: {q.question}", SCHEMA)
        meta = {**reply.usage(), "images": [im.describe() for im in images]}
        common = dict(method=method, cost_usd=reply.cost_usd, latency_ms=reply.latency_ms, meta=meta)

        if reply.error:
            return Answer.failed(reply.error, **common)
        if reply.data is None:
            return Answer.decline(reply.no_answer or "no usable reply", **common)

        data = reply.data
        number, text = data.get("answer_number"), data.get("answer_text")
        value = number if number is not None else (text.strip() if isinstance(text, str) and text.strip() else None)
        basis = str(data.get("basis") or "")
        try:
            confidence = min(1.0, max(0.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        if value is None:
            return Answer.decline(f"model could not tell from the image: {basis}"[:300],
                                  confidence=confidence, **common)
        return Answer(answer=value, state=CHECK, confidence=confidence, explanation=basis, **common)

    return answer


def estimate(questions, dataset: Dataset, vlm, max_pages: int = 4) -> dict:
    """Input tokens the run will send, measured from the files without calling the model."""
    calls = image_tokens = no_image = 0
    per_drawing: dict[str, int] = {}
    for q in questions:
        if not can_render(q.path):
            no_image += 1
            continue
        if q.path not in per_drawing:
            fixed = getattr(vlm, "IMAGE_TOKENS", None)  # a flat budget per image (Gemini)
            per_drawing[q.path] = fixed[vlm.resolution] if fixed else planned_tokens(
                dataset.abspath(q.path), max_edge=vlm.max_edge,
                max_tokens=vlm.max_visual_tokens, max_pages=max_pages)
        calls += 1
        image_tokens += per_drawing[q.path]
    return {
        "calls": calls,
        "no_image": no_image,
        "image_tokens": image_tokens,
        "input_tokens": image_tokens + calls * PROMPT_OVERHEAD_TOKENS,
    }
