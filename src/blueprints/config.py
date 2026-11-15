"""Paths, prices and run-wide constants."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SPLITS_DIR = REPO_ROOT / "splits"
RESULTS_DIR = REPO_ROOT / "results"

SPLIT_FILE = SPLITS_DIR / "split_v1.json"

# ECB euro reference rate, 13 Nov 2026: 1 EUR = 1.1052 USD.
# https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
USD_PER_EUR = float(os.environ.get("STOA_USD_PER_EUR", "1.1052"))
FX_NOTE = "ECB reference rate 2026-11-13: 1 EUR = 1.1052 USD"

# USD per million tokens (Anthropic first-party API list prices, checked 13 Nov 2026).
# cache_read is the price of tokens served from the prompt cache; a cache write costs
# 1.25x the input price (5-minute TTL). A model missing here gets cost None, never a guess.
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-fable-5-2": {"input": 8.00, "output": 40.00, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
    # Haiku 5.5 list price for prompts up to 100K tokens (ours are far below that).
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    # Gemini API paid tier (ai.google.dev/gemini-api/docs/pricing, checked 13 Nov 2026).
    # Output includes thinking tokens. Carbon and Argon are at an introductory price
    # through 30 Nov 2026; PRICE_NOTES records what they cost afterwards.
    "gemini-4-carbon": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
    # Gemma 4 on the Gemini API: listed free of charge on Google's pricing page (free tier), with no paid
    # price published (checked 15 Nov 2026). Its calls are recorded at zero cost.
    "gemma-4-31b-it": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "gemma-4-26b-a4b-it": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "gemini-4-argon": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
    "gemini-4-hydrogen": {"input": 1.50, "output": 7.50, "cache_read": 0.075},
}
CACHE_WRITE_MULTIPLIER = 1.25
PRICE_NOTES: dict[str, str] = {
    "gemini-4-carbon": "introductory price; doubles to $4 in / $20 out per MTok on 2026-12-01",
    "gemini-4-argon": "introductory price; doubles to $4 in / $20 out per MTok on 2026-12-01",
}


# Questions per (source, type) in the FULL dataset (127,044 questions), from
# 02-dataset/DATASET.md. Used to re-weight results to the real question mix while we
# still work on the preview, whose mix is different (it over-represents IFC).
FULL_SET_MIX: dict[tuple[str, str], int] = {
    ("cubicasa5k", "count"): 85_514,
    ("swiss_dwellings", "count"): 25_568,
    ("swiss_dwellings", "area"): 12_016,
    ("ifc_models", "area"): 2_218,
    ("ifc_models", "count"): 642,
    ("ifc_models", "volume"): 138,
    ("ifc_models", "length"): 64,
    ("ifc_bench", "text"): 820,
    ("opening_design", "count"): 39,
    ("opening_design", "text"): 16,
    ("opening_design", "area"): 9,
}


def count_mode() -> str:
    """How outlines that lie exactly on one another are counted.

    "distinct" (the default): each place once, which is what stands on the drawing.
    "outlines": every outline in the file, which is how the dataset's own answers count
    (a door between two units is listed once for each unit). Set with BLUEPRINTS_COUNT.
    """
    mode = os.environ.get("BLUEPRINTS_COUNT", "distinct").strip().lower()
    return mode if mode in ("distinct", "outlines") else "distinct"


def usd_to_eur(usd: float | None) -> float | None:
    return None if usd is None else usd / USD_PER_EUR


def token_cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Exact cost of one call from its token usage, or None for an unpriced model."""
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None:
        return None
    return (
        input_tokens * price["input"]
        + output_tokens * price["output"]
        + cache_read_tokens * price["cache_read"]
        + cache_write_tokens * price["input"] * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


def dataset_dir() -> Path:
    """The unpacked dataset: STOA_DATASET_DIR if set, else the full set, else the preview."""
    env = os.environ.get("STOA_DATASET_DIR")
    if env:
        return Path(env)
    for name in ("junction-dataset", "junction-dataset-preview"):
        candidate = DATA_DIR / name
        if (candidate / "questions.jsonl").is_file():
            return candidate
    raise FileNotFoundError(
        f"No dataset under {DATA_DIR}. Run: bash 02-dataset/download.sh preview"
    )
