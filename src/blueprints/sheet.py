"""An evidence sheet: the drawing with an answer's evidence marked on it, and a caption.

A single image a reader can attach to a report or send to site: what was asked, the
answer and its state, and every counted or measured item marked where it is.
"""

from __future__ import annotations

from pathlib import Path

from .overlay import _font, _wrap

MARK = (196, 97, 3)
INK = (1, 30, 1)
STATE = {"verified": ("Verified", (45, 128, 42)), "check": ("Check this", (140, 100, 0)),
         "declined": ("Can't answer reliably", (177, 65, 66))}


def render(image_path: Path, evidence: list[dict], *, question: str, answer_text: str, state: str,
           lines: list[str], out_path: Path) -> Path:
    """Write the sheet as a PNG. `evidence` is in the image's pixels, as the app draws it."""
    from PIL import Image, ImageDraw

    with Image.open(image_path) as im:
        plan = im.convert("RGB")
    zoom = max(1.0, 1800 / plan.width)  # a small scan is enlarged so the marks stay readable
    if zoom > 1:
        plan = plan.resize((round(plan.width * zoom), round(plan.height * zoom)), Image.Resampling.LANCZOS)
    k = max(1.0, max(plan.size) / 1800)  # marks keep their weight on a large sheet
    layer = Image.new("RGBA", plan.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    dots: list[tuple[float, float, str]] = []
    plates: list[tuple[float, float, str]] = []
    for item in evidence:
        n = item.get("n")
        if n is None:
            continue
        if item.get("points"):
            pts = [(x * zoom, y * zoom) for x, y in item["points"]]
            draw.polygon(pts, fill=(254, 202, 171, 120))
            draw.line(pts + [pts[0]], fill=(*MARK, 255), width=round(3 * k))
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            label = f"{item['area_m2']:.2f} m²" if item.get("area_m2") is not None else str(n)
            plates.append((cx, cy, label))
        elif item.get("bbox"):
            x0, y0, x1, y1 = (v * zoom for v in item["bbox"])
            pad = 5 * k
            draw.rounded_rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], radius=pad, outline=(*MARK, 255), width=round(3 * k), fill=(196, 97, 3, 26))
            dots.append((x0 - pad, y0 - pad, str(n)))
        elif item.get("x") is not None:
            x, y = item["x"] * zoom, item["y"] * zoom
            r = 16 * k
            draw.ellipse([x - r, y - r, x + r, y + r], outline=(*MARK, 255), width=round(3 * k), fill=(196, 97, 3, 26))
            dots.append((x - 0.85 * r, y - 0.85 * r, str(n)))
    plan = Image.alpha_composite(plan.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(plan)
    small = _font(round(17 * k))
    for x, y, text in dots:
        r = (13 if len(text) < 3 else 16) * k
        draw.ellipse([x - r, y - r, x + r, y + r], fill=INK, outline="white", width=round(2 * k))
        w = draw.textlength(text, font=small)
        draw.text((x - w / 2, y - 10 * k), text, fill="white", font=small)
    plate_font = _font(round(20 * k))
    for x, y, text in plates:
        w = draw.textlength(text, font=plate_font)
        draw.rounded_rectangle([x - w / 2 - 12 * k, y - 16 * k, x + w / 2 + 12 * k, y + 16 * k], radius=round(16 * k), fill="white", outline=(211, 214, 217), width=round(2 * k))
        draw.text((x - w / 2, y - 12 * k), text, fill=INK, font=plate_font)

    # ---- caption
    width, margin = plan.width, 44
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    body_font, note_font, head_font, pill_font = _font(26), _font(20), _font(64), _font(24)
    question_lines = _wrap(measure, question, body_font, width - 2 * margin)
    note_lines = [line for text in lines for line in _wrap(measure, text, note_font, width - 2 * margin)]
    height = 60 + 76 + 26 + 36 * len(question_lines) + 16 + 28 * len(note_lines) + 40
    sheet = Image.new("RGB", (width, plan.height + height), "white")
    sheet.paste(plan, (0, 0))
    draw = ImageDraw.Draw(sheet)
    y = plan.height
    draw.line([(0, y), (width, y)], fill=(228, 230, 232), width=2)
    y += 40
    label, colour = STATE.get(state, STATE["check"])
    pill_w = draw.textlength(label, font=pill_font) + 54
    draw.rounded_rectangle([margin, y + 14, margin + pill_w, y + 60], radius=23, fill=tuple(int(c + (255 - c) * 0.86) for c in colour))
    draw.ellipse([margin + 16, y + 31, margin + 28, y + 43], fill=colour)
    draw.text((margin + 38, y + 22), label, fill=colour, font=pill_font)
    draw.text((margin + pill_w + 28, y), answer_text, fill=INK, font=head_font)
    y += 76 + 26
    for line in question_lines:
        draw.text((margin, y), line, fill=(44, 65, 43), font=body_font)
        y += 36
    y += 16
    for line in note_lines:
        draw.text((margin, y), line, fill=(128, 142, 128), font=note_font)
        y += 28
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path
