"""Turn a drawing file into the image(s) a vision model sees.

Only formats that are pictures of a drawing are rendered: raster files, PDF pages
and SVG. IFC, DXF and DWG have no image here; an image-only method must decline them.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
PAGED = {".pdf", ".svg"}
# Formats the model APIs accept as-is, so an already small file is sent untouched.
_PASSTHROUGH = {"PNG": "image/png", "JPEG": "image/jpeg"}
_MAX_RAW_BYTES = 7_000_000  # stays under a 10 MB base64 limit per image
PATCH = 28  # one visual token per 28 x 28 px patch


@dataclass(frozen=True)
class Rendered:
    data: bytes
    media_type: str
    width: int
    height: int
    page: int
    scale: float  # rendered px per source unit (px for rasters, pt for PDF pages)

    @property
    def visual_tokens(self) -> int:
        return visual_tokens(self.width, self.height)

    def describe(self) -> dict:
        return {"page": self.page, "width": self.width, "height": self.height,
                "scale": round(self.scale, 5), "visual_tokens": self.visual_tokens}


def can_render(path: str | Path) -> bool:
    return Path(path).suffix.lower() in RASTER | PAGED


def visual_tokens(width: int, height: int) -> int:
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


def fit_size(width: float, height: float, max_edge: int, max_tokens: int) -> tuple[int, int]:
    """Largest size within the model's long-edge and visual-token limits, never upscaled.

    Resizing here, to exactly what the model would see anyway, keeps uploads small
    and makes the token estimate exact.
    """
    scale = min(1.0, max_edge / max(width, height))
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    while visual_tokens(w, h) > max_tokens:
        scale *= math.sqrt(max_tokens / visual_tokens(w, h)) * 0.999
        w, h = max(1, round(width * scale)), max(1, round(height * scale))
    return w, h


def _page_zoom(width: float, height: float, max_edge: int, max_tokens: int) -> float:
    """Zoom for a vector page: it has no native pixel size, so use the model's full limit."""
    return min(max_edge / max(width, height),
               math.sqrt(max_tokens * PATCH * PATCH / (width * height)))


def planned_tokens(path: Path, *, max_edge: int, max_tokens: int, max_pages: int = 4) -> int:
    """Visual tokens the rendered drawing will cost, without rendering it (for estimates)."""
    ext = path.suffix.lower()
    if ext in RASTER:
        from PIL import Image

        with Image.open(path) as im:
            return visual_tokens(*fit_size(im.width, im.height, max_edge, max_tokens))
    if ext in PAGED:
        import pymupdf

        total = 0
        with pymupdf.open(path) as doc:
            for number in range(min(len(doc), max_pages)):
                rect = doc[number].rect
                zoom = _page_zoom(rect.width, rect.height, max_edge, max_tokens)
                total += min(max_tokens, visual_tokens(round(rect.width * zoom), round(rect.height * zoom)))
        return total
    raise ValueError(f"no image for {ext} files")


def render(path: Path, *, max_edge: int, max_tokens: int, max_pages: int = 4) -> list[Rendered]:
    """Render up to `max_pages` pages of a drawing, each within the model's limits."""
    return list(_render_cached(str(path), max_edge, max_tokens, max_pages))


@lru_cache(maxsize=32)  # a drawing usually gets several questions in a row
def _render_cached(path_str: str, max_edge: int, max_tokens: int, max_pages: int) -> tuple[Rendered, ...]:
    path = Path(path_str)
    ext = path.suffix.lower()
    if ext in RASTER:
        return (_render_raster(path, max_edge, max_tokens),)
    if ext in PAGED:
        return tuple(_render_paged(path, max_edge, max_tokens, max_pages))
    raise ValueError(f"no image for {ext} files")


def _render_raster(path: Path, max_edge: int, max_tokens: int) -> Rendered:
    from PIL import Image

    with Image.open(path) as im:
        w, h = fit_size(im.width, im.height, max_edge, max_tokens)
        untouched = (w, h) == im.size and im.format in _PASSTHROUGH
        if untouched and path.stat().st_size <= _MAX_RAW_BYTES:
            return Rendered(path.read_bytes(), _PASSTHROUGH[im.format], w, h, 0, 1.0)
        scale = w / im.width
        if im.mode in ("RGBA", "LA", "P"):  # flatten transparency onto white paper
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            im = flat
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if (w, h) != im.size:
            im = im.resize((w, h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    if buf.tell() > _MAX_RAW_BYTES:  # a photographic scan: PNG is too heavy
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
        return Rendered(buf.getvalue(), "image/jpeg", w, h, 0, scale)
    return Rendered(buf.getvalue(), "image/png", w, h, 0, scale)


def _render_paged(path: Path, max_edge: int, max_tokens: int, max_pages: int) -> list[Rendered]:
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for number in range(min(len(doc), max_pages)):
            page = doc[number]
            zoom = _page_zoom(page.rect.width, page.rect.height, max_edge, max_tokens)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            while visual_tokens(pix.width, pix.height) > max_tokens:
                zoom *= 0.995
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            out.append(Rendered(pix.tobytes("png"), "image/png", pix.width, pix.height, number, zoom))
    return out
