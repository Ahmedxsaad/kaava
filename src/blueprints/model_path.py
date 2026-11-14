"""What answers when no exact engine covers a drawing: the vision model, never left to state a number.

    a sheet (a PDF page, or an image as large as one)    ->  working shown (sheet_reading.py):
                                                             printed figures and rows, computed by the code
    a small scan, asked to count things                  ->  each item boxed, the boxes counted (pointing.py)
    a small scan, asked anything else                    ->  working shown

A sheet carries its quantities as text (dimension strings, schedules, tables, tags), and
at the model's resolution that text is too small to trust: so the texts are read by a
program at full size and the model works from the list. A small scan of one dwelling is
mostly symbols, and those are boxed in the picture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import pointing, sheet_reading
from .answer import Answer
from .data import Dataset, Question
from .questions import measure_of

SHEET_LONG_EDGE = 3500  # an image this large is a sheet of a drawing set, not a scan of one dwelling


def is_sheet(path: Path) -> bool:
    if path.suffix.lower() == ".pdf" or path.with_suffix(".pdf").is_file():
        return True
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return max(im.size) >= SHEET_LONG_EDGE
    except Exception:
        return False


def make(vlm) -> Callable[[Question, Dataset], Answer]:
    working = sheet_reading.make_engine(vlm)
    boxes = pointing.make_engine(vlm)

    def answer(q: Question, dataset: Dataset) -> Answer:
        if is_sheet(dataset.abspath(q.path)) or measure_of(q.question) != "count":
            return working(q, dataset)
        return boxes(q, dataset)

    return answer
