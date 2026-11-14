"""Vision-model adapters: images + a question in, one JSON object + exact usage out.

Used only by the image-only baseline (and later as the last-resort fallback). The
adapters never compute an answer themselves and never see reference answers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Sequence

from . import config
from .harness import FatalEngineError
from .render import Rendered


@dataclass
class VLMReply:
    data: dict | None  # the parsed JSON object; None when there is no usable output
    model: str  # the model that actually served the request
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = 0.0
    latency_ms: float = 0.0
    stop_reason: str = ""
    request_id: str | None = None
    no_answer: str = ""  # the model produced nothing usable (refusal, cut off): why
    error: str | None = None  # the call itself failed (network, rate limit, bad request)

    def usage(self) -> dict:
        return {
            "model": self.model, "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens, "stop_reason": self.stop_reason,
            "request_id": self.request_id,
        }


class AnthropicVLM:
    """Claude via the Messages API with a JSON-schema constrained reply."""

    provider = "anthropic"
    # High-resolution tier (Claude 4.7 and later): images above either limit are
    # downscaled by the API, so we render to exactly this.
    max_edge = 2576
    max_visual_tokens = 4784

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high",
                 max_tokens: int = 16000, max_retries: int = 6):
        import anthropic

        if model not in config.PRICES_USD_PER_MTOK:
            raise FatalEngineError(
                f"no price on file for model {model!r}; add it to config.PRICES_USD_PER_MTOK "
                "so the run can report cost"
            )
        self._anthropic = anthropic
        # Reads ANTHROPIC_API_KEY from the environment (loaded from .env by the CLI).
        self.client = anthropic.Anthropic(max_retries=max_retries)
        self.model, self.effort, self.max_tokens = model, effort, max_tokens

    @property
    def label(self) -> str:
        return f"{self.model}:effort={self.effort}:px={self.max_edge}"

    def image_tokens(self, image: Rendered) -> int:
        return image.visual_tokens

    def ask(self, system: str, images: Sequence[Rendered], text: str, schema: dict) -> VLMReply:
        anthropic = self._anthropic
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": im.media_type,
                    "data": base64.standard_b64encode(im.data).decode("ascii"),
                },
            }
            for im in images
        ]
        content.append({"type": "text", "text": text})

        t0 = time.perf_counter()
        try:
            # Thinking is adaptive by default on current models; effort sets its depth.
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": content}],
            )
        except TypeError as exc:
            # The SDK raises this before sending anything when it finds no credentials.
            if "authentication" in str(exc).lower():
                raise FatalEngineError(
                    "no Anthropic credentials found. Put ANTHROPIC_API_KEY=... in .env "
                    "(see .env.example)."
                ) from None
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise FatalEngineError(
                f"the API rejected the credentials ({exc.status_code}). "
                "Check ANTHROPIC_API_KEY in .env."
            ) from None
        except anthropic.NotFoundError as exc:
            raise FatalEngineError(f"model {self.model!r} not found: {exc.message}") from None
        except anthropic.BadRequestError as exc:
            return self._failed(f"bad request: {exc.message}", t0)
        except anthropic.RateLimitError:
            return self._failed("rate limited after retries", t0)
        except anthropic.APIStatusError as exc:
            return self._failed(f"API error {exc.status_code}: {exc.message}", t0)
        except anthropic.APIConnectionError as exc:
            return self._failed(f"network error: {exc}", t0)
        latency_ms = (time.perf_counter() - t0) * 1000

        usage = response.usage
        reply = VLMReply(
            data=None,
            model=response.model,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
            stop_reason=response.stop_reason or "",
            request_id=getattr(response, "_request_id", None),
        )
        # Priced at the requested model's list price; no fallback model is configured,
        # so the serving model is the requested one.
        reply.cost_usd = config.token_cost_usd(
            self.model, reply.input_tokens, reply.output_tokens,
            reply.cache_read_tokens, reply.cache_write_tokens,
        )

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            reply.no_answer = f"model declined the request ({category})"
            return reply
        if response.stop_reason == "max_tokens":
            reply.no_answer = "reply cut off at max_tokens"
            return reply
        text_out = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text_out)
        except json.JSONDecodeError:
            reply.no_answer = "reply was not valid JSON"
            return reply
        if not isinstance(data, dict):
            reply.no_answer = "reply was not a JSON object"
            return reply
        reply.data = data
        return reply

    def _failed(self, error: str, t0: float) -> VLMReply:
        return VLMReply(data=None, model=self.model, error=error,
                        latency_ms=(time.perf_counter() - t0) * 1000)


class GeminiVLM:
    """Gemini via the Interactions API with a JSON-schema constrained reply."""

    provider = "gemini"
    native_boxes = True  # locates things as [ymin, xmin, ymax, xmax] on a 0-1000 grid (pointing.py)
    # Gemini spends a fixed token budget per image whatever its pixel size:
    # 1,120 tokens at "high", 2,240 at "ultra_high". We send the same render as for
    # other models and let the API downsample.
    max_edge = 2576
    max_visual_tokens = 4784
    IMAGE_TOKENS = {"low": 280, "medium": 560, "high": 1120, "ultra_high": 2240}

    def __init__(self, model: str = "gemini-4-carbon", effort: str = "high",
                 resolution: str | None = None, max_output_tokens: int = 16000, attempts: int = 6):
        from google import genai
        from google.genai import errors, types

        # The image budget can be set from the environment to measure a cheaper setting.
        resolution = resolution or os.environ.get("BLUEPRINTS_GEMINI_RESOLUTION") or "ultra_high"

        if model not in config.PRICES_USD_PER_MTOK:
            raise FatalEngineError(
                f"no price on file for model {model!r}; add it to config.PRICES_USD_PER_MTOK "
                "so the run can report cost"
            )
        if resolution not in self.IMAGE_TOKENS:
            raise FatalEngineError(f"unknown image resolution {resolution!r}")
        self._genai, self._errors, self._types = genai, errors, types
        self._attempts = attempts
        self._client = None  # made on first use, so a dry run needs no key
        self.model, self.effort, self.resolution = model, effort, resolution
        self.max_output_tokens = max_output_tokens

    @property
    def client(self):
        if self._client is None:
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise FatalEngineError(
                    "no Gemini credentials found. Put GEMINI_API_KEY=... in .env (see .env.example)."
                )
            options = self._types.HttpOptions(
                retry_options=self._types.HttpRetryOptions(attempts=self._attempts),
                # Tests point this at a local server; unset means Google's endpoint.
                base_url=os.environ.get("GEMINI_BASE_URL") or None,
            )
            self._client = self._genai.Client(api_key=key, http_options=options)
        return self._client

    @property
    def label(self) -> str:
        return f"{self.model}:thinking={self.effort}:img={self.resolution}"

    def image_tokens(self, image: Rendered) -> int:
        return self.IMAGE_TOKENS[self.resolution]

    def ask(self, system: str, images: Sequence[Rendered], text: str, schema: dict) -> VLMReply:
        # Google's guidance: with a single image, put the text before the image.
        content: list[dict] = [{"type": "text", "text": text}]
        content += [
            {
                "type": "image",
                "data": base64.b64encode(im.data).decode("ascii"),
                "mime_type": im.media_type,
                "resolution": self.resolution,
            }
            for im in images
        ]
        client = self.client  # raises a clear error when there is no key
        t0 = time.perf_counter()
        try:
            interaction = client.interactions.create(
                model=self.model,
                system_instruction=system,
                input=content,
                generation_config={
                    "thinking_level": self.effort,
                    "max_output_tokens": self.max_output_tokens,
                },
                response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                store=False,  # stateless: Google does not keep the drawing or the answer
            )
        except Exception as exc:
            # The Interactions client raises its own error classes from a private
            # module, so classify by HTTP status and class name, not by import path.
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            name = type(exc).__name__
            message = str(exc)[:300]
            if status in (401, 403):
                raise FatalEngineError(
                    f"the Gemini API rejected the credentials, or model {self.model!r} is not "
                    f"enabled for this key ({status}). Check GEMINI_API_KEY in .env. "
                    "Gemini 4 Carbon needs a paid-tier key."
                ) from None
            if status == 404:
                raise FatalEngineError(f"model {self.model!r} not found ({message})") from None
            if status == 429:
                return self._failed(f"rate limited or out of quota after retries: {message}", t0)
            if isinstance(status, int):
                return self._failed(f"API error {status}: {message}", t0)
            module = type(exc).__module__
            if ("Connection" in name or "Timeout" in name or isinstance(exc, (OSError, TimeoutError))
                    or module.startswith(("httpx", "httpcore"))):
                return self._failed(f"network error: {name}: {message}", t0)
            raise
        latency_ms = (time.perf_counter() - t0) * 1000

        usage = getattr(interaction, "usage", None)

        def tokens(name: str) -> int:
            return int(getattr(usage, name, 0) or 0)

        thought = tokens("total_thought_tokens")
        cached = tokens("total_cached_tokens")
        reply = VLMReply(
            data=None,
            model=getattr(interaction, "model", None) or self.model,
            input_tokens=tokens("total_input_tokens") - cached,
            # Thinking tokens are billed as output; the API reports them separately.
            output_tokens=tokens("total_output_tokens") + thought,
            cache_read_tokens=cached,
            latency_ms=latency_ms,
            stop_reason=str(getattr(interaction, "status", "") or ""),
            request_id=getattr(interaction, "id", None),
        )
        reply.cost_usd = config.token_cost_usd(
            self.model, reply.input_tokens, reply.output_tokens, reply.cache_read_tokens)

        status = reply.stop_reason
        if status == "incomplete":
            reply.no_answer = "reply cut off (max_output_tokens)"
            return reply
        if status != "completed":
            reply.no_answer = f"interaction ended with status {status!r}"
            return reply
        text_out = getattr(interaction, "output_text", None) or ""
        try:
            data = json.loads(text_out)
        except json.JSONDecodeError:
            reply.no_answer = "reply was not valid JSON"
            return reply
        if not isinstance(data, dict):
            reply.no_answer = "reply was not a JSON object"
            return reply
        reply.data = data
        return reply

    def _failed(self, error: str, t0: float) -> VLMReply:
        return VLMReply(data=None, model=self.model, error=error,
                        latency_ms=(time.perf_counter() - t0) * 1000)


class MockVLM:
    """Offline stand-in for tests and dry pipeline checks. Costs nothing, knows nothing."""

    provider = "mock"
    max_edge = AnthropicVLM.max_edge
    max_visual_tokens = AnthropicVLM.max_visual_tokens
    model = "mock"
    effort = "none"
    label = "mock"

    def __init__(self, fail_every: int = 0):
        self.calls = 0
        self.fail_every = fail_every

    def image_tokens(self, image: Rendered) -> int:
        return image.visual_tokens

    def ask(self, system: str, images: Sequence[Rendered], text: str, schema: dict) -> VLMReply:
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            return VLMReply(data=None, model="mock", error="mock failure")
        digest = int(hashlib.sha1(text.encode()).hexdigest(), 16)
        data = {
            "answer_number": digest % 7,
            "answer_text": None,
            "confidence": (digest % 100) / 100,
            "basis": "mock reply; not derived from the drawing",
        }
        return VLMReply(
            data=data, model="mock",
            input_tokens=sum(im.visual_tokens for im in images) + len(text) // 4,
            output_tokens=40, cost_usd=0.0, latency_ms=1.0, stop_reason="end_turn",
        )


PROVIDERS = ("gemini", "anthropic", "mock")


def make_vlm(provider: str, model: str | None = None, effort: str = "high"):
    if provider == "gemini":
        return GeminiVLM(model or "gemini-4-carbon", effort)
    if provider == "anthropic":
        return AnthropicVLM(model or "claude-opus-5-5", effort)
    if provider == "mock":
        return MockVLM()
    raise ValueError(f"unknown provider {provider!r} (available: {', '.join(PROVIDERS)})")
