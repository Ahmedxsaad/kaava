"""Room labels read from the text layer of a plan PDF.

A second, independent reading of the same floor: the PDF is another file, parsed by
another library, and carries each room's name, number and printed area as text. When
it agrees with the DXF geometry, that agreement is the evidence for trusting a number.

Each room is three text lines in order: name, number, area ("Zimmer", "2.01",
"15.61 m²"); small rooms carry only the number; one room per apartment starts with the
apartment tag ("Whg. A"). Legend and title-block words are never followed by a room
number, so they are not mistaken for rooms.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

_NUMBER = re.compile(r"^\d+(\.\d+)+[a-z]?$")
_AREA = re.compile(r"^(\d+(?:[.,]\d+)?)\s*m[²2]$")
_APARTMENT = re.compile(r"^(Whg|Wohnung|App|Appartement|Appartamento|Apt|Apartment|Unit|Log|Logement)\.?\s+(\w{1,3})$",
                        re.IGNORECASE)


@lru_cache(maxsize=64)
def read_labels(pdf_path: str) -> dict:
    """{"names": Counter of room names, "areas": {number: m²}, "tags": [apartment tags]}"""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        lines = [line.strip() for line in doc[0].get_text("text").splitlines() if line.strip()]

    names: Counter = Counter()
    areas: dict[str, float] = {}
    duplicates: set[str] = set()
    tags: list[str] = []
    for i, line in enumerate(lines):
        tag = _APARTMENT.match(line)
        if tag:
            tags.append(tag.group(2))
            continue
        if not _NUMBER.match(line):
            continue
        # The printed area, if any, is the line after the number; the name the line before.
        if i + 1 < len(lines) and (area := _AREA.match(lines[i + 1])):
            if line in areas:
                duplicates.add(line)
            areas[line] = float(area.group(1).replace(",", "."))
        if i > 0:
            before = lines[i - 1]
            if not (_NUMBER.match(before) or _AREA.match(before) or _APARTMENT.match(before)):
                names[before] += 1
    for number in duplicates:  # the same number printed twice: not a usable cross-check
        areas.pop(number, None)
    return {"names": names, "areas": areas, "tags": tags}


def labels_for(pdf_path: Path | None) -> dict | None:
    if pdf_path is None or not pdf_path.is_file():
        return None
    try:
        return read_labels(str(pdf_path))
    except Exception:
        return None  # an unreadable PDF just means no second source
