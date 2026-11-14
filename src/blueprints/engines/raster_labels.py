"""Room counts on a raster plan, from the text printed on it.

For plans that exist only as pixels (CubiCasa). Finnish plans label their rooms: "MH"
is a bedroom, "K" or "Keittiö" a kitchen, "KH / KPH / PH" a bathroom, "S" a sauna,
"WC" a toilet room, "Parveke" a balcony. Many also print the apartment's type line,
"3H+K+S": three rooms, a kitchen and a sauna. A local OCR model reads the text (see
ocr_index.py); from there on everything is rules a person can read:

    readings of the plan  ->  one label per place  ->  a kind per label  ->  count

That gives two sources printed on the same drawing: the room labels and the type line.
The answer is a count of labels (or what the type line states), every counted label is
a box on the plan, and the score says whether the two sources agree.

Covered: bedrooms, kitchens, bathrooms/washrooms, separate WC rooms, saunas, balconies.
Not covered: symbol counts (doors, windows, toilet bowls, sinks, showers); those are
drawn, not written, and need a detector. Declined rather than guessed: a plan without
readable room labels, a plan that looks mirrored, and a kitchen or bathroom count
that would come out as zero only because no label was read.

Answers are state "check"; the score is what calibration works with.
"""

from __future__ import annotations

import re
import statistics
import time
import unicodedata
from dataclasses import dataclass, field

from ..answer import Answer
from ..data import Dataset, Question
from ..questions import measure_of, norm, strip_quoted
from . import ocr_index

METHOD = "raster-labels:v1"

KINDS = ("bedroom", "kitchen", "bathroom", "wc", "sauna", "balcony")

# ----------------------------------------------------------------- the question

# The counted thing is named right after the counting phrase; later words only explain
# it ("sinks ... (kitchen and bathroom combined ...)" must not read as a kitchen count).
_SYMBOL_WORDS = re.compile(
    r"bowl|seat|istuin|sink|basin|allas|altaa|shower|bathtub|suihku|amme|door|window|ovi|ovea|ovia|ikkuna"
    r"|floors|stor(e)?ys?\b|kerros|kerrosta"
)
_QUESTION_KINDS: dict[str, re.Pattern] = {
    "bedroom": re.compile(r"\bbedrooms?\b|\bmakuuhuone\w*"),
    "kitchen": re.compile(r"\bkitchens?\b|\bkeittiö\w*"),
    "bathroom": re.compile(r"\bbathrooms?\b|\bwashrooms?\b|\bkylpyhuone\w*|\bpesuhuone\w*|\bkylpy- tai pesuhuone\w*"),
    "wc": re.compile(r"\bwc rooms?\b|\btoilet rooms?\b|\bwc-tila\w*"),
    "sauna": re.compile(r"\bsaunas?\b|\bsaunaa\b|\bsaunoja\b"),
    "balcony": re.compile(r"\bbalcon(y|ies)\b|\bparveke\w*|\bparvekkeita\b"),
}
_HEAD = re.compile(r"^(.*?)(?:\(|,|;|\?|$)")
_PLAN_WORDS = re.compile(r"\b(on|in) (this|the) floor ?plan\b|\bfloor ?plans?\b|\bpohjapiirustu\w*")


def room_kind_in(question: str) -> str | None:
    """The labelled room kind a counting question asks for, or None if it asks for something else."""
    if measure_of(question) != "count":
        return None
    text = norm(strip_quoted(question))
    head = _HEAD.match(text).group(1)  # up to the first bracket or comma: the counted noun
    head = _PLAN_WORDS.sub(" ", head)
    if _SYMBOL_WORDS.search(head):
        return None
    found = [kind for kind, pattern in _QUESTION_KINDS.items() if pattern.search(head)]
    return found[0] if len(found) == 1 else None


# ----------------------------------------------------------------- one reading

