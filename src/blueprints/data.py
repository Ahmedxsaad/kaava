"""Loader for Stoa's dataset: questions.jsonl plus an index of the drawing files.

Two identities matter for honest evaluation:

  drawing  one drawing, whatever its file format. "Files with the same name and a
           different extension are the same drawing" (dataset README).
  group    the unit that must stay inside ONE of dev / calibration / test. It is
           wider than a drawing wherever drawings share content: floors of the same
           Swiss site repeat almost unchanged, the plans rendered from an IFC model
           show that model, sheets of one project describe one building.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import config

NUMERIC_TYPES = ("count", "area", "length", "volume")

_SWISS_RE = re.compile(r"^(site\d+)_bldg\d+_floor\d+$")
# A name in quotes. The closing quote is one not followed by a letter, so French
# names with an apostrophe inside ('Cage d'escalier') stay in one piece.
_QUOTED_RE = re.compile(r"(?<!\w)'.+?'(?!\w)|\"[^\"]*\"|«[^»]*»|‘[^’]*’|[“„][^“”]*[“”]")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_IFCPROJECT_RE = re.compile(rb"IFCPROJECT\s*\(\s*'([^']{22})'", re.IGNORECASE)


@dataclass(frozen=True)
class Question:
    qid: str  # stable id: hash of path + question text
    path: str  # as written in questions.jsonl, relative to the dataset root
    question: str
    answer: Any
    unit: str | None
    type: str
    source: str
    drawing: str
    group: str

    @property
    def family(self) -> tuple[str, str]:
        return (self.source, self.type)

    @property
    def template(self) -> str:
        """The question with names and numbers blanked: same template, same kind of ask."""
        return question_template(self.question)


def question_id(path: str, question: str) -> str:
    return hashlib.sha1(f"{path}\x1f{question}".encode("utf-8")).hexdigest()[:12]


def question_template(question: str) -> str:
    t = _QUOTED_RE.sub("'X'", question)
    t = _NUMBER_RE.sub("N", t)
    return " ".join(t.split())


def source_of(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[1] if len(parts) > 2 and parts[0] == "data" else parts[0]


def drawing_of(path: str) -> str:
    """Path without its extension: the same drawing in any format."""
    p = PurePosixPath(path)
    return str(p.with_suffix("")) if p.suffix else str(p)


class Dataset:
    """Questions and files of one unpacked dataset folder."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else config.dataset_dir()
        if not (self.root / "questions.jsonl").is_file():
            raise FileNotFoundError(f"{self.root} has no questions.jsonl")
        self._guid_cache: dict[str, str | None] = {}

    # ---- files

    @cached_property
    def files(self) -> frozenset[str]:
        """Every file under data/, as a posix path relative to the dataset root."""
        found = []
        data = self.root / "data"
        for dirpath, _dirs, names in os.walk(data):
            rel = Path(dirpath).relative_to(self.root).as_posix()
            found.extend(f"{rel}/{n}" for n in names)
        return frozenset(found)

    @cached_property
    def _by_drawing(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = defaultdict(list)
        for f in self.files:
            index[drawing_of(f)].append(f)
        return {k: sorted(v) for k, v in index.items()}

    @cached_property
    def _ifc_models(self) -> dict[str, list[str]]:
        """IFC stems per folder, longest first, to match `<model>_<level>` renders."""
        per_dir: dict[str, list[str]] = defaultdict(list)
        for f in self.files:
            p = PurePosixPath(f)
            if p.suffix.lower() == ".ifc":
                per_dir[str(p.parent)].append(p.stem)
        return {d: sorted(stems, key=len, reverse=True) for d, stems in per_dir.items()}

    def abspath(self, rel: str) -> Path:
        return self.root / rel

    def formats(self, path: str) -> list[str]:
        """All files that are this same drawing in another format (including itself)."""
        return self._by_drawing.get(drawing_of(path), [])

    def ifc_model_of(self, path: str) -> str | None:
        """For `<model>_<level>.pdf|png|svg`, the drawing id of the `<model>.ifc` it shows."""
        p = PurePosixPath(path)
        if p.suffix.lower() == ".ifc":
            return drawing_of(path)
        for stem in self._ifc_models.get(str(p.parent), []):
            if p.stem.startswith(stem + "_"):
                return str(p.parent / stem)
        return None

    def renders_of(self, ifc_path: str) -> list[str]:
        """The floor plans rendered from an IFC model: `<model>_<level>.*` beside it."""
        model = drawing_of(ifc_path)
        parent = str(PurePosixPath(ifc_path).parent)
        out = []
        for f in self.files:
            p = PurePosixPath(f)
            if str(p.parent) == parent and p.suffix.lower() != ".ifc" and self.ifc_model_of(f) == model:
                out.append(f)
        return sorted(out)

    # ---- identities

    def group_of(self, path: str) -> str:
        """The leakage unit for splitting (see module docstring)."""
        source = source_of(path)
        p = PurePosixPath(path)
        if source == "swiss_dwellings":
            m = _SWISS_RE.match(p.stem)
            return f"{source}/{m.group(1)}" if m else f"{source}/{p.stem}"
        if source == "opening_design":
            # data/opening_design/<project folder>/...: one project, one building.
            parts = p.parts
            return f"{source}/{parts[2]}" if len(parts) > 3 else f"{source}/{p.stem}"
        if source == "ifc_models":
            # `<collection>-<project>_<file>[_plan-<level>]`: every model and rendered
            # plan of one project stays together (e.g. bsc-duplex_duplex-a-20110907).
            return f"{source}/{p.stem.split('_', 1)[0]}"
        if source == "ifc_bench":
            # `<project>_<discipline>.ifc`: arc / str / mep models of one building.
            return f"{source}/{p.stem.rsplit('_', 1)[0]}"
        return f"{source}/{p.stem}"

    def ifc_project_guid(self, path: str) -> str | None:
        """GlobalId of the IfcProject in an IFC file (plain text scan, no IFC library).

        The same building can sit in two sources under different file names (the
        Duplex model is in both ifc_models and ifc_bench); the GlobalId reveals it.
        """
        if path not in self._guid_cache:
            guid = None
            try:
                with open(self.abspath(path), "rb") as f:
                    tail = b""
                    while chunk := f.read(4 << 20):
                        m = _IFCPROJECT_RE.search(tail + chunk)
                        if m:
                            guid = m.group(1).decode("ascii", "replace")
                            break
                        tail = chunk[-64:]
            except OSError:
                pass
            self._guid_cache[path] = guid
        return self._guid_cache[path]

    # ---- questions

    @cached_property
    def questions(self) -> list[Question]:
        out: list[Question] = []
        seen: dict[str, int] = {}
        with open(self.root / "questions.jsonl", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                path, text = rec["path"], rec["question"]
                qid = question_id(path, text)
                if qid in seen:  # an exact repeat of path + question
                    seen[qid] += 1
                    qid = f"{qid}-{seen[qid]}"
                else:
                    seen[qid] = 0
                out.append(
                    Question(
                        qid=qid,
                        path=path,
                        question=text,
                        answer=rec.get("answer"),
                        unit=rec.get("unit"),
                        type=rec.get("type") or "other",
                        source=source_of(path),
                        drawing=drawing_of(path),
                        group=self.group_of(path),
                    )
                )
        return out

    @cached_property
    def by_qid(self) -> dict[str, Question]:
        return {q.qid: q for q in self.questions}

    def family_weights(self, questions: Iterable[Question] | None = None) -> dict[tuple[str, str], float]:
        """Share of each (source, type) in the question mix."""
        qs = list(questions) if questions is not None else self.questions
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for q in qs:
            counts[q.family] += 1
        total = sum(counts.values())
        return {k: v / total for k, v in sorted(counts.items())}
