"""Draw an answer's evidence on the drawing.

The engines return evidence in the DXF's own coordinates (metres). The plan people
look at is the PDF. The two are lined up from the drawing itself: every room number
is written at a known place in the DXF and can be found as text in the PDF, which
gives a scale and an offset (least squares over all numbers). If they do not line up
to within a few points, no overlay is drawn rather than a misleading one.

Each counted item gets a numbered pin; each measured room is filled and labelled with
its area. The header states the answer, its state and why.
"""

from __future__ import annotations

import io
from pathlib import Path

from .answer import Answer
from .scoring import CHECK, DECLINED, VERIFIED

MAX_RESIDUAL_PT = 4.0  # anchors must agree this well, in PDF points (about 1.4 mm on paper)
RENDER_LONG_EDGE = 2600

_STATE = {
    VERIFIED: ("VERIFIED", (22, 130, 70)),
    CHECK: ("CHECK THIS", (190, 130, 0)),
    DECLINED: ("CAN'T ANSWER RELIABLY", (170, 40, 40)),
}
_PIN = (214, 40, 90)
_ROOM = (30, 110, 220)


def fit_transform(index: dict, pdf_path: Path) -> dict | None:
    """x_page = s*x + tx, y_page = -s*y + ty (PDF points), from the room numbers in both files."""
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        words = doc[0].get_text("words")
    in_pdf: dict[str, list[tuple[float, float]]] = {}
    for x0, y0, x1, y1, word, *_ in words:
        in_pdf.setdefault(word, []).append(((x0 + x1) / 2, (y0 + y1) / 2))
    in_dxf: dict[str, list[tuple[float, float]]] = {}
    for text, x, y, _height in index.get("anchors", []):
        in_dxf.setdefault(text, []).append((x, y))
    # Only numbers written exactly once in each file are unambiguous anchors.
    pairs = [(in_dxf[t][0], in_pdf[t][0]) for t in in_dxf if len(in_dxf[t]) == 1 and len(in_pdf.get(t, [])) == 1]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(p[0][0] for p in pairs) / n
    my = sum(p[0][1] for p in pairs) / n
    mX = sum(p[1][0] for p in pairs) / n
    mY = sum(p[1][1] for p in pairs) / n
    num = sum((x - mx) * (X - mX) - (y - my) * (Y - mY) for (x, y), (X, Y) in pairs)
    den = sum((x - mx) ** 2 + (y - my) ** 2 for (x, y), _ in pairs)
    if den == 0:
        return None
    s = num / den
    tx, ty = mX - s * mx, mY + s * my
    residual = max(((s * x + tx - X) ** 2 + (-s * y + ty - Y) ** 2) ** 0.5 for (x, y), (X, Y) in pairs)
    if s <= 0 or residual > MAX_RESIDUAL_PT:
        return None
    # s is PDF points per metre; a point is 1/72 inch, so the sheet's scale is 1 : this.
    paper_scale = (72 / 0.0254) / s
    return {"scale": s, "tx": tx, "ty": ty, "residual_pt": residual, "anchors": n,
            "paper_scale": paper_scale}