# An area after the label: "MH 15.0", "K 17,6 m²", "MAK.H. 10.2 m2", also "MH1".
_AREA_TAIL = re.compile(r"[\s.:=]*\d[\d.,]*\s*(?:M2|M)?\s*$")
_ONLY_AREA = re.compile(r"^\d{1,3}[.,]\d{1,2}\s*(?:M2|M)?$|^\d{1,3}\s*M2$")
# "3H+K+S", "2 H + KK", "2-3h+k+s 71", "3H+K, 81,0 m2", "2HK": the apartment's type line.
_TYPE_LINE = re.compile(r"(?:^|[\s(\-])(\d)?(?:\s*-\s*(\d))?\s*H\s*(?:[+,&.]\s*|(?=K))(K.*|TUPAK.*|AVOK.*)$")
# The same line with its first character misread ("CH+KT" for "3H+KT").
_TYPE_LINE_LOOSE = re.compile(r"^[^O\s]?H\s*\+\s*(K.*|TUPAK.*|AVOK.*)$")  # not "OH+K": that is a room
_TYPE_PART = re.compile(r"^[A-Z.]+")
_SEPARATORS = re.compile(r"[+/&,;]|\s+-\s+|\s+")

_WORD_KINDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^MAKUU|^MAKH|^MH\d$"), "bedroom"),
    (re.compile(r"^KEITTI|^KEITTO|^AVOKEIT|^TUPAKEIT|^APUKEIT"), "kitchen"),
    (re.compile(r"^KYLPYH|^KYLPH$|^PESUH|^SUIHKUH"), "bathroom"),
    (re.compile(r"^SAUNA"), "sauna"),
    (re.compile(r"^PARVEKE|^LASIPARV|^PARV$|^LASITETTUPARV|^LASPARV"), "balcony"),
]
_ABBREVIATIONS: dict[str, str] = {
    "MH": "bedroom",
    "K": "kitchen", "KT": "kitchen", "KK": "kitchen", "AVOK": "kitchen", "TUPAK": "kitchen", "APUK": "kitchen",
    "KH": "bathroom", "KPH": "bathroom", "PH": "bathroom", "SH": "bathroom",
    "WC": "wc",
    "S": "sauna",
}
# What the parts of a type line stand for ("2H+KK+S+P").
_TYPE_PARTS: dict[str, str] = {
    "K": "kitchen", "KK": "kitchen", "KT": "kitchen", "TK": "kitchen", "AVOK": "kitchen", "TUPAK": "kitchen",
    "KEITTIO": "kitchen", "KEITTOKOMERO": "kitchen", "S": "sauna", "SA": "sauna", "SAUNA": "sauna",
    "P": "balcony", "PARV": "balcony", "PARVEKE": "balcony", "LASP": "balcony", "LASPARV": "balcony",
    "LP": "balcony",
}
# Other things written on plans: rooms that are none of the six kinds, and fittings.
# They show that a plan is labelled, and they keep a look-alike from being counted
# ("KHH" is a utility room, not the bathroom "KH").
_OTHER_ROOMS = {
    "OH", "ET", "VH", "KHH", "TK", "VAR", "RT", "RH", "TH", "AULA", "AH", "ALK", "LH", "AK", "AT", "PKH",
    "TKH", "ESH", "TYOH", "ULKOVAR", "HALLI", "KUISTI", "TERASSI", "PATIO", "PARVI", "KELLARI", "AUTOTALLI",
    "AUTOKATOS", "VARASTO", "OLOHUONE", "OLOH", "ETEINEN", "RUOKAILU", "RUOKAILUTILA", "TUULIKAAPPI",
    "KODINHOITOH", "KODINHOITOHUONE", "VAATEHUONE", "VAATEH", "TYOHUONE", "TEKN", "PUKUH", "PUKUHUONE",
    "KIRJASTO", "TAKKAH", "TAKKAHUONE", "ASKARTELU", "HUONE", "LASTENH", "VIERASH", "ARKIH", "ARKIHUONE",
    "TUPA", "KAMARI",
}
_FITTINGS = {"JK", "PK", "APK", "PPK", "LKV", "PAK", "SK", "LVK", "JKP", "KOM", "PKK", "LK", "KKK", "LTO", "UUNI",
             "LIESI", "TAKKA", "KIUAS", "PYK", "PPKK", "KTK", "LAVA"}
