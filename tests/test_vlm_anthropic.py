"""The Anthropic adapter against a local stand-in for the Messages API.

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

from blueprints import baseline
from blueprints.harness import FatalEngineError
from blueprints.render import Rendered
from blueprints.vlm import AnthropicVLM

REPLY = {"answer_number": 7, "answer_text": None, "confidence": 0.9, "basis": "seven windows"}


def _message(**over):
    body = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": json.dumps(REPLY)},
        ],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1500, "output_tokens": 300,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }
    body.update(over)
    return 200, body


def _error(status, kind, message):
    return status, {"type": "error", "error": {"type": kind, "message": message}}


@pytest.fixture
def api(monkeypatch):
    """A local server; set api.reply to choose what it answers, read api.requests after."""
    class State:
        reply = _message()
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
            self.send_header("request-id", "req_test")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}")
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
    reply = _ask(AnthropicVLM(max_retries=0))
    req = api.requests[-1]
    body = req["body"]
    assert req["path"] == "/v1/messages"
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["system"] == baseline.SYSTEM
    assert body["output_config"] == {"effort": "high",
                                     "format": {"type": "json_schema", "schema": baseline.SCHEMA}}
    # Current models reject sampling parameters, thinking budgets and forced tool use.
    for forbidden in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "tools"):
        assert forbidden not in body
    (message,) = body["messages"]
    image, text = message["content"]
    assert message["role"] == "user" and text == {"type": "text", "text": "Question: How many windows?"}
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"
    with Image.open(io.BytesIO(base64.b64decode(image["source"]["data"]))) as im:
        assert im.size == (56, 28)
    assert reply.data == REPLY


def test_usage_and_cost_come_from_the_response(api):
    reply = _ask(AnthropicVLM(max_retries=0))
    assert (reply.input_tokens, reply.output_tokens) == (1500, 300)
    # Opus 5.5: $4 in, $20 out per million tokens.
    assert reply.cost_usd == pytest.approx(1500 * 4e-6 + 300 * 20e-6)
    assert reply.model == "claude-opus-5-5" and reply.stop_reason == "end_turn"
    assert reply.error is None and reply.no_answer == ""


def test_schema_stays_within_the_supported_subset():
    def walk(node):
        if isinstance(node, dict):
            for bad in ("minimum", "maximum", "multipleOf", "minLength", "maxLength", "maxItems"):
                assert bad not in node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)

    walk(baseline.SCHEMA)


def test_refusal_is_no_answer_not_an_error(api):
    api.reply = _message(content=[], stop_reason="refusal",
                         stop_details={"type": "refusal", "category": "cyber", "explanation": "x"})
    reply = _ask(AnthropicVLM(max_retries=0))
    assert reply.data is None and reply.error is None and "declined" in reply.no_answer


def test_cut_off_reply_is_no_answer(api):
    api.reply = _message(content=[{"type": "text", "text": '{"answer_num'}], stop_reason="max_tokens")
    reply = _ask(AnthropicVLM(max_retries=0))
    assert reply.data is None and "max_tokens" in reply.no_answer
    assert reply.cost_usd > 0  # tokens were still billed


def test_bad_key_is_fatal_and_never_echoes_the_key(api):
    api.reply = _error(401, "authentication_error", "invalid x-api-key")
    with pytest.raises(FatalEngineError) as exc:
        _ask(AnthropicVLM(max_retries=0))
    assert "test-key-not-real" not in str(exc.value) and ".env" in str(exc.value)


def test_unknown_model_is_fatal(api):
    api.reply = _error(404, "not_found_error", "model: nope")
    with pytest.raises(FatalEngineError):
        _ask(AnthropicVLM(max_retries=0))


@pytest.mark.parametrize("status,kind", [(400, "invalid_request_error"), (429, "rate_limit_error"),
                                         (529, "overloaded_error")])
def test_per_request_failures_are_recorded_for_retry(api, status, kind):
    api.reply = _error(status, kind, "nope")
    reply = _ask(AnthropicVLM(max_retries=0))
    assert reply.data is None and reply.error and reply.cost_usd == 0.0


def test_missing_credentials_are_fatal_with_instructions(api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")  # no login profile on disk either
    with pytest.raises(FatalEngineError) as exc:
        _ask(AnthropicVLM(max_retries=0))
    assert ".env" in str(exc.value) and not api.requests


def test_unpriced_model_is_refused_up_front(api):
    with pytest.raises(FatalEngineError):
        AnthropicVLM(model="some-model-without-a-price")
