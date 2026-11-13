"""The Gemini adapter against a local stand-in for the Interactions API.

No real key and no network: this checks the request we send and how we read the
reply, not the model.
"""

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from blueprints import baseline, config
from blueprints.harness import FatalEngineError
from blueprints.render import Rendered
from blueprints.vlm import GeminiVLM, make_vlm

REPLY = {"answer_number": 7, "answer_text": None, "confidence": 0.9, "basis": "seven windows"}


def _interaction(**over):
    body = {
        "created": "2026-11-13T21:00:00Z", "updated": "2026-11-13T21:00:01Z",
        "id": "v1_test", "object": "interaction", "model": "gemini-4-carbon", "status": "completed",
        "steps": [
            {"type": "thought", "signature": "sig"},
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(REPLY)}]},
        ],
        "usage": {"total_cached_tokens": 0, "total_input_tokens": 2400, "total_output_tokens": 60,
                  "total_thought_tokens": 500, "total_tokens": 2960, "total_tool_use_tokens": 0},
    }
    body.update(over)
    return 200, body


def _error(status, state, message):
    return status, {"error": {"code": status, "message": message, "status": state}}


@pytest.fixture
def api(monkeypatch):
    class State:
        reply = _interaction()
        requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            State.requests.append({"path": self.path, "headers": dict(self.headers),
                                   "body": json.loads(self.rfile.read(length))})
            status, body = State.reply
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(proxy, raising=False)
    yield State
    server.shutdown()
    server.server_close()


def _image():
    buf = io.BytesIO()
    Image.new("RGB", (56, 28), "white").save(buf, format="PNG")
    return Rendered(buf.getvalue(), "image/png", 56, 28, 0, 1.0)


def _ask(vlm):
    return vlm.ask(baseline.SYSTEM, [_image()], "Question: How many windows?", baseline.SCHEMA)


def test_request_shape(api):
    reply = _ask(GeminiVLM(attempts=1))
    req = api.requests[-1]
    body = req["body"]
    assert req["path"].endswith("/interactions")
    assert body["model"] == "gemini-4-carbon"
    assert body["system_instruction"] == baseline.SYSTEM
    assert body["generation_config"]["thinking_level"] == "high"
    assert body["response_format"] == {"type": "text", "mime_type": "application/json",
                                       "schema": baseline.SCHEMA}
    assert body["store"] is False            # the drawing is not kept on Google's side
    (turn,) = body["input"]
    text, image = turn["content"]            # text first, then the image
    assert text == {"type": "text", "text": "Question: How many windows?"}
    assert image["type"] == "image" and image["mime_type"] == "image/png"
    assert image["resolution"] == "ultra_high"
    with Image.open(io.BytesIO(base64.b64decode(image["data"]))) as im:
        assert im.size == (56, 28)
    assert reply.data == REPLY


def test_thinking_tokens_are_billed_as_output(api):
    reply = _ask(GeminiVLM(attempts=1))
    assert reply.input_tokens == 2400 and reply.output_tokens == 60 + 500
    # Carbon today: $2 in, $10 out per million tokens.
    assert reply.cost_usd == pytest.approx(2400 * 2e-6 + 560 * 10e-6)
    assert reply.stop_reason == "completed" and reply.error is None


def test_cached_input_is_priced_at_the_cache_rate(api):
    api.reply = _interaction(usage={"total_cached_tokens": 1000, "total_input_tokens": 2400,
                                    "total_output_tokens": 60, "total_thought_tokens": 0,
                                    "total_tokens": 2460})
    reply = _ask(GeminiVLM(attempts=1))
    assert (reply.input_tokens, reply.cache_read_tokens) == (1400, 1000)
    assert reply.cost_usd == pytest.approx(1400 * 2e-6 + 1000 * 0.10e-6 + 60 * 10e-6)


def test_cut_off_reply_is_no_answer_but_still_costs(api):
    api.reply = _interaction(status="incomplete", steps=[
        {"type": "model_output", "content": [{"type": "text", "text": '{"answer_num'}]}])
    reply = _ask(GeminiVLM(attempts=1))
    assert reply.data is None and "cut off" in reply.no_answer and reply.cost_usd > 0


def test_failed_interaction_is_no_answer(api):
    api.reply = _interaction(status="failed", steps=[])
    reply = _ask(GeminiVLM(attempts=1))
    assert reply.data is None and "failed" in reply.no_answer and reply.error is None


@pytest.mark.parametrize("status,state", [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
def test_bad_key_is_fatal_and_never_echoes_the_key(api, status, state):
    api.reply = _error(status, state, "API key not valid")
    with pytest.raises(FatalEngineError) as exc:
        _ask(GeminiVLM(attempts=1))
    assert "test-key-not-real" not in str(exc.value) and ".env" in str(exc.value)


def test_unknown_model_is_fatal(api):
    api.reply = _error(404, "NOT_FOUND", "model not found")
    with pytest.raises(FatalEngineError):
        _ask(GeminiVLM(attempts=1))


@pytest.mark.parametrize("status,state", [(400, "INVALID_ARGUMENT"), (429, "RESOURCE_EXHAUSTED"),
                                          (503, "UNAVAILABLE")])
def test_per_request_failures_are_recorded_for_retry(api, status, state):
    api.reply = _error(status, state, "nope")
    reply = _ask(GeminiVLM(attempts=1))
    assert reply.data is None and reply.error and reply.cost_usd == 0.0


def test_missing_key_is_fatal_only_when_a_call_is_made(api, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    vlm = make_vlm("gemini")          # building it (for a dry run) needs no key
    assert vlm.model == "gemini-4-carbon"
    with pytest.raises(FatalEngineError) as exc:
        _ask(vlm)
    assert "GEMINI_API_KEY" in str(exc.value) and not api.requests


def test_models_have_prices_and_carbon_carries_its_price_note():
    for model in ("gemini-4-carbon", "gemini-4-argon", "gemini-4-hydrogen"):
        assert config.token_cost_usd(model, 1_000_000, 0) > 0
    assert "2026-12-01" in config.PRICE_NOTES["gemini-4-carbon"]
    with pytest.raises(FatalEngineError):
        GeminiVLM(model="gemini-without-a-price")