# What "MH" reads as in a mirror image, whichever way up: a sign the scan is mirrored.
_MIRRORED = {"HM", "WH"}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).upper()
    return text.replace("Ä", "A").replace("Ö", "O").replace("Å", "A").strip()


@dataclass
class Reading:
    """What one piece of read text says, by the rules."""

    text: str
    kinds: frozenset = frozenset()   # room kinds this label names
    other: bool = False              # a known label of another kind (room or fitting)
    compound: bool = False           # names several rooms at once ("OH+KK")
    has_area: bool = False           # the label carries its area ("MH 15.0")
    only_area: bool = False          # nothing but an area ("15.0", "30,0 m²")
    bare: bool = False               # a lone letter, e.g. just "K" or "S"
    mirrored: bool = False           # reads like a mirrored "MH"
    type_line: dict | None = None    # {"rooms": 3 or None, "kinds": [...]} for "3H+K+S"

    @property
    def known(self) -> bool:
        return bool(self.kinds) or self.other or self.type_line is not None


def _read_type_line(folded: str) -> dict | None:
    m = _TYPE_LINE.search(folded)
    if m:
        rooms = max(int(d) for d in m.group(1, 2) if d) if (m.group(1) or m.group(2)) else None
        rest = m.group(3)
    else:
        m = _TYPE_LINE_LOOSE.match(folded)
        if not m:
            return None
        rooms, rest = None, m.group(1)
    kinds = []
    for part in re.split(r"[+,&/.\s]+", rest):
        letters = _TYPE_PART.match(part.strip("()"))
        kind = _TYPE_PARTS.get(letters.group(0).replace(".", "")) if letters else None
        if kind and kind not in kinds:
            kinds.append(kind)
    if rooms is None and "kitchen" not in kinds:
        return None  # "H + something" without a kitchen is too little to call a type line
    return {"rooms": rooms, "kinds": kinds}


def read_text(text: str) -> Reading:
    folded = _fold(text).strip("()[] ")   # "(MH 1)"
    if _ONLY_AREA.match(folded):
        return Reading(text=text, only_area=True)
    type_line = _read_type_line(folded)
    if type_line is not None:
        return Reading(text=text, type_line=type_line)
    core = _AREA_TAIL.sub("", folded)
    has_area = core != folded
    tokens = [t.strip(".:-").replace(".", "") for t in _SEPARATORS.split(core)]
    tokens = [t for t in tokens if t]
    joined = "".join(tokens)
    kinds: set[str] = set()
    others = 0
    for token in tokens:
        kind = _ABBREVIATIONS.get(token) or next((k for p, k in _WORD_KINDS if p.search(token)), None)
        if kind:
            kinds.add(kind)
        elif token in _OTHER_ROOMS or token in _FITTINGS:
            others += 1
    if not kinds and not others and len(tokens) > 1:
        # "MAK. H.", "LAS. PARV.": one name written in pieces.
        kind = _ABBREVIATIONS.get(joined) or next((k for p, k in _WORD_KINDS if p.search(joined)), None)
        if kind:
            kinds.add(kind)
        elif joined in _OTHER_ROOMS or joined in _FITTINGS:
            others += 1
    compound = len(kinds) + others > 1
    # A toilet inside a bathroom label ("KPH/WC") is not a separate WC room.
    if "wc" in kinds and "bathroom" in kinds:
        kinds.discard("wc")
    bare = len(tokens) == 1 and len(tokens[0]) == 1
    return Reading(text=text, kinds=frozenset(kinds), other=bool(others), compound=compound,
                   has_area=has_area, bare=bare, mirrored=joined in _MIRRORED)


# ----------------------------------------------------------------- one label per place


