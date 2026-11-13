"""Reading a question without a language model: quoted names, what is measured, what is counted.

Rules, not a model: every rule here is a line a person can read and a test can pin.
A question the rules do not recognise is declined, never guessed.
"""

from __future__ import annotations

import re
import unicodedata

# Quote styles seen in the dataset: 'x', "x", «x», ‘x’, „x“, “x”. For the plain
# apostrophe the closing quote is one not followed by a letter, so 'Cage d'escalier'
# stays whole.
_QUOTED = re.compile(
    r"(?<!\w)'(.+?)'(?!\w)|\"([^\"]*)\"|«\s*([^»]*?)\s*»|‘([^’]*)’|[“„]([^“”]*)[“”]"
)


def quoted_strings(text: str) -> list[str]:
    """The names a question quotes, in order."""
    return [next(g for g in m.groups() if g is not None).strip() for m in _QUOTED.finditer(text)]


def strip_quoted(text: str) -> str:
    """The question with quoted names removed, so a room called 'Door store' counts nothing."""
    return _QUOTED.sub(" ", text)


def norm(text: str) -> str:
    """Comparison form of a name: unicode-normalised, case-folded, single spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


# NFKC turns "m²" into "m2" and "m³" into "m3", so one spelling covers both.
_AREA = re.compile(r"\bm2\b|m\^2|\bsq\.? ?m\b|square met")
_VOLUME = re.compile(r"\bm3\b|m\^3|cubic met")
_LENGTH = re.compile(r"\(m\)|\bin m\b|\bin met(er|re)s\b|\bin metern\b|\bi m\b|\bmeetrites\b|\bîn metri\b")
_COUNT = re.compile(
    r"\bhow many\b|\bnumber of\b|\bmitu\b|\bhvor mange\b|\bhoeveel\b|\bwie ?viele\b"
    r"|\bkuinka monta\b|\bmontako\b|\bcombien\b|\bquant[ie]\b|\bcu[aá]nt[oa]s\b|\bhur många\b"
    r"|\bnumărul de\b|\bnumarul de\b|\bcâte\b|\bcâți\b"
)


def measure_of(question: str) -> str | None:
    """count | area | volume | length, from the unit or the counting phrase; None if unclear."""
    text = norm(strip_quoted(question))
    if _VOLUME.search(text):
        return "volume"
    if _AREA.search(text):
        return "area"
    if _COUNT.search(text):
        return "count"
    if _LENGTH.search(text):
        return "length"
    return None


# Words for each countable kind. English and Estonian are checked against dev
# answers; the other languages are dictionary forms for the languages the dataset is
# known to contain. Matching is on whole words of the question minus quoted names.
_KIND_WORDS: dict[str, str] = {
    "door": r"doors?|uks|ust|uste|ukse|ukseid|døre?|deur(en)?|tür|türen?|ovi|ovea|ovia|portes?|porta|dörr(ar)?"
            r"|uși|ușă|usi",
    "window": r"windows?|aken|akent|akna|aknad|aknaid|vinduer?|raam|ramen|fenster|ikkunaa?|ikkunoita"
              r"|fenêtres?|fönster|finestr[ae]|ferestre|fereastră",
    "wall": r"walls?|sein|seina|seinad|seinu|væg|vægge|muur|muren|wand|wände|seinää?|seiniä|murs?"
            r"|vägg(ar)?|pereți|pereti|perete",
    "space": r"rooms?|spaces?|ruum\w*|rum|rummet|ruimtes?|raum|raums|räume|huone|huonetta|huoneita|pièces?"
             r"|camere|încăperi|incaperi",
    "stair": r"stairs?|staircases?|trepp|treppi|trepid|trappe|trapper|trap|trappen|treppen?"
             r"|porras|portaat|portaita|escaliers?|trappa|trappor|scări|scari|scară",
    "column": r"columns?|post|posti|postid|søjler?|kolom(men)?|stützen?|säulen?|pilaria?|pilareita"
              r"|poteaux?|pelare|stâlpi|stalpi|stâlp",
    "beam": r"beams?|tala|talad|talasid|bjælker?|balk(en)?|träger|palkkia?|palkkeja|poutres?|balkar|grinzi|grindă",
    "slab": r"slabs?|vahelagi|vahelaed|dæk|decken?|bodenplatten?|laattaa?|laattoja|dalles?|bjälklag|planșee",
    "roof": r"roofs?|katus(ed)?|dak(en)?|dach|dächer|katto|kattoa|toits?",
    "railing": r"railings?|piirded?|gelændere?|leuning(en)?|geländer|kaide|kaiteita",
    "ramp": r"ramps?|kaldtee|rampe[rn]?",
    "curtain_wall": r"curtain walls?",
    "covering": r"coverings?",
    "footing": r"footings?",
    "furniture": r"furniture|furnishings?",
    "sanitary": r"sanitary (fixtures?|terminals?|appliances?)|sanitärobjekte?|obiecte sanitare",
    "light": r"light fixtures?|luminaires?|leuchten?|corpuri de iluminat",
    "apartment": r"apartments?|appartements?|logements?|wohnung(en)?|appartament[oi]",
    "toilet": r"toilets?|wcs?|toiletten?|toilettes",
    "washbasin": r"washbasins?|wash basins?|lavabos?|lavabi|waschbecken",
    "bathtub": r"bathtubs?|baignoires?|badewannen?|vasche da bagno|vasca da bagno",
    "shower": r"showers?|douches?|duschen?|docce|doccia",
}
_KINDS = {kind: re.compile(rf"(?<!\w)(?:{words})(?!\w)") for kind, words in _KIND_WORDS.items()}
_MODEL_SCOPE = re.compile(r"(?<!\w)(model|mudel|modell|modèle|modelo|modello|malli)\w*")


def kinds_in(question: str) -> list[str]:
    """Every countable kind the question mentions (outside quoted names)."""
    text = norm(strip_quoted(question))
    return [kind for kind, pattern in _KINDS.items() if pattern.search(text)]


def asks_about_whole_model(question: str) -> bool:
    return bool(_MODEL_SCOPE.search(norm(strip_quoted(question))))


_APARTMENT_REF = re.compile(r"(?<!\w)(?:apartment|appartement|wohnung|appartamento)\s+([a-z]|\d{1,3})(?!\w)")
_NUMBERED_ROOMS = re.compile(r"(?<!\w)(numbered|numérotées|nummerierten|numerati)(?!\w)")
_BARE_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)*[a-z]?(?![\w.])")


def apartment_in(question: str) -> str | None:
    """The apartment a question names ("apartment B", "Wohnung B", "l'appartement B"), upper-cased."""
    m = _APARTMENT_REF.search(norm(strip_quoted(question)))
    return m.group(1).upper() if m else None


def asks_about_numbered_rooms(question: str) -> bool:
    return bool(_NUMBERED_ROOMS.search(norm(strip_quoted(question))))


def bare_numbers(question: str) -> list[str]:
    """Number-like tokens outside quotes, e.g. the "2.01" of "room 2.01 ('Zimmer')"."""
    return _BARE_NUMBER.findall(norm(strip_quoted(question)))


_ROOM_REF = re.compile(r"(\S+)\s*\(\s*['\"«‘“„]")


def room_ref_in(question: str) -> str | None:
    """The room identifier in "room 2.01 ('Zimmer')": the token right before the bracketed name.

    Structural, so it works for any numbering style ("U1.13") and any language. A
    question built this way asks about ONE room; if that room is not on the drawing the
    question must be declined, not read as something else.
    """
    m = _ROOM_REF.search(question)
    return m.group(1) if m else None
