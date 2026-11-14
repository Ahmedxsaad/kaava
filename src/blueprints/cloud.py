"""Cloudinary: prepare the drawings people bring, and host what they share.

A drawing photographed on site, or scanned at low resolution, is the hard case: labels
a few pixels high. Before the engines read such a drawing it goes through Cloudinary:

    upload the original  ->  limit to a workable size, even out colour and contrast,
    and for small scans enlarge with AI super-resolution  ->  a PNG the engines read

The same account hosts the marked-up evidence sheet a reader shares, delivered in the
format and quality that suit whoever opens it.

Everything here is optional: without CLOUDINARY_* in the environment the app works on
the file as it came. A failure on Cloudinary's side never blocks an upload.
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
    """The transformations a brought drawing goes through before it is read."""
    steps: list[dict] = []
    if max(width, height) < SMALL_EDGE:
        steps.append({"effect": "upscale"})  # AI super-resolution: small labels become readable
    steps.append({"width": WORK_EDGE, "height": WORK_EDGE, "crop": "limit"})
    steps.append({"effect": "improve"})  # even out a phone photo's colour and contrast
    steps.append({"fetch_format": "png"})
    return steps


def _fetch(url: str, attempts: int = 12) -> bytes:
    """Download a derived image. A new derivation can answer 'still working' at first."""
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
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
    """Upload a brought drawing and return the prepared PNG with what was done to it."""
    cloudinary = _sdk()
    public_id = f"{FOLDER}/drawings/{ident}"
    cloudinary.uploader.upload(str(original), public_id=public_id, overwrite=True, resource_type="image")
    steps = steps_for(width, height)
    url, _ = cloudinary.utils.cloudinary_url(public_id, transformation=steps, secure=True)
    data = _fetch(url)
    return data, {"public_id": public_id, "url": url, "upscaled": any(s.get("effect") == "upscale" for s in steps),
                  "steps": [next(f"{k}: {v}" for k, v in s.items() if k != "crop" and k != "height") for s in steps]}


def host(image: Path, ident: str, caption: str = "") -> dict:
    """Host an evidence sheet and return the link to share (format and quality chosen per viewer)."""
    cloudinary = _sdk()
    public_id = f"{FOLDER}/evidence/{ident}"
    context = {"caption": caption[:240]} if caption else None
    cloudinary.uploader.upload(str(image), public_id=public_id, overwrite=True, resource_type="image", context=context)
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, transformation=[{"width": 2400, "crop": "limit"}, {"fetch_format": "auto", "quality": "auto"}], secure=True)
    return {"public_id": public_id, "url": url}