@dataclass
class Label:
    """One place on the plan where text was read, with its reading at each size."""

    bbox: tuple[float, float, float, float]
    height: float                      # letter height in pixels of the original file
    readings: list[tuple[int, float, Reading]] = field(default_factory=list)  # (pass, score, reading)

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2

    def support(self, kind: str) -> list[tuple[int, float, Reading]]:
        return [x for x in self.readings if kind in x[2].kinds]

    def against(self, kind: str) -> list[tuple[int, float, Reading]]:
        return [x for x in self.readings if x[2].known and kind not in x[2].kinds]

    def best(self, kind: str | None = None) -> tuple[int, float, Reading]:
        pool = self.support(kind) if kind else [x for x in self.readings if x[2].known] or self.readings
        return max(pool, key=lambda x: x[1])

    @property
    def is_known(self) -> bool:
        return any(r.known for _, _, r in self.readings)

    def type_line(self) -> dict | None:
        lines = [(score, r.type_line) for _, score, r in self.readings if r.type_line is not None]
        return max(lines, key=lambda x: x[0])[1] if lines else None


def _bbox(box) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in box], [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _sides(box) -> tuple[float, float]:
    (x0, y0), (x1, y1), (x2, y2) = box[0], box[1], box[2]
    side_a = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    side_b = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return max(1.0, min(side_a, side_b)), max(1.0, max(side_a, side_b))


def _inside(point, bbox, margin: float) -> bool:
    return bbox[0] - margin <= point[0] <= bbox[2] + margin and bbox[1] - margin <= point[1] <= bbox[3] + margin


def choose(reads: list[dict], min_score: float = 0.5) -> tuple[float, Reading] | None:
    """The reading of a box to go by: the box was read at several quarter turns.

    A turn that gives a known label beats one that gives noise ("MH" beats the "HW" it
    reads as upside down); among known labels the most confident one wins.
    """
    best = None
    for read in reads:
        if read["score"] < min_score or not read["text"].strip():
            continue
        reading = read_text(read["text"])
        key = (reading.known, read["score"])
        if best is None or key > best[0]:
            best = (key, read["score"], reading)
    return None if best is None else (best[1], best[2])


def merge(index: dict, min_score: float = 0.5) -> list[Label]:
    """Combine the passes (the plan read at two sizes): readings at the same place become one label.

    Two readings from the same pass are never the same label. Known labels are placed
    first, so a label's box is that of its most confident reading.
    """
    detections = []
    for pass_no, reading_pass in enumerate(index["passes"]):
        for item in reading_pass["items"]:
            picked = choose(item["reads"], min_score)
            if picked is None:
                continue
            score, reading = picked
            short, long = _sides(item["box"])
            # A lone letter's box is as narrow as the letter: its height is the longer side.
            height = long if reading.bare else short
            detections.append((score, pass_no, _bbox(item["box"]), height, reading))
    detections.sort(key=lambda d: (not d[4].known, -d[0]))
    labels: list[Label] = []
    for score, pass_no, bbox, height, reading in detections:
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        home = None
        for label in labels:
            if any(p == pass_no for p, _, _ in label.readings):
                continue
            margin = 0.15 * min(height, label.height)
            if _inside(center, label.bbox, margin) or _inside(label.center, bbox, margin):
                home = label
                break
        if home is None:
            home = Label(bbox=bbox, height=height)
            labels.append(home)
        home.readings.append((pass_no, score, reading))
    return labels


# ----------------------------------------------------------------- counting


@dataclass
class Count:
    kind: str
    answer: int | None                  # None: declined, `reason` says why
    score: float
    counted: list[Label]                # the labels behind the answer
    type_label: Label | None            # the type line, when it was used
    source: str                         # "labels", "labels+type", "type"
    doubtful: list[tuple[Label, str]]   # labels that might be this kind but were not counted, with why
    known_labels: int
    notes: list[str]
    reason: str = ""


def _near_area(label: Label, labels: list[Label]) -> bool:
    """An area printed next to or under the label ("WC" above "2.3")."""
    cx, cy = label.center
    reach = 2.6 * label.height
    for other in labels:
        if other is label or not any(r.only_area for _, _, r in other.readings):
            continue
        ox, oy = other.center
        if abs(ox - cx) <= reach + (label.bbox[2] - label.bbox[0]) / 2 and abs(oy - cy) <= reach:
            return True
    return False


def _labels_of(kind: str, labels: list[Label], n_passes: int):
    """The labels that name this kind of room, the ones that only might, and the ones ruled out.

    Returns (counted, doubtful, ruled_out); the last two are lists of (label, why).
    """
    cupboards: list[tuple[Label, str]] = []
    rooms = [lb for lb in labels if any(r.known and not r.bare and r.type_line is None for _, _, r in lb.readings)]
    reference = statistics.median(lb.height for lb in rooms) if rooms else None
    with_area = sum(1 for lb in labels if any(r.has_area and r.known for _, _, r in lb.readings))
    area_style = with_area >= 2   # this plan prints an area with its room labels

    counted: list[Label] = []
    doubtful: list[tuple[Label, str]] = []
    for label in labels:
        support = label.support(kind)
        if not support:
            continue
        best_for = max(score for _, score, _ in support)
        against = label.against(kind)
        if against and max(score for _, score, _ in against) > best_for:
            doubtful.append((label, "read as something else at the other size"))
            continue
        if all(r.bare for _, _, r in support):
            # A lone "K" or "S": a room label only if it looks like the plan's other room labels.
            if area_style and not _near_area(label, labels):
                doubtful.append((label, "single letter without an area on a plan that prints areas"))
                continue
            if reference is not None and label.height < 0.7 * reference:
                doubtful.append((label, "single letter smaller than the room labels"))
                continue
            if len(support) < min(2, n_passes) and best_for < 0.9:
                doubtful.append((label, "single letter read once, weakly"))
                continue
        elif len(support) < min(2, n_passes) and best_for < 0.7:
            doubtful.append((label, "read once, weakly"))
            continue
        counted.append(label)
    if kind == "kitchen":
        # A lone "K" also marks a cupboard (komero). Next to a kitchen label that says more
        # ("KK", "KT", "Keittiö", "K 17.6") the lone ones are cupboards.
        lone = [lb for lb in counted if all(r.bare and not r.has_area for _, _, r in lb.support(kind))]
        if lone and len(lone) < len(counted):
            doubtful_lone = [(lb, "a lone K next to a full kitchen label: a cupboard (komero)") for lb in lone]
            counted = [lb for lb in counted if lb not in lone]
            cupboards.extend(doubtful_lone)
        elif len(lone) > 1:
            # Only lone K's, several of them: one is the kitchen, the others are cupboards.
            lone.sort(key=lambda lb: -lb.height)
            cupboards.extend((lb, "one of several lone K labels: cupboards are marked K too") for lb in lone[1:])
            counted = lone[:1]
        # "OH+KK" names the open space the kitchenette is in; its own "KK" is the same kitchen.
        plain = [lb for lb in counted if not all(r.compound for _, _, r in lb.support(kind))]
        compound = [lb for lb in counted if lb not in plain]
        if plain and compound:
            counted = plain if len(plain) >= len(compound) else compound
    return counted, doubtful, cupboards


def _solid(labels: list[Label], kind: str, n_passes: int) -> bool:
    """Every counted label was read the same way at every size, confidently."""
    for label in labels:
        support = label.support(kind)
        if len(support) < n_passes or max(score for _, score, _ in support) < 0.9 or label.against(kind):
            return False
    return True


def count_kind(index: dict, kind: str) -> Count:
    labels = merge(index)
    n_passes = len(index["passes"])
    known = [lb for lb in labels if lb.is_known]
    declined = dict(kind=kind, answer=None, score=0.0, counted=[], type_label=None, source="",
                    doubtful=[], known_labels=len(known), notes=[])
    if len(known) < 2:
        return Count(**declined, reason="no readable room labels on this plan, so rooms cannot be counted from labels")
    mirrored = [lb for lb in labels if any(r.mirrored and s >= 0.8 for _, s, r in lb.readings)]
    if mirrored and not any(lb.support("bedroom") for lb in labels):
        return Count(**declined, reason="the plan looks mirrored (its labels read backwards), so labels cannot be trusted")

    counted, doubtful, _ruled_out = _labels_of(kind, labels, n_passes)
    n = len(counted)
    solid = _solid(counted, kind, n_passes)
    well_labelled = len(known) >= 5

    # The type line, when the plan prints exactly one.
    type_labels = [lb for lb in labels if lb.type_line() is not None]
    type_label = type_labels[0] if len(type_labels) == 1 else None
    type_line = type_label.type_line() if type_label else None
    notes: list[str] = []
    if len(type_labels) > 1:
        notes.append("several type lines on the plan (more than one home?)")
    if doubtful:
        notes.append(f"{len(doubtful)} label(s) might also be a {kind}")

    def result(answer: int, score: float, source: str, used: list[Label] | None = None) -> Count:
        if len(type_labels) > 1 or doubtful:
            score = min(score, 0.6)
        return Count(kind=kind, answer=answer, score=score, counted=counted if used is None else used,
                     type_label=type_label if "type" in source else None, source=source, doubtful=doubtful,
                     known_labels=len(known), notes=notes)

    if kind == "bedroom":
        stated = type_line["rooms"] - 1 if type_line and type_line["rooms"] else None
        if stated is not None:
            if stated == n:
                return result(n, 0.99, "labels+type")
            notes.append(f"the type line states {type_line['rooms']} rooms, the labels show {n} bedroom(s)")
            return result(stated, 0.6, "type", used=[])
        # Labels alone: a neighbour's "MH" may be on the sheet, or a bedroom may carry no label.
        return result(n, 0.6, "labels")

    if kind == "sauna":
        stated = None if type_line is None or type_line["rooms"] is None else int("sauna" in type_line["kinds"])
        spelled = any(not r.bare or r.has_area for lb in counted for _, _, r in lb.support(kind))
        if stated is not None:
            if stated == min(n, 1):
                # Both say one sauna: the top score. Both say none: an unlabelled sauna on a
                # plan whose type line was misread is still possible, so one level lower.
                return result(n, 0.99 if n == 1 else 0.9 if n == 0 else 0.6, "labels+type")
            if stated == 1:
                notes.append("the type line states a sauna; its label was not read")
                return result(1, 0.9, "type", used=[])
            # A lone "S" also marks other things (a shower, a switchboard): the type line decides.
            notes.append(f"the type line states no sauna; {n} lone 'S' label(s) not counted")
            return result(0, 0.9 if not spelled else 0.6, "type", used=[])
        if n == 0:
            return Count(**declined, reason="no sauna label was read and the plan prints no type line; "
                                            "an unlabelled sauna cannot be told from none")
        return result(n, 0.9 if solid and spelled and n == 1 else 0.6, "labels")

    if kind == "kitchen":
        stated = 1 if type_line and "kitchen" in type_line["kinds"] else None
        if n == 0:
            if stated:
                notes.append("the type line states a kitchen; its label was not read")
                return result(1, 0.9, "type", used=[])
            return Count(**declined, reason="no kitchen label was read; nearly every home has a kitchen, "
                                            "so zero would be a guess")
        if n == 1:
            lone = all(r.bare and not r.has_area for _, _, r in counted[0].support(kind))
            if stated:
                return result(1, 0.99 if solid else 0.9, "labels+type")
            return result(1, 0.9 if solid and not (lone and _ruled_out) else 0.6, "labels")
        return result(n, 0.6, "labels")

    if kind == "bathroom":
        if n == 0:
            return Count(**declined, reason="no bathroom label was read; nearly every home has one, "
                                            "so zero would be a guess")
        return result(n, 0.99 if solid and well_labelled and n == 1 else 0.6, "labels")

    if kind == "balcony":
        stated = 1 if type_line and "balcony" in type_line["kinds"] else None
        if stated and n == 1:
            return result(n, 0.99, "labels+type")
        # No label is weak evidence of no balcony: it may simply not be labelled.
        return result(n, 0.9 if n and solid and well_labelled else 0.6, "labels")

    # separate WC rooms: the same holds, a WC without a label looks like no WC
    return result(n, 0.9 if n and solid and well_labelled else 0.6, "labels")


# ----------------------------------------------------------------- the engine

_NAMES = {"bedroom": "bedroom", "kitchen": "kitchen", "bathroom": "bathroom or washroom",
          "wc": "separate WC room", "sauna": "sauna", "balcony": "balcony"}


def _box(label: Label, text: str, score: float, **extra) -> dict:
    return {"kind": "box", "space": "image_px", "bbox": [round(v, 1) for v in label.bbox],
            "label": text, "score": round(score, 3), **extra}


def answer(q: Question, dataset: Dataset) -> Answer:
    started = time.perf_counter()

    def done(ans: Answer) -> Answer:
        ans.latency_ms = (time.perf_counter() - started) * 1000
        return ans

    if not q.path.lower().endswith((".png", ".jpg", ".jpeg")):
        return done(Answer.decline("not a raster plan", method=METHOD, cost_usd=0.0))
    kind = room_kind_in(q.question)
    if kind is None:
        return done(Answer.decline("not a count of labelled rooms (bedrooms, kitchens, bathrooms, WC rooms, "
                                   "saunas, balconies); symbols need a detector", method=METHOD, cost_usd=0.0))
    try:
        index = ocr_index.load_index(dataset.abspath(q.path))
    except Exception as exc:
        return done(Answer.failed(f"OCR failed: {type(exc).__name__}: {exc}", method=METHOD, cost_usd=0.0))
    result = count_kind(index, kind)
    meta = {"kind": kind, "source": result.source, "known_labels": result.known_labels, "notes": result.notes,
            "doubtful": [{"bbox": [round(v, 1) for v in lb.bbox], "label": lb.best()[2].text, "why": why}
                         for lb, why in result.doubtful],
            "ocr": index.get("models"), "ocr_seconds": index.get("seconds")}
    if result.answer is None:
        return done(Answer.decline(result.reason, method=METHOD, cost_usd=0.0, meta=meta))

    n, name = result.answer, _NAMES[kind]
    evidence = []
    for label in result.counted:
        _, score, reading = label.best(kind)
        evidence.append(_box(label, reading.text, score, passes=len(label.support(kind))))
    if result.type_label is not None:
        _, score, reading = result.type_label.best()
        evidence.append(_box(result.type_label, reading.text, score, role="type line"))
    texts = ", ".join(lb.best(kind)[2].text for lb in result.counted)
    if result.source == "type":
        type_text = result.type_label.best()[2].text
        evidence.append({"kind": "formula", "text": (
            f"type line '{type_text}': {result.type_label.type_line()['rooms']} rooms - 1 living room = {n} bedroom(s)"
            if kind == "bedroom" else f"type line '{type_text}' states a {name}")})
        explanation = f"{n} {name}(s), from the type line '{type_text}' printed on the plan."
    elif n:
        explanation = f"{n} label(s) on the plan read as a {name}: {texts}."
        if result.source == "labels+type":
            explanation += f" The type line '{result.type_label.best()[2].text}' states the same."
    else:
        evidence.append({"kind": "formula",
                         "text": f"{result.known_labels} labels read on the plan, none of them a {name}"})
        explanation = f"No label on the plan reads as a {name} ({result.known_labels} labels read)."
        if result.source == "labels+type":
            explanation += f" The type line '{result.type_label.best()[2].text}' states none either."
    if result.notes:
        explanation += " Note: " + "; ".join(result.notes) + "."
    return done(Answer(answer=n, unit="count", confidence=result.score, method=METHOD, evidence=evidence,
                       explanation=explanation, cost_usd=0.0, meta=meta))