# A font that can draw "m²" and accented letters. Pillow's built-in one cannot, so
# look for a common system font first and only then fall back to it.
_FONT_FILES = (
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _font(size: int):
    from PIL import ImageFont

    for path in _FONT_FILES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, max_width: float) -> list[str]:
    """Break text into lines that fit `max_width` pixels in `font`."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > max_width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + ([line] if line else [])


def render(answer: Answer, question: str, pdf_path: Path, index: dict, out_path: Path) -> dict | None:
    """Write a PNG of the plan with the evidence drawn on it. Returns the transform used, or None."""
    import pymupdf
    from PIL import Image, ImageDraw

    transform = fit_transform(index, pdf_path)
    if transform is None:
        return None
    s, tx, ty = transform["scale"], transform["tx"], transform["ty"]
    # Show the plan itself, not the whole sheet: crop to the rooms plus a margin.
    xs = [s * x + tx for room in index["rooms"] for x, _ in room["polygon"]]
    ys = [-s * y + ty for room in index["rooms"] for _, y in room["polygon"]]
    with pymupdf.open(pdf_path) as doc:
        page = doc[0]
        margin = 0.06 * max(max(xs) - min(xs), max(ys) - min(ys)) + 12
        clip = pymupdf.Rect(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin) & page.rect
        zoom = min(RENDER_LONG_EDGE / max(clip.width, clip.height), 12.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    plan = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")

    def px(x: float, y: float) -> tuple[float, float]:
        return (s * x + tx - clip.x0) * zoom, (-s * y + ty - clip.y0) * zoom

    font = _font

    layer = Image.new("RGBA", plan.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shapes = [e for e in answer.evidence if e.get("space") == "dxf_m"]
    labels = []
    for number, item in enumerate(shapes, 1):
        if item["kind"] == "polygon":
            pts = [px(x, y) for x, y in item["points"]]
            draw.polygon(pts, fill=(*_ROOM, 70), outline=(*_ROOM, 255))
            draw.line(pts + [pts[0]], fill=(*_ROOM, 255), width=4)
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            if "area_m2" in item:
                labels.append((cx, cy, f"{item['area_m2']:.2f} m²", _ROOM))
        elif item["kind"] == "box":
            x0, y0, x1, y1 = item["bbox"]
            (ax, ay), (bx, by) = px(x0, y1), px(x1, y0)
            pad = 6
            draw.rectangle([ax - pad, ay - pad, bx + pad, by + pad], outline=(*_PIN, 255), width=4,
                           fill=(*_PIN, 50))
            labels.append(((ax + bx) / 2, (ay + by) / 2, str(number), _PIN))
    plan = Image.alpha_composite(plan, layer)
    draw = ImageDraw.Draw(plan)
    for cx, cy, text, colour in labels:
        f = font(26)
        w = draw.textlength(text, font=f)
        if text.isdigit():  # a numbered pin
            r = max(20, w / 2 + 9)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour, outline="white", width=3)
            draw.text((cx - w / 2, cy - 15), text, fill="white", font=f)
        else:  # an area label on a white plate, below the room's own text
            draw.rectangle([cx - w / 2 - 8, cy + 26, cx + w / 2 + 8, cy + 62], fill="white", outline=colour, width=3)
            draw.text((cx - w / 2, cy + 28), text, fill=colour, font=f)

    # ---- header: state, answer, question, why
    state_text, state_colour = _STATE[answer.state]
    value = "no answer" if answer.answer is None else f"{answer.answer:g}" if isinstance(answer.answer, (int, float)) else str(answer.answer)
    unit = {"m2": " m²", "m3": " m³", "m": " m", "count": ""}.get(answer.unit or "", "")
    cost = "EUR 0" if not answer.cost_usd else f"USD {answer.cost_usd:.4f}"
    footer = (f"{answer.method}  ·  {cost}  ·  {len(shapes)} item(s) drawn  ·  drawing and PDF aligned on "
              f"{transform['anchors']} room numbers (max {transform['residual_pt']:.1f} pt off)")
    width = max(plan.width, 1700)  # a narrow plan still gets a readable header
    margin = 30
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    body_font, small_font = font(28), font(22)
    body = (_wrap(measure, f"Q: {question}", body_font, width - 2 * margin)
            + _wrap(measure, answer.explanation or answer.reason, body_font, width - 2 * margin))
    footer_lines = _wrap(measure, footer, small_font, width - 2 * margin)
    height = 118 + 38 * len(body) + 30 * len(footer_lines) + 14
    canvas = Image.new("RGB", (width, plan.height + height), "white")
    canvas.paste(plan.convert("RGB"), ((width - plan.width) // 2, height))
    draw = ImageDraw.Draw(canvas)
    badge = font(40)
    w = draw.textlength(state_text, font=badge)
    draw.rounded_rectangle([margin, 26, margin + w + 40, 90], radius=12, fill=state_colour)
    draw.text((margin + 20, 34), state_text, fill="white", font=badge)
    draw.text((margin + w + 70, 22), f"{value}{unit}", fill="black", font=font(56))
    y = 112
    for line in body:
        draw.text((margin, y), line, fill=(40, 40, 40), font=body_font)
        y += 38
    for line in footer_lines:
        draw.text((margin, y), line, fill=(110, 110, 110), font=small_font)
        y += 30
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return transform


def render_points(answer: Answer, question: str, image_path: Path, out_path: Path) -> bool:
    """Draw a model's pointed-at items on a raster plan (pixel coordinates of the file)."""
    from PIL import Image, ImageDraw

    points = [e for e in answer.evidence if e.get("kind") == "point" and e.get("space") == "image_px"]
    with Image.open(image_path) as im:
        plan = im.convert("RGB")
    # Small scans are enlarged so pins and text stay readable.
    zoom = max(1.0, 1700 / plan.width)
    if zoom > 1:
        plan = plan.resize((round(plan.width * zoom), round(plan.height * zoom)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(plan)
    pin_font = _font(24)
    for number, item in enumerate(points, 1):
        x, y, text = item["x"] * zoom, item["y"] * zoom, str(number)
        w = draw.textlength(text, font=pin_font)
        r = max(19, w / 2 + 8)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=_PIN, outline="white", width=3)
        draw.text((x - w / 2, y - 14), text, fill="white", font=pin_font)

    state_text, state_colour = _STATE[answer.state]
    value = "no answer" if answer.answer is None else str(answer.answer)
    cost = "EUR 0" if not answer.cost_usd else f"USD {answer.cost_usd:.4f}"
    footer = f"{answer.method}  ·  {cost}  ·  {len(points)} item(s) pointed at by the model and counted by the code"
    margin, width = 30, plan.width
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    body_font, small_font = _font(28), _font(22)
    body = (_wrap(measure, f"Q: {question}", body_font, width - 2 * margin)
            + _wrap(measure, answer.explanation or answer.reason, body_font, width - 2 * margin))
    footer_lines = _wrap(measure, footer, small_font, width - 2 * margin)
    height = 118 + 38 * len(body) + 30 * len(footer_lines) + 14
    canvas = Image.new("RGB", (width, plan.height + height), "white")
    canvas.paste(plan, (0, height))
    draw = ImageDraw.Draw(canvas)
    badge = _font(40)
    w = draw.textlength(state_text, font=badge)
    draw.rounded_rectangle([margin, 26, margin + w + 40, 90], radius=12, fill=state_colour)
    draw.text((margin + 20, 34), state_text, fill="white", font=badge)
    draw.text((margin + w + 70, 22), value, fill="black", font=_font(56))
    y = 112
    for line in body:
        draw.text((margin, y), line, fill=(40, 40, 40), font=body_font)
        y += 38
    for line in footer_lines:
        draw.text((margin, y), line, fill=(110, 110, 110), font=small_font)
        y += 30
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return True
