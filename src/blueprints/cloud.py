"""Cloudinary: a sharper view of small scans, and hosting for what people share.

Two things run through Cloudinary:

    enhance   a small scan enlarged with AI super-resolution, for the reader's eyes.
              The browser loads it straight from Cloudinary, layered over the original,
              so the evidence marks keep their place.
    host      the marked-up evidence sheet a reader shares, delivered in the format and
              quality that suit whoever opens it.

The engines never read an enlarged scan. `prepare` (enlarge, even out colour, then read)
was built and measured on dev scans (scripts/eval_prepared_scans.py, logged in results/):
the enlarged scans gave no more right answers and added wrong ones at the score levels
the router trusts. Super-resolution draws plausible detail, and a label that looks
plausible is not evidence. So the engines keep reading the scan as it came, which is
what their error rates were measured on; `prepare` stays only to reproduce that run.

Everything here is optional: without CLOUDINARY_* in the environment the app works the
same, without the enlarged view and with evidence sheets served locally.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

SMALL_EDGE = 1400  # a scan whose long edge is below this is enlarged
WORK_EDGE = 3000  # no drawing is read at more than this
FOLDER = "blueprints-talk"


def configured() -> bool:
    if os.environ.get("BLUEPRINTS_OFFLINE"):  # tests and air-gapped demos: never reach out
        return False
    return bool(os.environ.get("CLOUDINARY_URL")
                or all(os.environ.get(k) for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")))


def _sdk():
    import cloudinary
    import cloudinary.uploader
    import cloudinary.utils

    if not os.environ.get("CLOUDINARY_URL"):
        cloudinary.config(cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"], api_key=os.environ["CLOUDINARY_API_KEY"],
                          api_secret=os.environ["CLOUDINARY_API_SECRET"], secure=True)
    else:
        cloudinary.config(secure=True)
    return cloudinary


def steps_for(width: int, height: int) -> list[dict]:
    """The preparation that was measured (and is not used for reading): see the module docstring."""
    steps: list[dict] = []
    if max(width, height) < SMALL_EDGE:
        steps.append({"effect": "upscale"})  # AI super-resolution: small labels become readable
    steps.append({"width": WORK_EDGE, "height": WORK_EDGE, "crop": "limit"})
    steps.append({"effect": "improve"})  # even out a phone photo's colour and contrast
    steps.append({"fetch_format": "png"})
    return steps


# What a browser sends when it loads a picture. With f_auto Cloudinary derives one copy
# per format, so warming a link has to ask for the format the browser will ask for.
BROWSER_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"


def _fetch(url: str, attempts: int = 12, accept: str | None = None) -> bytes:
    """Download a derived image. A new derivation can answer 'still working' at first."""
    last = None
    request = urllib.request.Request(url, headers={"Accept": accept} if accept else {})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (420, 423, 429, 500, 502, 503):
                raise
        except urllib.error.URLError as exc:
            last = exc
        time.sleep(min(8.0, 1.5 * (attempt + 1)))
    raise RuntimeError(f"Cloudinary did not deliver the image in time: {last}")


def prepare(original: Path, ident: str, width: int, height: int) -> tuple[bytes, dict]:
    """Upload a drawing and return the prepared PNG with what was done to it. Measurement only."""
    cloudinary = _sdk()
    public_id = f"{FOLDER}/drawings/{ident}"
    cloudinary.uploader.upload(str(original), public_id=public_id, overwrite=True, resource_type="image")
    steps = steps_for(width, height)
    url, _ = cloudinary.utils.cloudinary_url(public_id, transformation=steps, secure=True)
    data = _fetch(url)
    return data, {"public_id": public_id, "url": url, "upscaled": any(s.get("effect") == "upscale" for s in steps),
                  "steps": [next(f"{k}: {v}" for k, v in s.items() if k != "crop" and k != "height") for s in steps]}


def enhance(image: Path, ident: str) -> dict:
    """A small scan enlarged with AI super-resolution, for the reader's eyes.

    Returns a delivery link the browser loads straight from Cloudinary, in the format
    and quality that suit it. The engines keep reading the scan as it came: that is
    what their error rates were measured on.
    """
    cloudinary = _sdk()
    public_id = f"{FOLDER}/drawings/{ident}"
    cloudinary.uploader.upload(str(image), public_id=public_id, overwrite=False, resource_type="image")
    steps = [{"effect": "upscale"}, {"fetch_format": "auto", "quality": "auto"}]
    url, _ = cloudinary.utils.cloudinary_url(public_id, transformation=steps, secure=True)
    _fetch(url, accept=BROWSER_ACCEPT)  # the first request does the work; after it the link answers at once
    return {"public_id": public_id, "url": url}


def host(image: Path, ident: str, caption: str = "") -> dict:
    """Host an evidence sheet and return the link to share (format and quality chosen per viewer)."""
    cloudinary = _sdk()
    public_id = f"{FOLDER}/evidence/{ident}"
    context = {"caption": caption[:240]} if caption else None
    cloudinary.uploader.upload(str(image), public_id=public_id, overwrite=True, resource_type="image", context=context)
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, transformation=[{"width": 2400, "crop": "limit"}, {"fetch_format": "auto", "quality": "auto"}], secure=True)
    return {"public_id": public_id, "url": url}
