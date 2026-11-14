"""HTTP API behind the verification UI.

The UI asks a question about one drawing and gets back the answer, its state
(Verified / Check this / Can't answer reliably), why, what it cost, and the evidence
in the pixel coordinates of the image it is shown on (or as model elements for 3D).

Only drawings of the dev split are served: the calibration and test splits stay
unseen. Drawings without any question (archive scans) belong to no split and can be
browsed too. The dataset's own answer is returned next to ours for comparison only;
it is looked up after the engine has answered and never reaches an engine.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cloud, config, interpret, mesh, overlay, sheet, verify
from .answer import Answer
from .data import Dataset, Question, drawing_of, question_id, source_of
from .engines import dxf, dxf_index, ifc, ifc_index, raster_labels, router
from .harness import FatalEngineError
from .scoring import CHECK, VERIFIED, score_answer
from .split import Split

VIEW_LONG_EDGE = 3200
THUMB_EDGE = 360
RASTER = (".png", ".jpg", ".jpeg")
ARCHIVE_SOURCES = ("finna", "habs", "europeana", "wikimedia")  # drawings nobody wrote questions for
CREDITS = {
    "cubicasa5k": "CubiCasa5K © 2019 CubiCasa, CC BY-NC-SA 4.0 (non-commercial)",
    "swiss_dwellings": "Drawn from Swiss Dwellings v3.0.0, Archilyse AG, CC BY 4.0",
    "ifc_models": "Open IFC model; licence and credit per model in the source's LICENSE.md",
    "ifc_bench": "IFC-Bench v2 (S. Hellin, TUM), CC BY 4.0; model licences in LICENSE.md",
    "opening_design": "OpeningDesign, Madison WI, CC BY-SA 4.0 / MIT per file",
    "finna": "Helsinki City Archives / Architecture & Design Museum via Finna, CC BY 4.0",
    "habs": "Historic American Buildings Survey, Library of Congress (public domain)",
    "europeana": "Via Europeana; licence per file in the source's LICENSE.md",
    "wikimedia": "Wikimedia Commons; licence per file in the source's LICENSE.md",
    "uploads": "Brought by you. Not part of the dataset.",
}
UPLOADS_DIR = config.REPO_ROOT / "uploads"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# What to offer on a drawing nobody wrote questions for.
GENERIC_QUESTIONS = (
    "How many doors are shown on this floor plan?",
    "How many windows are shown on this floor plan?",
    "How many bedrooms are on this floor plan?",
    "How many toilet bowls are drawn on this floor plan?",
)


class Library:
    """The dataset plus the drawings people bring. To the engines it looks like a Dataset."""

    def __init__(self, dataset: Dataset):
        self._ds = dataset
        self.uploads: dict[str, Path] = {}  # "uploads/<id>.png" -> the file
        self.notes: dict[str, dict] = {}  # drawing id -> what is known about an upload
        if UPLOADS_DIR.is_dir():
            for note in sorted(UPLOADS_DIR.glob("*.json")):
                image = note.with_suffix(".png")
                if image.is_file():
                    self.add(image, json.loads(note.read_text(encoding="utf-8")))

    def __getattr__(self, name: str):
        return getattr(self._ds, name)

    def add(self, image: Path, note: dict) -> str:
        rel = f"uploads/{image.name}"
        self.uploads[rel] = image
        self.notes[drawing_of(rel)] = note
        return drawing_of(rel)

    def uploads_of(self, drawing: str) -> list[str]:
        return [rel for rel in self.uploads if drawing_of(rel) == drawing]

    def abspath(self, rel: str) -> Path:
        return self.uploads.get(rel) or self._ds.abspath(rel)

    def formats(self, path: str) -> list[str]:
        return self.uploads_of(drawing_of(path)) or self._ds.formats(path)

    def ifc_model_of(self, path: str) -> str | None:
        return None if path.startswith("uploads/") else self._ds.ifc_model_of(path)

    def group_of(self, path: str) -> str:
        return "uploads" if path.startswith("uploads/") else self._ds.group_of(path)
ENGINE_LABELS = {
    ifc.METHOD: "IFC model query",
    dxf.METHOD: "DXF geometry",
    raster_labels.METHOD: "Printed room labels (local OCR)",
    router.METHOD: "Router",
}


@dataclass
class View:
    """The image a drawing is shown on, and how drawing coordinates map onto its pixels."""

    kind: str  # plan (PDF lined up with its DXF) | page (a PDF page) | image (a raster file) | none
    width: int = 0
    height: int = 0
    source_file: str | None = None  # dataset-relative file the image comes from
    page: int = 0
    pages: int = 1
    aligned: bool = False
    note: str = ""
    # plan views: PDF points -> pixels, and DXF metres -> PDF points
    clip: tuple[float, float, float, float] | None = None
    zoom: float = 1.0
    transform: dict | None = None
    dxf_file: str | None = None
    extra: dict = field(default_factory=dict)

    def px(self, x: float, y: float) -> list[float]:
        """DXF metres -> pixels of this view."""
        t = self.transform
        return [round((t["scale"] * x + t["tx"] - self.clip[0]) * self.zoom, 1),
                round((-t["scale"] * y + t["ty"] - self.clip[1]) * self.zoom, 1)]


class AskRequest(BaseModel):
    drawing: str
    question: str
    use_model: bool = False


class SpeakRequest(BaseModel):
    text: str


def speak(text: str) -> bytes:
    """The answer read aloud by ElevenLabs, as MP3. Needs ELEVENLABS_API_KEY."""
    import urllib.request

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or os.environ.get("BLUEPRINTS_OFFLINE"):
        raise HTTPException(503, "no voice service is configured")
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    body = json.dumps({"text": text[:400], "model_id": os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")}).encode()
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128", data=body,
        headers={"xi-api-key": key, "content-type": "application/json", "accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except Exception as exc:
        raise HTTPException(502, f"the voice service did not answer: {type(exc).__name__}") from None


class Service:
    """Everything the API needs, loaded once."""

    def __init__(self, dataset_root: Path | str | None = None):
        from dotenv import load_dotenv

        load_dotenv(config.REPO_ROOT / ".env")  # partner keys, if any; never printed
        self.ds = Library(Dataset(dataset_root))
        self.split = Split.load()
        self.thresholds = verify.load_thresholds()
        self.lock = threading.Lock()
        self.spent_eur = 0.0
        self.budget_eur = float(os.environ.get("BLUEPRINTS_UI_BUDGET_EUR", "1.0"))
        self.questions: dict[str, list[Question]] = defaultdict(list)
        for q in self.ds.questions:
            if self.split.of(q) == "dev":
                self.questions[q.drawing].append(q)
        self.archive = sorted({drawing_of(f) for f in self.ds.files
                               if source_of(f) in ARCHIVE_SOURCES and f.lower().endswith(RASTER + (".pdf",))})
        self._views: dict[tuple[str, int], View] = {}
        self._meshes: dict[str, tuple] = {}
        self._plan_fits: dict[str, dict | None] = {}
        self._restated: dict[tuple[str, str], interpret.Restated] = {}  # a question is only restated once
        self.logged = self._load_logged_runs()

    # ---- which drawings may be shown

    def allowed(self, drawing: str) -> bool:
        if drawing in self.questions:
            return True
        if drawing.startswith("uploads/"):
            return bool(self.ds.uploads_of(drawing))
        if source_of(drawing) in ARCHIVE_SOURCES:
            return drawing in set(self.archive)
        files = self._files(drawing)  # an IFC model that only its plans have questions about
        return bool(files) and self.split.groups.get(self.ds.group_of(files[0])) == "dev"

    def _files(self, drawing: str) -> list[str]:
        """Every format of a drawing. `drawing` is already a path without its extension, and archive
        names contain dots, so it must not be stripped a second time."""
        return self.ds.uploads_of(drawing) or self.ds._by_drawing.get(drawing, [])

    def files_of(self, drawing: str) -> dict[str, str]:
        return {PurePosixPath(f).suffix.lower(): f for f in self._files(drawing)}

    def model_of(self, drawing: str) -> str | None:
        """The IFC file behind a drawing: itself, or the model a plan was rendered from."""
        files = self.files_of(drawing)
        if ".ifc" in files:
            return files[".ifc"]
        any_file = next(iter(files.values()), None)
        model = self.ds.ifc_model_of(any_file) if any_file else None
        return f"{model}.ifc" if model and f"{model}.ifc" in self.ds.files else None

    def engine_of(self, drawing: str) -> str | None:
        files = self.files_of(drawing)
        if self.model_of(drawing):
            return "ifc"
        if ".dxf" in files:
            return "dxf"
        return None

    # ---- logged runs (answers already paid for)

    def _load_logged_runs(self) -> dict[str, dict[str, dict]]:
        """Latest logged answer per question for the image-only baseline and the pointing run."""
        out: dict[str, dict[str, dict]] = {"baseline": {}, "pointing": {}}
        runs = config.RESULTS_DIR / "runs"
        if not runs.is_dir():
            return out
        for run in sorted(runs.iterdir()):
            kind = ("baseline" if "-vlm-image-only-" in run.name and "router" not in run.name
                    else "pointing" if "-vlm-pointing-" in run.name else None)
            summary, preds = run / "summary.json", run / "predictions.jsonl"
            if kind is None or not summary.is_file() or not preds.is_file():
                continue
            meta = json.loads(summary.read_text(encoding="utf-8"))
            if meta.get("split") != "dev":
                continue
            with open(preds, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        if not row.get("error"):
                            out[kind][row["qid"]] = {**row, "run_id": run.name}
        return out

    # ---- views

    def view(self, drawing: str, page: int = 0) -> View:
        key = (drawing, page)
        with self.lock:
            if key not in self._views:
                self._views[key] = self._make_view(drawing, page)
            return self._views[key]

    def _make_view(self, drawing: str, page: int) -> View:
        import pymupdf
        from PIL import Image

        files = self.files_of(drawing)
        pdf, dxf_rel = files.get(".pdf"), files.get(".dxf")
        raster = next((files[e] for e in RASTER if e in files), None)
        if pdf and dxf_rel:
            index = dxf_index.load_index(self.ds.abspath(dxf_rel))
            transform = overlay.fit_transform(index, self.ds.abspath(pdf))
            with pymupdf.open(self.ds.abspath(pdf)) as doc:
                rect = doc[0].rect
                if transform is None:
                    zoom = VIEW_LONG_EDGE / max(rect.width, rect.height)
                    return View("page", round(rect.width * zoom), round(rect.height * zoom), pdf, 0, len(doc),
                                note="The DXF and the PDF could not be lined up, so nothing is drawn on the plan.",
                                clip=(0, 0, rect.width, rect.height), zoom=zoom, dxf_file=dxf_rel)
                s, tx, ty = transform["scale"], transform["tx"], transform["ty"]
                xs = [s * x + tx for room in index["rooms"] for x, _ in room["polygon"]]
                ys = [-s * y + ty for room in index["rooms"] for _, y in room["polygon"]]
                margin = 0.05 * max(max(xs) - min(xs), max(ys) - min(ys)) + 14
                clip = pymupdf.Rect(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin) & rect
            zoom = min(VIEW_LONG_EDGE / max(clip.width, clip.height), 12.0)
            return View("plan", round(clip.width * zoom), round(clip.height * zoom), pdf, 0, 1, aligned=True,
                        clip=(clip.x0, clip.y0, clip.x1, clip.y1), zoom=zoom, transform=transform,
                        dxf_file=dxf_rel)
        if pdf:
            with pymupdf.open(self.ds.abspath(pdf)) as doc:
                page = max(0, min(page, len(doc) - 1))
                rect = doc[page].rect
                zoom = min(VIEW_LONG_EDGE / max(rect.width, rect.height), 6.0)
                return View("page", round(rect.width * zoom), round(rect.height * zoom), pdf, page, len(doc),
                            clip=(0, 0, rect.width, rect.height), zoom=zoom)
        if raster:
            with Image.open(self.ds.abspath(raster)) as im:
                return View("image", im.width, im.height, raster, aligned=True)
        return View("none", note="This drawing is a model; its evidence is shown in 3D.")

    def view_png(self, drawing: str, page: int = 0) -> tuple[bytes | Path, str]:
        """The view's image: a file to send as it is, or rendered PNG bytes (cached on disk)."""
        import pymupdf

        view = self.view(drawing, page)
        if view.kind == "none":
            raise HTTPException(404, "this drawing has no 2D view")
        source = self.ds.abspath(view.source_file)
        if view.kind == "image":
            return source, "image/png" if source.suffix.lower() == ".png" else "image/jpeg"
        tag = hashlib.sha1(f"{view.source_file}|{view.page}|{view.clip}|{view.zoom:.4f}".encode()).hexdigest()[:16]
        cache = config.REPO_ROOT / "cache" / "views" / f"{tag}.png"
        if not cache.is_file():
            with pymupdf.open(source) as doc:
                pix = doc[view.page].get_pixmap(matrix=pymupdf.Matrix(view.zoom, view.zoom),
                                                clip=pymupdf.Rect(*view.clip), alpha=False)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(pix.tobytes("png"))
        return cache, "image/png"

    def thumb(self, drawing: str) -> Path | None:
        import pymupdf
        from PIL import Image

        cache = config.REPO_ROOT / "cache" / "thumbs" / f"{hashlib.sha1(drawing.encode()).hexdigest()[:16]}.png"
        if cache.is_file():
            return cache
        files = self.files_of(drawing)
        if not any(e in files for e in RASTER + (".pdf",)):
            model = self.model_of(drawing)
            plans = [f for f in self.ds.renders_of(model) if f.lower().endswith(".pdf")] if model else []
            if not plans:
                return None
            files = {".pdf": plans[len(plans) // 2]}
        raster = next((files[e] for e in RASTER if e in files), None)
        lined_up = ".dxf" in files and ".pdf" in files  # show the plan itself, not the whole sheet
        if raster and not lined_up:
            with Image.open(self.ds.abspath(raster)) as im:
                im = im.convert("RGBA")
                flat = Image.new("RGB", im.size, "white")
                flat.paste(im, mask=im.getchannel("A"))
        else:
            view = self.view(drawing) if lined_up else None
            with pymupdf.open(self.ds.abspath(files[".pdf"])) as doc:
                page = doc[0]
                clip = pymupdf.Rect(*view.clip) if view and view.clip else page.rect
                zoom = THUMB_EDGE * 2 / max(clip.width, clip.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
            flat = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        flat.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
        cache.parent.mkdir(parents=True, exist_ok=True)
        flat.save(cache)
        return cache

    # ---- plans rendered from an IFC model: model coordinates -> plan pixels

    def _model_mesh(self, model_rel: str) -> tuple:
        """(header, positions, indices, elements by GlobalId) of a model's mesh."""
        if model_rel not in self._meshes:
            header, positions, indices = mesh.unpack(mesh.ifc_mesh(self.ds.abspath(model_rel)))
            self._meshes[model_rel] = (header, positions, indices, {e["id"]: e for e in header["elements"]})
        return self._meshes[model_rel]

    def plan_fit(self, drawing: str) -> dict | None:
        """How the model lies on a plan rendered from it, found from the room numbers printed on the plan.

        Each space's number is text at a known place on the PDF and the centre of a known
        outline in the model. The storey whose spaces fit best wins; a plan that does not
        line up to within a few points gets no overlay.
        """
        if drawing in self._plan_fits:
            return self._plan_fits[drawing]
        import pymupdf
        from shapely.geometry import Polygon

        fit = None
        model, pdf = self.model_of(drawing), self.files_of(drawing).get(".pdf")
        if model and pdf and not model.endswith(f"{drawing}.ifc"):
            header, positions, indices, _ = self._model_mesh(model)
            with pymupdf.open(self.ds.abspath(pdf)) as doc:
                words = doc[0].get_text("words")
            on_plan: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for x0, y0, x1, y1, word, *_ in words:
                on_plan[word].append(((x0 + x1) / 2, (y0 + y1) / 2))
            by_storey: dict[int, list[dict]] = defaultdict(list)
            for element in header["elements"]:
                if element["cls"] == "IfcSpace" and element["storey"] is not None:
                    by_storey[element["storey"]].append(element)
            # The plan's file name ends with its storey's name; when that names one storey, only it is tried.
            stem = PurePosixPath(drawing).name.lower()
            named = [i for i, storey in enumerate(header["storeys"])
                     if _slug(storey["name"]) and stem.endswith("-" + _slug(storey["name"]))]
            if len(named) == 1:
                by_storey = {named[0]: by_storey.get(named[0], [])}
            fits = []
            for storey, spaces in by_storey.items():
                names: dict[str, list[dict]] = defaultdict(list)
                for space in spaces:
                    names[space["name"]].append(space)
                pairs = []
                for name, same in names.items():
                    if len(same) != 1 or len(on_plan.get(name, [])) != 1:
                        continue  # only numbers written once on the plan and used once on the storey
                    outline = mesh.footprint(same[0], positions, indices)
                    if len(outline) >= 3:
                        centre = Polygon(outline).centroid
                        pairs.append(((centre.x, centre.y), on_plan[name][0]))
                candidate = _fit_affine(pairs)
                if candidate:
                    fits.append({**candidate, "storey": storey})
            fits.sort(key=lambda f: -f["anchors"])
            # Two storeys with the same room numbers both fit: the plan cannot tell which it shows.
            if fits and (len(fits) == 1 or fits[1]["anchors"] < 0.8 * fits[0]["anchors"]):
                fit = fits[0]
        self._plan_fits[drawing] = fit
        return fit

    def ground(self, drawing: str) -> dict | None:
        """Where the plan image lies under the 3D model: its four corners in the mesh's coordinates.

        A Swiss plan is lined up with its DXF, an IFC plan with its model's storey, so the
        drawing itself can be the floor the 3D walls stand on.
        """
        view = self.view(drawing)
        if view.kind not in ("plan", "page") or not view.clip:
            return None
        w, h = view.width, view.height
        corners_px = [(0, 0), (w, 0), (w, h), (0, h)]

        def pdf(u: float, v: float) -> tuple[float, float]:
            return u / view.zoom + view.clip[0], v / view.zoom + view.clip[1]

        if view.kind == "plan" and view.transform and view.dxf_file:
            header, _, _ = mesh.unpack(mesh.plan_mesh(self.ds.abspath(view.dxf_file)))
            ox, oy, oz = header["offset"]
            t = view.transform
            corners = []
            for u, v in corners_px:
                X, Y = pdf(u, v)
                corners.append([round((X - t["tx"]) / t["scale"] - ox, 4),
                                round(-(Y - t["ty"]) / t["scale"] - oy, 4), round(0.0 - oz + 0.012, 4)])
            return {"corners": corners, "storey": 0}
        fit = self.plan_fit(drawing)
        if fit is None:
            return None
        header = self._model_mesh(self.model_of(drawing))[0]
        (a, d), (b, e), (c, f) = fit["coef"]  # X = a x + b y + c, Y = d x + e y + f
        det = a * e - b * d
        if abs(det) < 1e-9:
            return None
        z = header["storeys"][fit["storey"]].get("z") or 0.0
        corners = []
        for u, v in corners_px:
            X, Y = pdf(u, v)
            corners.append([round((e * (X - c) - b * (Y - f)) / det, 4),
                            round((-d * (X - c) + a * (Y - f)) / det, 4), round(z + 0.03, 4)])
        return {"corners": corners, "storey": fit["storey"]}

    # ---- drawing descriptions

    def summary(self, drawing: str) -> dict:
        files = self.files_of(drawing)
        qs = self.questions.get(drawing, [])
        engine = self.engine_of(drawing)
        return {
            "id": drawing,
            "source": source_of(drawing),
            "title": self.ds.notes.get(drawing, {}).get("title") or PurePosixPath(drawing).name,
            "formats": sorted(e.lstrip(".") for e in files),
            "questions": len({q.question for q in qs}),
            "engine": engine,
            "has_3d": engine is not None,
        }

    def rooms_layer(self, drawing: str, view: View) -> list[dict]:
        """Every room of an aligned plan, in view pixels: for hover and for showing what was left out."""
        if view.kind != "plan" or not view.dxf_file:
            return []
        index = dxf_index.load_index(self.ds.abspath(view.dxf_file))
        out = []
        for n, room in enumerate(index["rooms"]):
            if room.get("shaft"):
                continue
            out.append({"ref": f"room-{n}", "points": [view.px(x, y) for x, y in room["polygon"]],
                        "name": room["name"] or "", "number": room["number"] or "",
                        "area_m2": round(room["area"], 2), "outdoor": dxf.is_outdoor(room),
                        "unit": room.get("unit")})
        return out

    def px_per_metre(self, drawing: str, view: View) -> float | None:
        """How many pixels of the shown image make one metre, where the drawing's scale is known.

        A Swiss plan is lined up with its DXF (in metres); an IFC plan with its model. Lets the
        reader measure on the sheet. For an IFC plan the model's mesh must already be built.
        """
        if view.kind == "plan" and view.transform:
            return round(view.transform["scale"] * view.zoom, 4)
        model = self.model_of(drawing) if view.kind == "page" else None
        if model and (drawing in self._plan_fits or mesh.is_built(self.ds.abspath(model))):
            fit = self.plan_fit(drawing)
            if fit:
                (a, d), (b, e), _ = fit["coef"]
                return round(abs(a * e - b * d) ** 0.5 * view.zoom, 4)
        return None

    def detail(self, drawing: str, page: int = 0) -> dict:
        view = self.view(drawing, page)
        seen: dict[str, dict] = {}
        for q in self.questions.get(drawing, []):
            seen.setdefault(q.question, {"question": q.question, "type": q.type, "unit": q.unit})
        model = self.model_of(drawing)
        return {
            **self.summary(drawing),
            "credit": CREDITS.get(source_of(drawing), ""),
            "view": {"kind": view.kind, "width": view.width, "height": view.height, "page": view.page,
                     "pages": view.pages, "aligned": view.aligned, "note": view.note,
                     "px_per_m": self.px_per_metre(drawing, view),
                     "url": None if view.kind == "none" else f"/api/image?drawing={_quote(drawing)}&page={view.page}"},
            "rooms": self.rooms_layer(drawing, view),
            "mesh": (f"/api/mesh?drawing={_quote(drawing)}" if self.engine_of(drawing) else None),
            "mesh_kind": "ifc" if model else "plan" if self.engine_of(drawing) == "dxf" else None,
            "model_file": PurePosixPath(model).name if model else None,
            "suggested": list(seen.values()) or (
                [{"question": q, "type": "count", "unit": "count"} for q in GENERIC_QUESTIONS]
                if view.kind == "image" else []),
            "prepared": self.ds.notes.get(drawing, {}).get("cloudinary"),
            "paper_scale": (round(view.transform["paper_scale"]) if view.transform else None),
        }

    # ---- asking

    def _question_for(self, drawing: str, text: str) -> tuple[Question, Question | None]:
        """A Question to hand the engines, and the dataset's own question when the text is one of them."""
        known = [q for q in self.questions.get(drawing, []) if q.question == text]
        logged = self.logged["pointing"].keys() | self.logged["baseline"].keys()
        known.sort(key=lambda q: (q.qid not in logged, not q.path.lower().endswith(".pdf")))
        if known:
            q = known[0]
            asked = Question(qid=q.qid, path=q.path, question=q.question, answer=None, unit=None, type="",
                             source=q.source, drawing=q.drawing, group=q.group)
            return asked, q
        files = self.files_of(drawing)
        path = next((files[e] for e in (".pdf", ".ifc") + RASTER + (".dxf",) if e in files), None)
        if path is None:
            raise HTTPException(404, "this drawing has no file to ask about")
        return Question(qid=question_id(path, text), path=path, question=text, answer=None, unit=None, type="",
                        source=source_of(path), drawing=drawing_of(path), group=self.ds.group_of(path)), None

    def _model_fallback(self):
        """A vision model for drawings no exact engine covers. Costs money per question."""
        from dotenv import load_dotenv

        from . import pointing
        from .vlm import make_vlm

        load_dotenv(config.REPO_ROOT / ".env")
        if os.environ.get("GEMINI_API_KEY"):
            vlm = make_vlm("gemini")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            vlm = make_vlm("anthropic", "claude-sonnet-5-5")
        else:
            raise HTTPException(503, "no vision-model key is configured on the server")
        return pointing.make_engine(vlm)

    def upload(self, name: str, data: bytes) -> dict:
        """Take in a drawing someone brings: an image or a PDF. Returns its summary."""
        import pymupdf
        from PIL import Image

        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "a drawing can be at most 25 MB")
        ident = hashlib.sha1(data).hexdigest()[:12]
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        image_path = UPLOADS_DIR / f"{ident}.png"
        try:
            if data[:5] == b"%PDF-":
                with pymupdf.open(stream=data, filetype="pdf") as doc:
                    page = doc[0]
                    zoom = min(3000 / max(page.rect.width, page.rect.height), 6.0)
                    picture = Image.open(io.BytesIO(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")))
            else:
                picture = Image.open(io.BytesIO(data))
                picture.load()
            if picture.mode in ("RGBA", "LA", "P"):
                picture = picture.convert("RGBA")
                flat = Image.new("RGB", picture.size, "white")
                flat.paste(picture, mask=picture.getchannel("A"))
                picture = flat
            picture = picture.convert("RGB")
        except Exception:
            raise HTTPException(415, "this file is not an image or a PDF the app can read") from None
        if max(picture.size) > 6000:
            picture.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
        picture.save(image_path)
        note: dict = {"title": PurePosixPath(name or "drawing").stem[:80] or "drawing",
                      "size": list(picture.size), "cloudinary": None}
        if cloud.configured():  # normalise, even out and, for a small scan, enlarge it before it is read
            try:
                prepared, info = cloud.prepare(image_path, ident, *picture.size)
                with Image.open(io.BytesIO(prepared)) as better:
                    better.convert("RGB").save(image_path)
                    info["size"] = list(better.size)
                note["cloudinary"] = info
            except Exception as exc:  # the drawing still opens, as it came
                note["cloudinary"] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        (UPLOADS_DIR / f"{ident}.json").write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
        drawing = self.ds.add(image_path, note)
        for key in [k for k in self._views if k[0] == drawing]:
            del self._views[key]
        return self.summary(drawing)

    def share(self, req: AskRequest) -> dict:
        """An evidence sheet for one answer: the drawing with its evidence marked, hosted to share."""
        answer = self.ask(AskRequest(drawing=req.drawing, question=req.question, use_model=False))
        view = self.view(req.drawing)
        if view.kind == "none":
            raise HTTPException(409, "this drawing has no 2D sheet to mark; its evidence is in 3D")
        source, _ = self.view_png(req.drawing)
        unit = {"m2": " m²", "m3": " m³", "m": " m"}.get(answer["unit"] or "", "")
        value = answer["answer"]
        text = "No number" if value is None else (f"{value:,.2f}{unit}" if isinstance(value, float) else f"{value}{unit}")
        ident = hashlib.sha1(f"{req.drawing}|{answer['question']}|{value}".encode()).hexdigest()[:14]
        out = config.REPO_ROOT / "cache" / "shared" / f"{ident}.png"
        why = answer["verified_because"]
        lines = [answer["explanation"] or answer["reason"]]
        if why:
            lines.append(f"Verified: {why['calibration_errors']} wrong in {why['calibration_answers']:,} held-out answers of "
                         f"this kind (error rate below {100 * why['error_upper_bound']:.2f}%).")
        lines.append(f"{self.summary(req.drawing)['title']} · {answer['engine']} · {CREDITS.get(source_of(req.drawing), '')}")
        sheet.render(Path(source), answer["evidence"], question=answer["question"], answer_text=text,
                     state=answer["state"], lines=lines, out_path=out)
        if cloud.configured():
            try:
                hosted = cloud.host(out, ident, caption=f"{text} · {answer['question']}")
                return {"url": hosted["url"], "hosted": "cloudinary"}
            except Exception as exc:
                return {"url": f"/api/shared/{ident}.png", "hosted": "local", "note": f"{type(exc).__name__}: {str(exc)[:120]}"}
        return {"url": f"/api/shared/{ident}.png", "hosted": "local"}

    def _text_model(self):
        """A language model for restating questions: Gemini when its key is set, else Claude Sonnet."""
        from dotenv import load_dotenv

        from .vlm import make_vlm

        load_dotenv(config.REPO_ROOT / ".env")
        if os.environ.get("GEMINI_API_KEY"):
            return make_vlm("gemini", "gemini-4-hydrogen", "low")
        if os.environ.get("ANTHROPIC_API_KEY"):
            return make_vlm("anthropic", "claude-sonnet-5-5", "low")
        return None

    def _restate(self, asked: Question, drawing: str, engine: str) -> tuple[Answer | None, dict | None]:
        """Have a language model restate a question the engine could not read, then ask the engine again.

        Returns the engine's answer to the restated question (never Verified) and a note for the UI.
        """
        if self.spent_eur >= self.budget_eur and (drawing, asked.question) not in self._restated:
            return None, None
        try:
            llm = self._text_model()
        except FatalEngineError:
            llm = None
        if llm is None:
            return None, None
        own = [q.question for q in self.questions.get(drawing, [])]
        if not own:  # an IFC model whose plans carry the questions: borrow theirs
            model = self.model_of(drawing)
            own = [q.question for d, qs in self.questions.items() if model and self.model_of(d) == model for q in qs]
        english = [q for q in dict.fromkeys(own) if q[:4] in ("How ", "What", "Whic")]
        examples = (english or list(dict.fromkeys(own)))[:interpret.MAX_EXAMPLES]
        try:
            if engine == "dxf":
                names = interpret.names_on_dxf(dxf_index.load_index(self.ds.abspath(self.files_of(drawing)[".dxf"])))
            else:
                names = interpret.names_in_ifc(ifc_index.load_index(self.ds.abspath(self.model_of(drawing))))
            key = (drawing, asked.question)
            fresh = key not in self._restated
            if fresh:
                self._restated[key] = interpret.restate(asked.question, examples, names, llm)
            restated = self._restated[key]
        except FatalEngineError:
            return None, None
        if fresh:
            self.spent_eur += config.usd_to_eur(restated.cost_usd)
        elif restated.error is None:  # shown again: already paid for
            restated = interpret.Restated(restated.question, restated.reason, restated.model, 0.0, 0.0)
        note = {"question": restated.question, "reason": restated.reason, "model": restated.model,
                "cost_eur": round(config.usd_to_eur(restated.cost_usd), 6), "latency_ms": round(restated.latency_ms, 1)}
        if restated.question is None:
            return None, note
        again = Question(qid=question_id(asked.path, restated.question), path=asked.path, question=restated.question,
                         answer=None, unit=None, type="", source=asked.source, drawing=asked.drawing, group=asked.group)
        ans = router.make(self.thresholds)(again, self.ds)
        if ans.answer is None:
            note["reason"] = f"restated, but the engine still declined: {ans.reason}"
            return None, note
        if ans.state == VERIFIED:  # the answer rests on a model's reading of the question
            ans.state = CHECK
            ans.meta = {k: v for k, v in ans.meta.items() if k != "verified_because"}
        ans.cost_usd = (ans.cost_usd or 0.0) + restated.cost_usd
        return ans, note

    def ask(self, req: AskRequest) -> dict:
        drawing, text = req.drawing, " ".join(req.question.split())
        if not text:
            raise HTTPException(400, "empty question")
        asked, known = self._question_for(drawing, text)
        t0 = time.perf_counter()
        replayed = None
        ans = router.make(self.thresholds)(asked, self.ds)
        engine = router.route(asked, self.ds)
        exact = engine in ("ifc", "dxf")
        restated = None
        if exact and ans.answer is None:  # the rules could not read it: let a language model restate it
            again, restated = self._restate(asked, drawing, engine)
            if again is not None:
                ans = again
                known = next((q for q in self.questions.get(drawing, []) if q.question == restated["question"]), known)
            elif restated is not None:  # the attempt was paid for even though it led nowhere
                ans.cost_usd = (ans.cost_usd or 0.0) + restated["cost_eur"] * config.USD_PER_EUR
        # Scans: no exact engine. A weak or missing label reading gives way to a vision model.
        if not exact and (ans.answer is None or router.is_weak(ans)):
            labels = ans if ans.method == raster_labels.METHOD and ans.answer is not None else None
            logged = self.logged["pointing"].get(asked.qid)
            if logged and logged.get("pred") is not None:  # already paid for: show it again, free
                ans = _answer_from_row(logged)
                replayed = logged["run_id"]
            elif req.use_model:
                if self.spent_eur >= self.budget_eur:
                    raise HTTPException(402, f"the demo's model budget of EUR {self.budget_eur:.2f} is used up")
                try:
                    ans = self._model_fallback()(asked, self.ds)
                except FatalEngineError as exc:
                    raise HTTPException(503, f"cannot ask the vision model: {exc}")
                self.spent_eur += config.usd_to_eur(ans.cost_usd or 0.0)
            if labels is not None and ans is not labels and ans.answer is not None:
                _second_opinion(ans, labels)
        latency = ans.latency_ms if ans.latency_ms else (time.perf_counter() - t0) * 1000
        view = self.view(drawing)
        out = {
            "question": text,
            "state": ans.state,
            "answer": ans.answer,
            "unit": ans.unit,
            "explanation": ans.explanation,
            "reason": ans.reason,
            "method": ans.method,
            "engine": ENGINE_LABELS.get(ans.method, "Vision model, pointing" if "pointing" in ans.method
                                        else "Vision model" if ans.method.startswith("vlm") else ans.method),
            "confidence": ans.confidence,
            "cost_eur": round(config.usd_to_eur(ans.cost_usd or 0.0), 6),
            "latency_ms": round(latency, 1),
            "replayed_from": replayed,
            "interpreted": ans.meta.get("interpreted"),
            "restated": restated,
            "checks": _checks(ans),
            "verified_because": ans.meta.get("verified_because"),
            "family": self._family_stats(ans),
            "evidence": self._evidence(ans, view, drawing),
            "can_use_model": not exact and not ans.method.startswith("vlm")
                             and (ans.answer is None or router.is_weak(ans)),
            "reference": None,
            "baseline": None,
        }
        if known is not None:  # the dataset's answer, for comparison only
            if ans.answer is not None:
                score = score_answer(ans.answer, known.answer, known.type, gold_unit=known.unit, pred_unit=ans.unit)
                status = score.status
            else:
                status = "abstained"
            out["reference"] = {"answer": known.answer, "unit": known.unit, "type": known.type, "status": status}
            base = self.logged["baseline"].get(known.qid)
            if base is None:  # the same question may have been logged under a sibling format
                for q in self.questions.get(drawing, []):
                    if q.question == text and q.qid in self.logged["baseline"]:
                        base = self.logged["baseline"][q.qid]
                        break
            if base is not None:
                out["baseline"] = {"answer": base.get("pred"), "status": base.get("status"),
                                   "cost_eur": base.get("cost_eur"), "latency_ms": base.get("latency_ms"),
                                   "model": (base.get("meta") or {}).get("model"), "run_id": base["run_id"]}
        return out

    def _family_stats(self, ans: Answer) -> dict | None:
        """What calibration measured for this kind of answer, verified or not."""
        kind = {"count": "count", "m2": "area", "m3": "volume", "m": "length"}.get(ans.unit or "")
        fam = self.thresholds.get("families", {}).get(verify.family_of(ans.method, kind)) if kind else None
        if not fam:
            return None
        return {"family": verify.family_of(ans.method, kind), "threshold": fam.get("threshold"),
                "alpha": self.thresholds.get("alpha"), "levels": fam.get("levels_tested", []),
                "calibration_answers": fam.get("calibration_answers")}

    def _on_plan(self, drawing: str, view: View, element_id: str) -> dict:
        """Where a model element lies on the plan it is asked about: a pin, or a room outline."""
        fit = self.plan_fit(drawing) if view.kind == "page" else None
        if fit is None:
            return {}
        header, positions, indices, by_id = self._model_mesh(self.model_of(drawing))
        element = by_id.get(element_id)
        if element is None or element["storey"] != fit["storey"]:
            return {}  # another storey would land on this plan in the wrong place
        (a, d), (b, e), (c, f) = fit["coef"]

        def px(x: float, y: float) -> list[float]:
            return [round((a * x + b * y + c - view.clip[0]) * view.zoom, 1),
                    round((d * x + e * y + f - view.clip[1]) * view.zoom, 1)]

        if element["cls"] == "IfcSpace":
            outline = mesh.footprint(element, positions, indices)
            return {"points": [px(x, y) for x, y in outline]} if len(outline) >= 3 else {}
        x, y = px(element["c"][0], element["c"][1])
        return {"x": x, "y": y}

    def _evidence(self, ans: Answer, view: View, drawing: str = "") -> list[dict]:
        index = None
        if view.dxf_file and any(e.get("space") == "dxf_m" for e in ans.evidence):
            index = dxf_index.load_index(self.ds.abspath(view.dxf_file))
        out, n = [], 0
        for item in ans.evidence:
            kind, space = item.get("kind"), item.get("space")
            if kind == "formula":
                out.append({"kind": "formula", "text": item.get("text", "")})
            elif kind == "ifc_element":
                n += 1
                out.append({"kind": "element", "n": n, "id": item["id"], "cls": item.get("class", ""),
                            "label": item.get("name") or item.get("label") or item.get("class", ""),
                            **{k: item[k] for k in ("area_m2", "volume_m3", "length_m", "storey") if k in item},
                            **self._on_plan(drawing, view, item["id"])})
            elif space == "dxf_m" and kind == "polygon":
                n += 1
                ref = _room_ref(index, item["points"])
                entry = {"kind": "polygon", "n": n, "ref": ref, "label": item.get("label", ""),
                         "area_m2": item.get("area_m2")}
                if view.kind == "plan":
                    entry["points"] = [view.px(x, y) for x, y in item["points"]]
                out.append(entry)
            elif space == "dxf_m" and kind == "box":
                n += 1
                entry = {"kind": "box", "n": n, "ref": _box_ref(index, item["bbox"]), "label": item.get("label", "")}
                if view.kind == "plan":
                    x0, y0, x1, y1 = item["bbox"]
                    (ax, ay), (bx, by) = view.px(x0, y1), view.px(x1, y0)
                    entry["bbox"] = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
                out.append(entry)
            elif space == "image_px" and kind == "point":
                n += 1
                out.append({"kind": "point", "n": n, "x": item["x"], "y": item["y"], "label": item.get("label", "")})
            elif space == "image_px" and kind == "box" and item.get("role"):
                # Read on the plan and used as a check, not a counted item (the apartment's type line).
                out.append({"kind": "context", "role": item["role"], "bbox": item["bbox"],
                            "label": item.get("label", "")})
            elif space == "image_px" and kind == "box":
                n += 1
                out.append({"kind": "box", "n": n, "bbox": item["bbox"], "label": item.get("label", ""),
                            "score": item.get("score")})
        for item in ans.meta.get("doubtful") or []:  # labels the engine looked at and did not count
            out.append({"kind": "context", "role": "not counted", "bbox": item["bbox"],
                        "label": item.get("label", ""), "why": item.get("why", "")})
        return out

    @staticmethod
    def _labels_share(runs: list[dict]) -> dict | None:
        """From the latest full dev run of the router (no fallback): what the label engine covers on scans."""
        full = [r for r in runs if r["method"] == router.METHOD and r["split"] == "dev" and r["sample"].startswith("all-dev")]
        # The full dataset's run before the preview's, the newest first.
        for run in sorted(full, key=lambda r: (not r["dataset"].endswith("preview"), r["timestamp"]), reverse=True):
            share = _labels_share_of(run["run_id"])
            if share and share["answered"]:
                return share
        return None

    # ---- numbers for the pitch, straight from the run log

    def stats(self) -> dict:
        board = config.RESULTS_DIR / "leaderboard.csv"
        rows = []
        if board.is_file():
            with open(board, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        latest: dict[tuple, str] = {}
        for r in rows:  # the file is append-only: the last run of each kind wins
            latest[(r["method"], r["dataset"], r["split"], r["sample"])] = r["run_id"]
        keep = set(latest.values())
        number = ("n", "answered", "coverage", "correct", "wrong", "ungraded", "accuracy", "accuracy_when_answered",
                  "verified", "verified_share", "verified_wrong", "verified_error_upper95", "check", "declined",
                  "eur_per_question", "eur_total", "latency_p50_ms", "latency_p95_ms")
        runs: dict[str, dict] = {}
        for r in rows:
            if r["run_id"] not in keep:
                continue
            run = runs.setdefault(r["run_id"], {
                "run_id": r["run_id"], "method": r["method"], "dataset": r["dataset"], "split": r["split"],
                "sample": r["sample"], "commit": r["commit"], "timestamp": r["timestamp_utc"],
                "complete": r["complete"] == "True", "notes": r["notes"], "rows": []})
            run["rows"].append({"scope": r["scope"], "source": r["source"], "type": r["type"],
                                **{k: _num(r[k]) for k in number}})
        return {"runs": list(runs.values()), "thresholds": self.thresholds,
                "labels_on_scans": self._labels_share(list(runs.values())),
                "full_set_mix": [{"source": s, "type": t, "questions": n}
                                 for (s, t), n in sorted(config.FULL_SET_MIX.items())],
                "model_budget_eur": self.budget_eur, "model_spent_eur": round(self.spent_eur, 4)}


def _second_opinion(ans: Answer, labels: Answer) -> None:
    """Put the label engine's reading next to a vision model's answer: two methods on one plan.

    The model's items stay the numbered evidence; the labels the OCR counted are added as
    boxes that are drawn but not counted, with a check line saying whether the two agree.
    """
    agree = labels.answer == ans.answer
    for item in labels.evidence:
        if item.get("kind") == "box" and item.get("space") == "image_px" and not item.get("role"):
            ans.evidence.append({**item, "role": "label read by OCR"})
    ans.meta = {**ans.meta, "labels_said": labels.answer, "labels_score": labels.confidence,
                "checks": {**(ans.meta.get("checks") or {}),
                           "labels printed on the plan (local OCR)":
                               f"{labels.answer}: {'the same count' if agree else 'a different count'}"}}


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _checks(ans: Answer) -> list[dict]:
    """The independent checks behind an answer, as name and value."""
    checks = [{"name": k, "value": v} for k, v in (ans.meta.get("checks") or {}).items()]
    if ans.method == raster_labels.METHOD and ans.answer is not None:
        source = ans.meta.get("source")
        if source in ("labels", "labels+type"):
            checks.append({"name": "room labels of this kind read on the plan", "value": ans.answer})
        if source in ("type", "labels+type"):
            checks.append({"name": "the apartment's type line on the plan",
                           "value": "states the same" if source == "labels+type" else "the only source"})
        if ans.meta.get("known_labels") is not None:
            checks.append({"name": "labels read on the plan in all", "value": ans.meta["known_labels"]})
    return checks


def _fit_affine(pairs: list) -> dict | None:
    """Least-squares map from model x, y to plan points, dropping anchors that do not agree."""
    import numpy as np

    if len(pairs) < 4:
        return None
    src = np.array([[x, y, 1.0] for (x, y), _ in pairs])
    dst = np.array([target for _, target in pairs], dtype=float)
    keep = np.ones(len(pairs), dtype=bool)
    residual = np.zeros(len(pairs))
    for _ in range(4):
        if keep.sum() < 4:
            return None
        coef, *_ = np.linalg.lstsq(src[keep], dst[keep], rcond=None)
        residual = np.linalg.norm(src @ coef - dst, axis=1)
        agree = residual <= max(3 * float(np.median(residual[keep])), 4.0)
        if (agree == keep).all():
            break
        keep = agree
    if keep.sum() < 4 or keep.sum() < 0.6 * len(pairs) or residual[keep].max() > 15.0:
        return None
    return {"coef": coef.tolist(), "anchors": int(keep.sum()), "residual_pt": round(float(residual[keep].max()), 2)}


@lru_cache(maxsize=8)
def _labels_share_of(run_id: str) -> dict | None:
    """In a logged router run: the share of scan questions the label engine answered on its own."""
    preds = config.RESULTS_DIR / "runs" / run_id / "predictions.jsonl"
    if not preds.is_file():
        return None
    scans = stood = right = 0
    with open(preds, encoding="utf-8") as f:
        for line in f:
            if '"cubicasa5k"' not in line:
                continue
            row = json.loads(line)
            scans += 1
            if (row.get("method") == raster_labels.METHOD and row.get("pred") is not None
                    and (row.get("confidence") or 0.0) >= router.LABELS_STAND_FROM):
                stood += 1
                right += row.get("status") == "correct"
    if not scans:
        return None
    return {"run_id": run_id, "scan_questions": scans, "answered": stood, "correct": right,
            "share": stood / scans}


def _num(text: str):
    if text in ("", None):
        return None
    try:
        value = float(text)
    except ValueError:
        return text
    return int(value) if value.is_integer() and "." not in text else value


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def _answer_from_row(row: dict) -> Answer:
    """A logged prediction turned back into an Answer (state and evidence as logged)."""
    return Answer(answer=row.get("pred"), unit=row.get("pred_unit"), state=row.get("state") or "check",
                  confidence=row.get("confidence"), method=row.get("method", ""),
                  evidence=row.get("evidence") or [], explanation=row.get("explanation", ""),
                  cost_usd=row.get("cost_usd"), latency_ms=row.get("latency_ms"), meta={})


def _room_ref(index: dict | None, points: list) -> str | None:
    if index is None:
        return None
    for n, room in enumerate(index["rooms"]):
        if room["polygon"] == points:
            return f"room-{n}"
    return None


def _box_ref(index: dict | None, bbox: list) -> str | None:
    if index is None:
        return None
    for kind in ("doors", "windows"):
        for n, item in enumerate(index[kind]):
            if item["bbox"] == bbox:
                return f"{kind[:-1]}-{n}"
    for n, item in enumerate(index["fixtures"]):
        if item["bbox"] == bbox:
            return f"fixture-{n}"
    return None


# ----------------------------------------------------------------- app


def create_app(dataset_root: Path | str | None = None) -> FastAPI:
    app = FastAPI(title="Make Blueprints Talk", docs_url="/api/docs", openapi_url="/api/openapi.json")
    service = Service(dataset_root)
    app.state.service = service

    def need(drawing: str) -> str:
        if not service.allowed(drawing):
            raise HTTPException(404, "unknown drawing, or not part of the dev split")
        return drawing

    @lru_cache(maxsize=1)
    def dataset_catalogue() -> dict[str, list[str]]:
        by_source: dict[str, list[str]] = defaultdict(list)
        for drawing in sorted(service.questions):
            by_source[source_of(drawing)].append(drawing)
        for drawing in service.archive:
            by_source[source_of(drawing)].append(drawing)
        return dict(by_source)

    def catalogue() -> dict[str, list[str]]:
        brought = sorted(service.ds.notes, key=lambda d: service.ds.notes[d].get("title", ""))
        return {**({"uploads": brought} if brought else {}), **dataset_catalogue()}

    @app.get("/api/sources")
    def sources() -> list[dict]:
        order = ("uploads", "swiss_dwellings", "ifc_models", "cubicasa5k", "opening_design", "ifc_bench") + ARCHIVE_SOURCES
        found = catalogue()
        return [{"source": s, "drawings": len(found[s]), "credit": CREDITS.get(s, ""),
                 "archive": s in ARCHIVE_SOURCES or s == "uploads"}
                for s in sorted(found, key=lambda s: order.index(s) if s in order else len(order))]

    @app.get("/api/drawings")
    def drawings(source: str, q: str = "", offset: int = 0, limit: int = 48) -> dict:
        items = catalogue().get(source, [])
        if q:
            needle = q.lower()
            items = [d for d in items if needle in d.lower()]
        page = items[offset: offset + max(1, min(limit, 200))]
        return {"total": len(items), "items": [service.summary(d) for d in page]}

    @app.get("/api/drawing")
    def drawing_detail(drawing: str, page: int = 0) -> dict:
        return service.detail(need(drawing), page)

    @app.get("/api/image")
    def image(drawing: str, page: int = 0):
        source, media = service.view_png(need(drawing), page)
        return FileResponse(source, media_type=media, headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/thumb")
    def thumb(drawing: str):
        path = service.thumb(need(drawing))
        if path is None:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/mesh")
    def mesh_of(drawing: str):
        need(drawing)
        model = service.model_of(drawing)
        try:
            if model:
                data = mesh.ifc_mesh(service.ds.abspath(model))
            elif ".dxf" in service.files_of(drawing):
                data = mesh.plan_mesh(service.ds.abspath(service.files_of(drawing)[".dxf"]))
            else:
                raise HTTPException(404, "no 3D view for this drawing")
        except mesh.MeshTooLarge as exc:
            raise HTTPException(413, f"this model is too large to show in the browser ({exc})")
        return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/ground")
    def ground(drawing: str) -> dict:
        placed = service.ground(need(drawing))
        if placed is None:
            raise HTTPException(404, "this plan is not lined up with a 3D model")
        return placed

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        need(req.drawing)
        return service.ask(req)

    @app.post("/api/upload")
    async def upload(request: Request, name: str = "drawing") -> dict:
        return service.upload(name, await request.body())

    @app.post("/api/share")
    def share(req: AskRequest) -> dict:
        need(req.drawing)
        return service.share(req)

    @app.get("/api/shared/{name}")
    def shared(name: str):
        path = config.REPO_ROOT / "cache" / "shared" / PurePosixPath(name).name
        if path.suffix != ".png" or not path.is_file():
            raise HTTPException(404, "no such sheet")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/speak")
    def speak_aloud(req: SpeakRequest):
        return Response(speak(" ".join(req.text.split())), media_type="audio/mpeg")

    @app.get("/api/partners")
    def partners() -> dict:
        """Which partner services this server is set up to use (no secrets)."""
        from dotenv import load_dotenv

        load_dotenv(config.REPO_ROOT / ".env")
        return {"gemini": bool(os.environ.get("GEMINI_API_KEY")), "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "cloudinary": cloud.configured(), "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY"))}

    @app.get("/api/stats")
    def stats() -> dict:
        return service.stats()

    dist = config.REPO_ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def serve(host: str = "127.0.0.1", port: int = 8000, dataset_root: Path | str | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(dataset_root), host=host, port=port, log_level="info")
