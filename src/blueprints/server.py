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

from . import cloud, config, interpret, ledger, mesh, model_query, overlay, plan_query, progress, scan_plan, sheet, verify
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
# Sheets that come with the app as examples: taken in at start like a drawing someone brings.
EXAMPLES_DIR = config.REPO_ROOT / "examples"
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
    router.AGREED: "Printed labels + vision model",
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


# How long one request waits for a model answer before saying "still working". Well under the
# 100 seconds after which a proxy such as Cloudflare gives up on a request.
WAIT_FOR_MODEL_S = float(os.environ.get("BLUEPRINTS_WAIT_S", "50"))


class StillWorking(Exception):
    """The answer is being worked on; ask again and it will be there, or nearer."""


class AskRequest(BaseModel):
    drawing: str
    question: str
    use_model: bool = False  # ask the vision model as well, even where another reading already answers
    auto: bool = False       # ask the vision model when nothing else can answer (what the app does)


MAX_SPOKEN_CHARS = 400


class SpeakRequest(BaseModel):
    text: str


def speak(text: str) -> bytes:
    """The answer read aloud by ElevenLabs, as MP3. Needs ELEVENLABS_API_KEY."""
    import urllib.request

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or os.environ.get("BLUEPRINTS_OFFLINE"):
        raise HTTPException(503, "no voice service is configured")
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    model = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    text = text[:400]
    # A line is made once and kept: the same sentence costs nothing the second time.
    kept = config.REPO_ROOT / "cache" / "speech" / f"{hashlib.sha1(f'{voice}|{model}|{text}'.encode()).hexdigest()[:20]}.mp3"
    if kept.is_file():
        return kept.read_bytes()
    body = json.dumps({"text": text, "model_id": model}).encode()
    request = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128", data=body,
        headers={"xi-api-key": key, "content-type": "application/json", "accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            audio = response.read()
    except Exception as exc:
        raise HTTPException(502, f"the voice service did not answer: {type(exc).__name__}") from None
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(audio)
    return audio


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
        self._enhanced: dict[str, dict] = {}  # drawing -> its enlarged view on Cloudinary
        self.logged = self._load_logged_runs()
        self._flights: dict[str, dict] = {}  # model answers being worked on right now, by question
        self.examples = self._take_in_examples()

    def _take_in_examples(self) -> list[str]:
        """The example sheets (examples/*/sheets.json), each taken in once; returns their drawing ids."""
        out = []
        for listing in sorted(EXAMPLES_DIR.glob("*/sheets.json")):
            for entry in json.loads(listing.read_text(encoding="utf-8")):
                file = listing.parent / entry["file"]
                if not file.is_file():
                    continue
                data = file.read_bytes()
                drawing = drawing_of(f"uploads/{hashlib.sha1(data).hexdigest()[:12]}.png")
                extra = {"title": entry["title"], "credit": entry.get("credit", ""),
                         "questions": entry.get("questions", []), "example": True}
                if drawing not in self.ds.notes:
                    self.upload(entry["file"], data)
                note_file = UPLOADS_DIR / f"{PurePosixPath(drawing).name}.json"
                note = {**self.ds.notes[drawing], **extra}
                if note != self.ds.notes[drawing]:
                    self.ds.notes[drawing] = note
                    note_file.write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
                out.append(drawing)
        return out

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

    def scan_image(self, drawing: str) -> Path | None:
        """The picture of a drawing that is only a picture: no model, no DXF, no PDF behind it."""
        files = self.files_of(drawing)
        if ".pdf" in files or self.engine_of(drawing):
            return None
        raster = next((files[e] for e in RASTER if e in files), None)
        return self.ds.abspath(raster) if raster else None

    def scan_3d(self, drawing: str, known_only: bool = False) -> bool:
        """Whether a scan's walls were found in its pixels well enough to stand it up in 3D (scan_plan.py).

        `known_only` answers from what was already worked out, for listings: it costs one `stat`.
        """
        path = self.scan_image(drawing)
        if path is None:
            return False
        try:
            if known_only:
                return bool(scan_plan.verdict_if_known(path))
            return bool(scan_plan.load(path).get("ok"))
        except Exception:  # an image that cannot be read has no 3D view; the plan view says why
            return False

    # ---- logged runs (answers already paid for)

    def _load_logged_runs(self) -> dict[str, dict[str, dict]]:
        """Latest logged answer per question for the image-only baseline and the pointing run."""
        out: dict[str, dict[str, dict]] = {"baseline": {}, "pointing": {}}
        runs = config.RESULTS_DIR / "runs"
        if not runs.is_dir():
            return out
        for run in sorted(runs.iterdir()):
            # A router run that replayed a model run carries the model's name too; it is not a model run.
            kind = (None if "-router-" in run.name else "baseline" if "-vlm-image-only-" in run.name
                    else "pointing" if "-vlm-pointing-" in run.name or "-sheet-working-" in run.name else None)
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
        if view.kind == "image":
            # A scan stood up from its own pixels: the scan lies under its walls, pixel for pixel.
            path = self.scan_image(drawing)
            if path is None or not self.scan_3d(drawing):
                return None
            header, _, _ = mesh.unpack(mesh.scan_mesh(path))
            unit, (ox, oy, oz) = header["scan"]["unit_per_px"], header["offset"]
            wide, high = header["scan"]["width"], header["scan"]["height"]
            x0, y0, x1, y1 = header["scan"].get("crop") or [0, 0, wide, high]  # the part of the page the building stands on
            return {"corners": [[round(u * unit - ox, 4), round((high - v) * unit - oy, 4), round(0.012 - oz, 4)]
                                for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))], "storey": 0,
                    "crop": [x0, y0, x1, y1]}
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
            "has_3d": engine is not None or self.scan_3d(drawing, known_only=True),
            "example": bool(self.ds.notes.get(drawing, {}).get("example")),
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
        scan3d = self.scan_3d(drawing)  # before the summary, which only says what is already known
        return {
            **self.summary(drawing),
            "credit": self.ds.notes.get(drawing, {}).get("credit") or CREDITS.get(source_of(drawing), ""),
            "view": {"kind": view.kind, "width": view.width, "height": view.height, "page": view.page,
                     "pages": view.pages, "aligned": view.aligned, "note": view.note,
                     "px_per_m": self.px_per_metre(drawing, view),
                     "url": None if view.kind == "none" else f"/api/image?drawing={_quote(drawing)}&page={view.page}"},
            "rooms": self.rooms_layer(drawing, view),
            "mesh": (f"/api/mesh?drawing={_quote(drawing)}" if self.engine_of(drawing) or scan3d else None),
            "mesh_kind": "ifc" if model else "plan" if self.engine_of(drawing) == "dxf" else "scan" if scan3d else None,
            "model_file": PurePosixPath(model).name if model else None,
            "suggested": list(seen.values()) or (
                [{"question": q, "type": "", "unit": None} for q in self.ds.notes.get(drawing, {}).get("questions", [])]
                or ([{"question": q, "type": "count", "unit": "count"} for q in GENERIC_QUESTIONS]
                    if view.kind == "image" else [])),
            "can_enhance": self.can_enhance(view),
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

        from . import model_path
        from .vlm import make_vlm

        load_dotenv(config.REPO_ROOT / ".env")
        if os.environ.get("GEMINI_API_KEY"):
            # Low thinking: in a pilot, high thinking cost about EUR 0.10 and three minutes per question
            # and often ran out of tokens; low costs about EUR 0.006 and answers in under ten seconds.
            vlm = make_vlm("gemini", "gemini-4-carbon", "low")
        elif os.environ.get("ANTHROPIC_API_KEY"):
            vlm = make_vlm("anthropic", "claude-sonnet-5-5")
        else:
            raise HTTPException(503, "no vision-model key is configured on the server")
        engine = model_path.make(vlm)

        def work(q: Question, dataset, job: dict) -> None:
            try:
                ans = engine(q, dataset)
                if ans.error is None and ans.answer is not None:  # an answer is kept; a failure is tried again
                    store = _kept_file(q)
                    store.parent.mkdir(parents=True, exist_ok=True)
                    store.write_text(json.dumps({"answer": ans.answer, "unit": ans.unit, "state": ans.state,
                                                 "confidence": ans.confidence, "method": ans.method, "evidence": ans.evidence,
                                                 "explanation": ans.explanation, "reason": ans.reason,
                                                 "latency_ms": ans.latency_ms, "meta": ans.meta}), encoding="utf-8")
                job["answer"] = ans
            except Exception as exc:  # the one waiting is told, whatever went wrong
                job["failed"] = exc
            finally:
                job["finished"] = time.time()
                job["done"].set()

        def remembered(q: Question, dataset) -> Answer:
            """A model answer is paid for once, and worked on once however many times it is asked for.

            A long answer (a hundred doors boxed tile by tile) takes longer than a proxy in front of the
            app lets one request live. So the work runs on its own; a request waits for it a while, and if
            it is not done says so (StillWorking). The page asks again and joins the same piece of work.
            """
            key = _kept_file(q).name
            with self.lock:
                for old in [k for k, j in self._flights.items() if j.get("finished") and time.time() - j["finished"] > 300]:
                    del self._flights[old]
                job = self._flights.get(key)
                if job is None or job["paid"]:  # no work under way, or its answer was already handed over
                    kept = _kept_answer(q)
                    if kept is not None:
                        return kept
                if job is None:
                    job = self._flights[key] = {"done": threading.Event(), "answer": None, "failed": None, "paid": False}
                    threading.Thread(target=work, args=(q, dataset, job), daemon=True).start()
            if not job["done"].wait(WAIT_FOR_MODEL_S):
                raise StillWorking()
            if job["failed"] is not None:
                with self.lock:
                    self._flights.pop(key, None)
                raise job["failed"]
            ans = job["answer"]
            if job["paid"]:  # another request already took this answer and its cost into the session's total
                ans = Answer(**{**ans.__dict__, "cost_usd": 0.0})
            job["paid"] = True  # the first request to get the answer carries what it cost
            return ans

        return remembered

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
                # The PDF is kept beside its picture: its own text layer is exact (sheet_text.py).
                (UPLOADS_DIR / f"{ident}.pdf").write_bytes(data)
                with pymupdf.open(stream=data, filetype="pdf") as doc:
                    page = doc[0]
                    zoom = min(5000 / max(page.rect.width, page.rect.height), 6.0)
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
        # The drawing is read as it came. Enlarging a scan with AI before reading it was measured
        # (scripts/eval_prepared_scans.py) and made the label engine worse where it is trusted most,
        # so the enlarged copy is only ever shown to the reader (see `enhanced`).
        note: dict = {"title": PurePosixPath(name or "drawing").stem[:80] or "drawing", "size": list(picture.size)}
        (UPLOADS_DIR / f"{ident}.json").write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
        drawing = self.ds.add(image_path, note)
        for key in [k for k in self._views if k[0] == drawing]:
            del self._views[key]
        return self.summary(drawing)

    def can_enhance(self, view: View) -> bool:
        """A small scan can be shown enlarged by Cloudinary's super-resolution."""
        return view.kind == "image" and max(view.width, view.height) < cloud.SMALL_EDGE and cloud.configured()

    def enhanced(self, drawing: str) -> dict:
        """The scan enlarged for viewing. Evidence keeps its place: the picture is only sharper."""
        view = self.view(drawing)
        if not self.can_enhance(view):
            raise HTTPException(409, "this drawing has no enlarged view")
        if drawing not in self._enhanced:
            source = self.ds.abspath(view.source_file)
            ident = hashlib.sha1(source.read_bytes()).hexdigest()[:14]
            try:
                self._enhanced[drawing] = cloud.enhance(source, ident)
            except Exception as exc:
                raise HTTPException(502, f"Cloudinary could not enlarge it: {type(exc).__name__}: {str(exc)[:120]}") from None
        return self._enhanced[drawing]

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

    def _reader_model(self):
        """The language model that reads a free-form question as a query: the stronger one, since a wrong
        reading gives a wrong answer however exact the engine is. Gemini 4 Carbon when its key is set."""
        from dotenv import load_dotenv

        from .vlm import make_vlm

        load_dotenv(config.REPO_ROOT / ".env")
        if os.environ.get("GEMINI_API_KEY"):
            return make_vlm("gemini", "gemini-4-carbon", "low")
        return self._text_model()

    def _second_reader(self):
        """A different model for the second reading of a free-form question: Gemma, Google's open model,
        beside Gemini. Two unrelated models agreeing is stronger than one agreeing with itself. Measured
        on 50 questions with known readings (scripts/bench_readers.py): both read every one right when the
        call went through, and never agreed on a wrong reading. KAAVA_SECOND_READER=same turns it off."""
        choice = os.environ.get("KAAVA_SECOND_READER", "gemma-4-31b-it").strip()
        if choice.lower() in ("", "same", "off", "none") or not os.environ.get("GEMINI_API_KEY"):
            return None
        from .vlm import make_vlm

        return make_vlm("gemma", choice)

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

    def _two_readings(self, module, drawing: str, question: str, listing: str, ask) -> tuple | None:
        """Both readings of a free-form question, and whether they were paid for just now.

        A pair of readings already paid for is kept on disk (keyed by the question, the instructions and
        the list of contents the model was shown), so asking it again costs nothing and takes no time,
        after a restart too. Only the reading is kept: the count or the measurement is redone each time.
        """
        key = (drawing, "query: " + question)
        if key not in self._restated:
            kept = _kept_readings(module, drawing, question, listing)
            if kept is not None:
                self._restated[key] = kept
        if key in self._restated:
            return (*self._restated[key], False)
        if self.spent_eur >= self.budget_eur:
            return None
        llm = self._reader_model()
        if llm is None:
            return None
        pair = ask(llm)
        if not pair[0].error and not pair[1].error:  # a reading that failed is asked again next time
            self._restated[key] = pair
            _keep_readings(module, drawing, question, listing, pair)
        return (*pair, True)

    def _query_plan(self, asked: Question, drawing: str) -> tuple[Answer | None, dict | None]:
        """A question no phrasing of the engine fits, read as a query over the plan's own rooms (plan_query.py).

        The language model selects rooms from the plan's inventory and says what to measure; the engine
        measures them on the geometry. Returns the answer (never Verified) and a note for the UI.
        """
        try:
            index = dxf_index.load_index(self.ds.abspath(self.files_of(drawing)[".dxf"]))
            read = self._two_readings(plan_query, drawing, asked.question, plan_query.inventory(index, dxf.is_outdoor),
                                      lambda llm: plan_query.ask_twice(asked.question, index, llm, dxf.is_outdoor, other=self._second_reader()))
        except (FatalEngineError, KeyError):
            return None, None
        if read is None:
            return None, None
        query, second, fresh = read
        if not query.error and not second.error and not plan_query.same_reading(query, second):
            # Two readings of the question that differ: no number, and both readings are said.
            one = plan_query.in_words(query, index) if not query.cannot else f"it cannot be answered ({query.reading})"
            two = plan_query.in_words(second, index) if not second.cannot else f"it cannot be answered ({second.reading})"
            query = plan_query.Query(cannot=True, model=query.model, cost_usd=query.cost_usd, latency_ms=query.latency_ms,
                                     reading=f"The question can be read two ways: (1) {one}; (2) {two}. "
                                             f"Say which rooms you mean, by name or number.")
        cost = query.cost_usd if fresh else 0.0
        if fresh:
            self.spent_eur += config.usd_to_eur(cost)
        note = {"question": None, "reason": query.reading, "model": query.model, "twice": True,
                "readers": [query.model, second.model],  # who read it: two different models when Gemma took the second reading
                "cost_eur": round(config.usd_to_eur(cost), 6), "latency_ms": round(query.latency_ms if fresh else 0.0, 1)}
        if query.error or query.cannot:
            return None, note
        if query.measure == "count_items" and not query.rooms and query.item in plan_query.ITEM_QUESTIONS:
            again = Question(qid=question_id(asked.path, plan_query.ITEM_QUESTIONS[query.item]), path=asked.path,
                             question=plan_query.ITEM_QUESTIONS[query.item], answer=None, unit=None, type="",
                             source=asked.source, drawing=asked.drawing, group=asked.group)
            ans = dxf.answer(again, self.ds)
        else:
            ans = plan_query.run(query, index, dxf, dict(method=dxf.METHOD, cost_usd=0.0))
        if ans.answer is None:
            note["reason"] = f"{query.reading} But: {ans.reason}"
            return None, note
        note["question"] = plan_query.in_words(query, index)
        ans.cost_usd = (ans.cost_usd or 0.0) + cost
        return ans, note

    def _query_model(self, asked: Question, drawing: str) -> tuple[Answer | None, dict | None]:
        """A question no phrasing of the IFC engine fits, read as a query over the model's contents (model_query.py).

        The language model selects storeys, element kinds or rooms from the model's inventory; the engine
        counts and measures them. Returns the answer (never Verified) and a note for the UI.
        """
        try:
            model_file = self.model_of(drawing)
            index = ifc_index.load_index(self.ds.abspath(model_file))
            name = PurePosixPath(drawing).name
            read = self._two_readings(model_query, drawing, asked.question, model_query.inventory(index, name)[0],
                                      lambda llm: model_query.ask_twice(asked.question, index, name, llm, other=self._second_reader()))
        except (FatalEngineError, KeyError, TypeError):
            return None, None
        if read is None:
            return None, None
        query, second, fresh = read
        if not query.error and not second.error and not model_query.same_reading(query, second):
            one = model_query.in_words(query, index) if not query.cannot else f"it cannot be answered ({query.reading})"
            two = model_query.in_words(second, index) if not second.cannot else f"it cannot be answered ({second.reading})"
            query = model_query.Query(cannot=True, model=query.model, cost_usd=query.cost_usd, latency_ms=query.latency_ms,
                                      reading=f"The question can be read two ways: (1) {one}; (2) {two}. "
                                              f"Name the storey or the rooms you mean.")
        cost = query.cost_usd if fresh else 0.0
        if fresh:
            self.spent_eur += config.usd_to_eur(cost)
        note = {"question": None, "reason": query.reading, "model": query.model, "twice": True,
                "readers": [query.model, second.model],  # who read it: two different models when Gemma took the second reading
                "cost_eur": round(config.usd_to_eur(cost), 6), "latency_ms": round(query.latency_ms if fresh else 0.0, 1)}
        if query.error or query.cannot:
            return None, note
        ans = model_query.run(query, index, ifc._element, dict(method=ifc.METHOD, cost_usd=0.0))
        if ans.answer is None:
            note["reason"] = f"{query.reading} But: {ans.reason}"
            return None, note
        ans.meta = {**ans.meta, "ifc": model_file}
        note["question"] = model_query.in_words(query, index)
        ans.cost_usd = (ans.cost_usd or 0.0) + cost
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
        if exact and ans.answer is None:
            # The rules could not read it. A language model reads the question as a query over the plan's
            # rooms, or over the model's storeys, elements and rooms; the engine runs the query. (Restating
            # the question in one of the engine's own phrasings came first until a restatement was seen to
            # drop the room a question named: "the windows in Stanza" became the windows of the floor.)
            again, restated = (self._query_plan if engine == "dxf" else self._query_model)(asked, drawing)
            if again is None and restated is None:  # no language model to hand: the older restating step
                again, restated = self._restate(asked, drawing, engine)
            elif again is None and restated.get("reason"):  # why the drawing cannot answer it, in plain words
                ans.reason = restated["reason"]
            if again is not None:
                ans = again
                known = next((q for q in self.questions.get(drawing, []) if q.question == restated["question"]), known)
            elif restated is not None:  # the attempt was paid for even though it led nowhere
                ans.cost_usd = (ans.cost_usd or 0.0) + restated["cost_eur"] * config.USD_PER_EUR
        # Scans: no exact engine. A weak or missing label reading gives way to a vision model.
        if not exact and (ans.answer is None or router.is_weak(ans)):
            labels = ans if ans.method == raster_labels.METHOD and ans.answer is not None else None
            logged = self.logged["pointing"].get(asked.qid)
            kept = None if logged else _kept_answer(asked)
            if logged and logged.get("pred") is not None:  # already paid for: show it again, free
                ans = _answer_from_row(logged)
                replayed = logged["run_id"]
            elif kept is not None:  # asked in the app before: shown again, free
                ans, replayed = kept, "kept from when it was first asked"
            elif req.use_model or req.auto:
                if self.spent_eur >= self.budget_eur:
                    raise HTTPException(402, f"the demo's model budget of EUR {self.budget_eur:.2f} is used up")
                if progress.get(asked.qid) is None:
                    progress.say(asked.qid, "Asking the vision model")
                try:
                    ans = self._model_fallback()(asked, self.ds)
                except StillWorking:
                    raise  # the work goes on; what it is doing stays on show
                except FatalEngineError as exc:
                    progress.clear(asked.qid)
                    raise HTTPException(503, f"cannot ask the vision model: {exc}")
                except Exception:
                    progress.clear(asked.qid)
                    raise
                progress.clear(asked.qid)
                self.spent_eur += config.usd_to_eur(ans.cost_usd or 0.0)
            if labels is not None and ans is not labels and ans.answer is not None:
                _second_opinion(ans, labels)
        # Scans: a strong label reading can be put next to the vision model's own count of the same thing.
        can_confirm = (not exact and ans.method == raster_labels.METHOD and ans.answer is not None
                       and not router.is_weak(ans))
        if can_confirm and (req.use_model or router.wants_second_reading(ans)):
            # Asked for, or the label answer is of the weaker kind. A logged count costs nothing more.
            other = None
            logged = self.logged["pointing"].get(asked.qid)
            if logged and logged.get("pred") is not None:  # already paid for
                other, replayed = _answer_from_row(logged), logged["run_id"]
            elif req.use_model:
                if self.spent_eur >= self.budget_eur:
                    raise HTTPException(402, f"the demo's model budget of EUR {self.budget_eur:.2f} is used up")
                try:
                    other = self._model_fallback()(asked, self.ds)
                except FatalEngineError as exc:
                    raise HTTPException(503, f"cannot ask the vision model: {exc}")
                self.spent_eur += config.usd_to_eur(other.cost_usd or 0.0)
            if other is not None and other.answer is not None:
                ans = verify.apply(router.confirmed(ans, other), self.thresholds)
                can_confirm = False
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
            "engine": _engine_label(ans.method),
            "confidence": ans.confidence,
            "cost_eur": round(config.usd_to_eur(ans.cost_usd or 0.0), 6),
            "latency_ms": round(latency, 1),
            "replayed_from": replayed,
            "interpreted": ans.meta.get("interpreted"),
            # A measurement whose scale was not printed (taken from a standard door, say): the range it leaves open.
            "estimate": ans.meta.get("estimate"),
            # What examiners found inside the thing measured and could not take into account (examiner.py).
            "caveat": ans.meta.get("caveat"),
            "bound": ans.meta.get("bound"),
            "basis": ans.meta.get("basis"),
            # What the program and the model each did, step by step (sheet_reading.py).
            "trace": [{"title": title, "body": body} for title, body in ans.meta.get("trace") or []],
            "restated": restated,
            "checks": _checks(ans),
            "verified_because": ans.meta.get("verified_because"),
            "family": self._family_stats(ans),
            "evidence": self._evidence(ans, view, drawing),
            # Outlines that lie exactly on another: how many the file holds, how many distinct places
            # that is, and which of the two the answer counts.
            "coinciding": _coinciding(ans),
            "can_use_model": can_confirm or (not exact and not ans.method.startswith("vlm")
                                             and (ans.answer is None or router.is_weak(ans))),
            "can_confirm": can_confirm,
            "readings": ({"labels": ans.meta.get("labels_said"), "model": ans.meta.get("model_said")}
                         if ans.method == router.AGREED else None),
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
        # The log of questions asked (ledger.py): what was asked and how it ended, never who asked.
        ledger.record(drawing=drawing, source=asked.source, question=text, state=ans.state, answer=ans.answer, unit=ans.unit,
                      engine=out["engine"], method=ans.method, cost_eur=out["cost_eur"],
                      alone_eur=(out["baseline"] or {}).get("cost_eur"), latency_ms=int(latency),
                      replayed=bool(replayed) or bool(ans.meta.get("shown_again")))
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
        # Evidence on a file is in that file's own units: pixels of an image, points of a PDF page.
        unit = view.zoom if view.kind == "page" else 1.0
        first_at: dict[str, int] = {}  # the first item at each place: an outline can be in the file twice
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
            elif space == "dxf_m" and kind == "polygon" and item.get("role"):
                # Shown, not counted: a room that was searched for the items the answer counts.
                if view.kind == "plan":
                    out.append({"kind": "context", "role": item["role"], "label": item.get("label", ""),
                                "points": [view.px(x, y) for x, y in item["points"]]})
            elif space == "dxf_m" and kind == "polygon":
                n += 1
                ref = _room_ref(index, item["points"])
                entry = {"kind": "polygon", "n": n, "ref": ref, "label": item.get("label", ""),
                         "area_m2": item.get("area_m2")}
                if view.kind == "plan":
                    entry["points"] = [view.px(x, y) for x, y in item["points"]]
                    if item.get("holes"):  # what was cut out of the room, so that it is drawn cut out
                        entry["holes"] = [[view.px(x, y) for x, y in ring] for ring in item["holes"]]
                out.append(entry)
            elif space == "dxf_m" and kind == "box" and item.get("role"):
                # Shown, not counted: a door opening left out of a wall length.
                if view.kind == "plan":
                    x0, y0, x1, y1 = item["bbox"]
                    (ax, ay), (bx, by) = view.px(x0, y1), view.px(x1, y0)
                    out.append({"kind": "context", "role": item["role"], "label": item.get("label", ""),
                                "bbox": [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]})
            elif space == "dxf_m" and kind == "box":
                n += 1
                entry = {"kind": "box", "n": n, "ref": _box_ref(index, item["bbox"]), "label": item.get("label", "")}
                if item.get("times"):
                    entry["times"] = item["times"]  # this many outlines lie here; the place is counted once
                if entry["ref"]:
                    if entry["ref"] in first_at:
                        entry["same_as"] = first_at[entry["ref"]]  # lies exactly on that earlier item
                    else:
                        first_at[entry["ref"]] = n
                if view.kind == "plan":
                    x0, y0, x1, y1 = item["bbox"]
                    (ax, ay), (bx, by) = view.px(x0, y1), view.px(x1, y0)
                    entry["bbox"] = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
                out.append(entry)
            elif space == "image_px" and kind == "polygon":
                # A room measured on the sheet by its own outline, with what stands in it cut out.
                n += 1
                entry = {"kind": "polygon", "n": n, "ref": None, "label": item.get("label", ""), "area_m2": item.get("area_m2"),
                         "points": [[x * unit, y * unit] for x, y in item["points"]]}
                if item.get("holes"):
                    entry["holes"] = [[[x * unit, y * unit] for x, y in ring] for ring in item["holes"]]
                out.append(entry)
            elif space == "image_px" and kind == "point":
                n += 1
                out.append({"kind": "point", "n": n, "x": item["x"] * unit, "y": item["y"] * unit, "label": item.get("label", "")})
            elif space == "image_px" and kind == "box" and item.get("role"):
                # Read on the plan and used as a check, not a counted item (the apartment's type line).
                out.append({"kind": "context", "role": item["role"], "bbox": [v * unit for v in item["bbox"]],
                            "label": item.get("label", "")})
            elif space == "image_px" and kind == "box":
                n += 1
                out.append({"kind": "box", "n": n, "bbox": [v * unit for v in item["bbox"]], "label": item.get("label", ""),
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
                "sheets_bench": _sheets_bench(),
                "live": ledger.summary(),  # questions asked of this app, from its log (Aiven for PostgreSQL)
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
    if ans.method in (raster_labels.METHOD, router.AGREED) and ans.answer is not None:
        source = ans.meta.get("source")
        if source in ("labels", "labels+type"):
            checks.append({"name": "room labels of this kind read on the plan", "value": ans.meta.get("labels_said", ans.answer)})
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
    scans = stood = right = alone = 0
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
                alone += (row.get("confidence") or 0.0) >= router.CONFIRM_BELOW
    if not scans:
        return None
    # share: the label engine has a strong answer. share_alone: strong enough to stand without a second reading.
    return {"run_id": run_id, "scan_questions": scans, "answered": stood, "correct": right,
            "share": stood / scans, "share_alone": alone / scans}


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


def _coinciding(ans: Answer) -> dict | None:
    """The file holds some counted outlines more than once: both counts, and which one is the answer."""
    twice = ans.meta.get("coinciding")
    if not twice:
        return None
    return {"outlines": twice["outlines"], "distinct": twice["distinct"],
            "extra": twice["outlines"] - twice["distinct"], "counted": twice["counted"]}


def _sheets_bench() -> dict:
    """The latest scored run of each kind on Stoa's sample sheets (scripts/bench_examples.py)."""
    out: dict[str, dict] = {}
    folder = config.RESULTS_DIR / "stoa_sheets"
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        data = json.loads(file.read_text(encoding="utf-8"))
        out[data["summary"]["method"].split(":")[0]] = {**data["summary"], "file": file.name}
    return out


def _readings_file(module, drawing: str, question: str, listing: str) -> Path:
    """Where the two readings of a free-form question are kept. New instructions, or a drawing whose
    contents list differently, give a new file: a reading names rooms by their place in that list."""
    stamp = hashlib.sha1(f"{module.__name__}\x1f{module.SYSTEM}\x1f{listing}".encode("utf-8")).hexdigest()[:10]
    digest = hashlib.sha1(f"{drawing}\x1f{question}".encode("utf-8")).hexdigest()[:20]
    return config.REPO_ROOT / "cache" / "readings" / f"{digest}-{stamp}.json"


def _kept_readings(module, drawing: str, question: str, listing: str):
    store = _readings_file(module, drawing, question, listing)
    if not store.is_file():
        return None
    try:
        first, second = json.loads(store.read_text(encoding="utf-8"))
        return module.Query(**first), module.Query(**second)
    except (ValueError, TypeError):
        return None  # written by an older version: read the question again


def _keep_readings(module, drawing: str, question: str, listing: str, pair) -> None:
    from dataclasses import asdict

    store = _readings_file(module, drawing, question, listing)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps([asdict(pair[0]), asdict(pair[1])], ensure_ascii=False), encoding="utf-8")


def _kept_file(q: Question) -> Path:
    """Where a model's answer to this question is kept. A brought-in drawing's path is a hash of its content."""
    digest = hashlib.sha1(f"{q.path}\x1f{q.question}".encode("utf-8")).hexdigest()[:20]
    return config.REPO_ROOT / "cache" / "answers" / f"{digest}.json"


def _kept_answer(q: Question) -> Answer | None:
    """A model answer already paid for: from the answers that come with the example sheets, or from the cache."""
    store = _kept_file(q)
    for kept_file in (EXAMPLES_DIR / "answers" / store.name, store):
        if kept_file.is_file():
            kept = json.loads(kept_file.read_text(encoding="utf-8"))
            return Answer(**{**kept, "cost_usd": 0.0, "meta": {**kept.get("meta", {}), "shown_again": True}})
    return None


def _engine_label(method: str) -> str:
    """What answered, in a few words for the reader."""
    if method in ENGINE_LABELS:
        return ENGINE_LABELS[method]
    if method.startswith("sheet-working"):
        return ("Boxed tile by tile, counted" if method.endswith(":tiles")
                else "Measured on the sheet against its scale bar" if method.endswith(":measured")
                else "Printed figures and rows, computed")
    if method.startswith("scan-index"):
        return "Scan read once, items boxed"
    if "pointing" in method:
        return "Gemini, each item boxed" if "gemini" in method and ":boxes" in method else "Vision model, pointing"
    return "Vision model" if method.startswith("vlm") else method


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
    app = FastAPI(title="Kaava", docs_url="/api/docs", openapi_url="/api/openapi.json")
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
    def mesh_of(drawing: str, response: Response):
        need(drawing)
        model = service.model_of(drawing)
        try:
            if model:
                path = service.ds.abspath(model)
                if not mesh.is_built(path):
                    # Building a large model's mesh takes minutes the first time: longer than a proxy
                    # lets one request live. It is built on its own; the page asks again until it is there.
                    with service.lock:
                        job = service._flights.get(f"mesh:{path}")
                        if job is None:
                            job = service._flights[f"mesh:{path}"] = {"done": threading.Event(), "failed": None}

                            def build(job=job, path=path):
                                try:
                                    mesh.build_apart(path)  # in a process of its own: this one keeps answering
                                except Exception as exc:
                                    job["failed"] = exc
                                finally:
                                    job["finished"] = time.time()
                                    job["done"].set()

                            threading.Thread(target=build, daemon=True).start()
                    if not job["done"].wait(min(WAIT_FOR_MODEL_S, 40)):
                        response.status_code = 202
                        return {"pending": True}
                    if job["failed"] is not None:
                        with service.lock:
                            service._flights.pop(f"mesh:{path}", None)
                        raise job["failed"]
                data = mesh.ifc_mesh(path)
            elif ".dxf" in service.files_of(drawing):
                data = mesh.plan_mesh(service.ds.abspath(service.files_of(drawing)[".dxf"]))
            elif service.scan_image(drawing) is not None:
                # A scan: its walls found in the pixels (no model is asked), standing on the scan itself.
                try:
                    data = mesh.scan_mesh(service.scan_image(drawing))
                except mesh.NoScanWalls as exc:
                    raise HTTPException(404, f"no 3D view for this scan: {exc}")
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
    def ask(req: AskRequest, response: Response):
        need(req.drawing)
        try:
            return service.ask(req)
        except StillWorking:
            # Not an error: a long answer is still being worked on. The page asks again and joins it.
            response.status_code = 202
            return {"pending": True}

    @app.post("/api/known")
    def known(body: dict) -> dict:
        """Which of these drawings this server can show (the examples name drawings of the full dataset)."""
        return {"known": [d for d in body.get("drawings", [])[:200] if isinstance(d, str) and service.allowed(d)]}

    @app.get("/api/progress")
    def progress_of(drawing: str, question: str) -> dict:
        """What a long answer is doing right now (reading the sheet, tile 3 of 8), for the one waiting."""
        need(drawing)
        asked, _ = service._question_for(drawing, " ".join(question.split()))
        return {"text": progress.get(asked.qid)}

    @app.post("/api/upload")
    async def upload(request: Request, name: str = "drawing") -> dict:
        return service.upload(name, await request.body())

    @app.get("/api/enhanced")
    def enhanced(drawing: str) -> dict:
        return service.enhanced(need(drawing))

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
        # An answer is a sentence or two. On a public link, longer text is not read: the voice is paid by the character.
        text = " ".join(req.text.split())[:MAX_SPOKEN_CHARS]
        if not text:
            raise HTTPException(400, "nothing to read aloud")
        return Response(speak(text), media_type="audio/mpeg")

    @app.get("/api/partners")
    def partners() -> dict:
        """Which partner services this server is set up to use (no secrets)."""
        from dotenv import load_dotenv

        load_dotenv(config.REPO_ROOT / ".env")
        return {"gemini": bool(os.environ.get("GEMINI_API_KEY")), "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "cloudinary": cloud.configured(), "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
                "aiven": ledger.enabled()}

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
