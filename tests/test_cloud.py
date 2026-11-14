"""Cloudinary preparation: which steps a brought drawing goes through (no network)."""

from blueprints import cloud


def test_a_small_scan_is_enlarged_and_a_large_one_only_limited():
    small = cloud.steps_for(800, 600)
    assert small[0] == {"effect": "upscale"}
    assert {"width": 3000, "height": 3000, "crop": "limit"} in small and small[-1] == {"fetch_format": "png"}
    large = cloud.steps_for(4000, 3000)
    assert {"effect": "upscale"} not in large and {"effect": "improve"} in large


def test_nothing_is_sent_without_credentials(monkeypatch):
    for name in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("BLUEPRINTS_OFFLINE", raising=False)
    assert cloud.configured() is False
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")
    assert cloud.configured() is True
    monkeypatch.setenv("BLUEPRINTS_OFFLINE", "1")
    assert cloud.configured() is False
