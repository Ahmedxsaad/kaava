"""Read a scan once: the vision model marks every countable thing, the code answers from the list.

A scan has no geometry to query, so a vision model has to look at it. Asked one question
at a time, it looks at the same picture again for every question, and the picture is what
costs: about 2,900 tokens of image against some 140 tokens of answer. The dataset asks 24
questions of each scanned plan (12 kinds of thing, each in English and in Finnish).

Here the model looks once per drawing. It marks every window, door, toilet bowl, sink,
shower and bathtub, every room of the kinds people ask about, and each storey's plan,
with a box around each (Gemini's detection format: [ymin, xmin, ymax, xmax] on a 0-1000
grid). That list is the drawing's index, the same kind of thing a DXF gives for free.
Every question about the drawing is then a count over the index: computed by the code
from boxes a person can see, at no further cost and in no time.

The index is kept on disk with what it cost. A run that reuses a stored index charges
that cost to the first question that needs it, so a run's total is what reading its
drawings really cost, whether the model was called in this run or an earlier one.

Answers are state "check": this is still a model reading pixels.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

from . import config
from .answer import Answer
from .data import Dataset, Question
from .engines import raster_labels
from .pointing import _box
from .questions import measure_of, norm, quoted_strings, strip_quoted
from .render import can_render, render
from .scoring import CHECK

# The kinds the index holds, each with the rule by which it is marked. The rules are the
# ones the dataset's questions state (read on development drawings only).
KINDS: dict[str, str] = {
    "window": "a window in a wall",
    "door": "a doorway fitted with a door: a door leaf, usually drawn with its swing arc. "
            "An open doorway without a door leaf is not a door",
    "toilet_bowl": "a toilet bowl (WC seat)",
    "sink": "a sink or wash basin, in kitchens and in bathrooms alike. A double-bowl sink is one item",
    "shower": "a shower",
    "bathtub": "a bathtub",
    "sauna": "a sauna room (S, SAUNA)",
    "kitchen": "a kitchen, including kitchenettes, open kitchens and utility kitchens (K, KK, KT, KEITTIÖ, TUPAK, AVOK)",
    "bedroom": "a bedroom (makuuhuone, usually marked MH)",
    "bathroom": "a bathroom or washroom (KH, KPH, PH, PESUH, SH). A separate WC room is not a bathroom",
    "wc_room": "a separate WC room: a toilet room marked WC",
    "balcony": "a balcony (parveke), including glazed balconies",
    "floor_plan": "the plan of one floor (storey): one box around each separate storey the sheet shows",
}
ROOMS = ("sauna", "kitchen", "bedroom", "bathroom", "wc_room", "balcony")

# How the kinds are shared out over model calls. "one": a single call marks everything.
# "three": openings, fixtures, and rooms with storeys, each in a call of its own.
# "four": as "three", with windows and doors each in a call of their own. Measured on 44
# development drawings, a single call marks rooms and storeys well (92-100% of counts right)
# and openings badly (windows 55%, doors 70%): there are many of them and they are small.
GROUPINGS: dict[str, tuple[tuple[str, ...], ...]] = {
    "one": (tuple(KINDS),),
    "three": (("window", "door"), ("toilet_bowl", "sink", "shower", "bathtub"), ROOMS + ("floor_plan",)),
    "four": (("window",), ("door",), ("toilet_bowl", "sink", "shower", "bathtub"), ROOMS + ("floor_plan",)),
}

# The prompt's version is part of the method's name, so a run log says which prompt it measured.
PROMPT = 1


def system_prompt(kinds: tuple[str, ...]) -> str:
    listed = "\n".join(f"- {kind}: {KINDS[kind]}." for kind in kinds)
    return f"""You index a floor plan. You get the plan as an image. Mark every instance of each kind listed below: one entry per instance, with a box drawn tightly around it and the kind's name as its label. box_2d is [ymin, xmin, ymax, xmax], normalized to 0-1000: y from the top edge, x from the left edge.

Kinds:
{listed}

- Go through the kinds one at a time, in the order given, and finish one before starting the next.
- For a thing (a window, a door, a fixture) the box goes around the thing itself, not around the room it is in. For a room the box goes around the room.
- Mark each instance once. A kind with no instance on the plan gets no entry.
- Mark only what belongs to the dwelling or building the plan is drawn for. When a plan of one dwelling shows cut-off parts of its neighbours at the edges, the neighbours' items are not marked.
- Every count is computed from your list, so for each kind the list must be complete and must not contain anything that does not count.
- unsure: the names of the kinds this image does not let you mark reliably (an empty list when there are none)."""


def schema(kinds: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {"box_2d": {"type": "array", "items": {"type": "integer"}},
                               "label": {"type": "string", "enum": list(kinds)}},
                "required": ["box_2d", "label"],
                "additionalProperties": False,
            }},
            "unsure": {"type": "array", "items": {"type": "string", "enum": list(kinds)}},
        },
        "required": ["items", "unsure"],
        "additionalProperties": False,
    }


# ----------------------------------------------------------------- the question

_HEAD = re.compile(r"^(.*?)(?:\(|,|;|\?|$)")
_ROOM_KINDS = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom", "wc": "wc_room",
               "sauna": "sauna", "balcony": "balcony"}
# The counted thing, named right after the counting phrase. English and Finnish are the
# languages the scans are asked in; a question in other words goes to the model as it is.
_THINGS: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"\bfloors\b|\bstore?ys?\b|\bkerrosta\b|\bkerroksia\b"), ("floor_plan",)),
    (re.compile(r"\btoilet bowls?\b|\bwc seats?\b|\bwc-istuin\w*"), ("toilet_bowl",)),
    (re.compile(r"\bsinks?\b|\bwash ?basins?\b|\btiskiallas\w*|\bpesuallas\w*|\baltaita\b"), ("sink",)),
    (re.compile(r"\bwindows?\b|\bikkuna\w*|\bikkunoita\b"), ("window",)),
    (re.compile(r"\bdoors?\b|\bovea\b|\bovia\b|\bovet\b"), ("door",)),
]
_SHOWER = re.compile(r"\bshowers?\b|\bsuihku\w*")
_BATHTUB = re.compile(r"\bbathtubs?\b|\bkylpyamme\w*")
# A question about part of the plan ("in apartment 3", "in room 2.01") is not a count over the whole index.
_PART = re.compile(r"\d|\b(apartment|unit|flat|dwelling|huoneisto\w*|asunno\w*)\b")


def kinds_asked(question: str) -> tuple[str, ...] | None:
    """The index kinds whose instances the question counts, or None when the index cannot answer it."""
    if measure_of(question) != "count" or quoted_strings(question):
        return None
    text = norm(strip_quoted(question))
    head = _HEAD.match(text).group(1)
    if _PART.search(head):
        return None
    room = raster_labels.room_kind_in(question)
    if room:
        return (_ROOM_KINDS[room],)
    shower, bathtub = bool(_SHOWER.search(head)), bool(_BATHTUB.search(head))
    if shower or bathtub:
        return tuple(kind for kind, asked in (("shower", shower), ("bathtub", bathtub)) if asked)
    found = [kinds for pattern, kinds in _THINGS if pattern.search(head)]
    return found[0] if len(found) == 1 else None


# ----------------------------------------------------------------- reading a drawing

CACHE_DIR = config.REPO_ROOT / "cache" / "scan_index"


def method_name(vlm, grouping: str) -> str:
    return f"scan-index:{vlm.label}:{grouping}" + (f":p{PROMPT}" if PROMPT > 1 else "")


def _file_key(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:20]


class Reader:
    """Reads drawings group by group, once each, and remembers who has already paid for a reading."""

    def __init__(self, vlm, grouping: str = "one", cache_dir: Path | None = None):
        if grouping not in GROUPINGS:
            raise ValueError(f"unknown grouping {grouping!r} (available: {', '.join(GROUPINGS)})")
        self.vlm, self.grouping, self.groups = vlm, grouping, GROUPINGS[grouping]
        self.cache_dir = cache_dir or CACHE_DIR
        self._locks: dict[tuple, threading.Lock] = {}
        self._guard = threading.Lock()
        self._charged: set[tuple] = set()  # readings whose cost this run has already put on a question

    def group_of(self, kind: str) -> int:
        return next(i for i, kinds in enumerate(self.groups) if kind in kinds)

    def _store(self, file_key: str, group: int) -> Path:
        name = re.sub(r"[^\w.=+-]", "_", f"{self.vlm.label}-{self.grouping}{group}-p{PROMPT}")
        return self.cache_dir / file_key[:2] / f"{file_key}-{name}.json"

    def reading(self, path: Path, group: int) -> tuple[dict, bool]:
        """The stored reading of one group of kinds on one drawing, and whether its cost is still to be charged.

        A reading is {"items": {kind: [evidence box, ...]}, "unsure": [kind, ...], "cost_usd", "latency_ms",
        "usage", "image"} or {"error": ...} / {"no_answer": ...} when the call gave nothing usable.
        """
        file_key = _file_key(path)
        key = (file_key, group)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            store = self._store(file_key, group)
            if store.is_file():
                data = json.loads(store.read_text(encoding="utf-8"))
            else:
                data = self._ask(path, self.groups[group])
                if "error" not in data:  # a failed call is tried again next time
                    store.parent.mkdir(parents=True, exist_ok=True)
                    store.write_text(json.dumps(data), encoding="utf-8")
            first = key not in self._charged
            self._charged.add(key)
            return data, first

    def _ask(self, path: Path, kinds: tuple[str, ...]) -> dict:
        image = render(path, max_edge=self.vlm.max_edge, max_tokens=self.vlm.max_visual_tokens, max_pages=1)[0]
        reply = self.vlm.ask(system_prompt(kinds), [image], "Index this floor plan.", schema(kinds))
        out = {"cost_usd": reply.cost_usd, "latency_ms": reply.latency_ms, "usage": reply.usage(),
               "image": image.describe(), "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if reply.error:
            return {**out, "error": reply.error}
        if reply.data is None:
            return {**out, "no_answer": reply.no_answer or "no usable reply"}
        items: dict[str, list[dict]] = {kind: [] for kind in kinds}
        for item in reply.data.get("items") or []:
            if not isinstance(item, dict) or item.get("label") not in items:
                continue
            box = _box(item, image.width / image.scale, image.height / image.scale)
            if box:
                items[item["label"]].append(box)
        unsure = [kind for kind in reply.data.get("unsure") or [] if kind in items]
        return {**out, "items": items, "unsure": unsure}


def make_engine(vlm, grouping: str = "one", other: Callable[[Question, Dataset], Answer] | None = None,
                cache_dir: Path | None = None) -> Callable[[Question, Dataset], Answer]:
    """Answer counting questions about a scan from its index; `other` answers what the index cannot."""
    reader = Reader(vlm, grouping, cache_dir)
    method = method_name(vlm, grouping)

    def answer(q: Question, dataset: Dataset) -> Answer:
        kinds = kinds_asked(q.question)
        if kinds is None or not can_render(q.path) or not q.path.lower().endswith((".png", ".jpg", ".jpeg")):
            if other is not None:
                return other(q, dataset)
            return Answer.decline("not a count the drawing's index holds", method=method, cost_usd=0.0)
        t0 = time.perf_counter()
        cost = 0.0
        latency = 0.0
        counted: list[dict] = []
        unsure: list[str] = []
        usage: list[dict] = []
        for group in sorted({reader.group_of(kind) for kind in kinds}):
            data, first = reader.reading(dataset.abspath(q.path), group)
            if first:  # the reading is paid for once, by the first question that needs it
                cost += data.get("cost_usd") or 0.0
                latency += data.get("latency_ms") or 0.0
                usage.append(data.get("usage") or {})
            common = dict(method=method, cost_usd=cost, latency_ms=latency or (time.perf_counter() - t0) * 1000,
                          meta={"read_once": not first, "usage": usage})
            if data.get("error"):
                return Answer.failed(data["error"], **common)
            if "items" not in data:
                return Answer.decline(data.get("no_answer") or "no usable reply", **common)
            for kind in kinds:
                if kind in data["items"]:
                    counted.extend({**box, "label": kind.replace("_", " ")} for box in data["items"][kind])
                    if kind in data.get("unsure", []):
                        unsure.append(kind)
        meta = {"read_once": cost == 0.0, "kinds": list(kinds), "grouping": grouping, "usage": usage,
                "interpreted": "count of " + " and ".join(kind.replace("_", " ") + "s" for kind in kinds)
                               + " in the drawing's index"}
        common = dict(method=method, cost_usd=cost, latency_ms=latency or (time.perf_counter() - t0) * 1000, meta=meta)
        if unsure:
            return Answer.decline("the model could not mark this kind reliably on the image: "
                                  + ", ".join(kind.replace("_", " ") for kind in unsure), **common)
        count = len(counted)
        evidence = counted or [{"kind": "formula", "text": "the model marked nothing of this kind"}]
        rule = "; ".join(KINDS[kind] for kind in kinds)
        return Answer(answer=count, unit="count", state=CHECK, confidence=None, evidence=evidence,
                      explanation=f"{count} marked on the plan by the model when it read the drawing, counted by the code. "
                                  f"Marked by this rule: {rule}.",
                      **common)

    return answer
