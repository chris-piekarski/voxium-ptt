"""Unit tests for the local llama.cpp client helpers."""

from __future__ import annotations

from typing import Any

import requests

from voxium.llama_cpp_client import (
    llama_cpp_chat,
    llama_cpp_loaded_model,
    llama_cpp_reachable,
)


class _DummyResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


def test_llama_cpp_loaded_model_returns_first_model_id(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _url, timeout: _DummyResponse(
            200,
            {"data": [{"id": "plain.gguf"}, {"id": "other.gguf"}]},
        ),
    )
    assert llama_cpp_loaded_model("http://127.0.0.1:11435") == "plain.gguf"


def test_llama_cpp_reachable_normalizes_connection_error(monkeypatch) -> None:
    def fake_get(_url: str, timeout: float) -> _DummyResponse:
        raise requests.ConnectionError("raw connection pool details")

    monkeypatch.setattr("voxium.llama_cpp_client.requests.get", fake_get)
    ok, reason = llama_cpp_reachable("http://127.0.0.1:11435")
    assert ok is False
    assert reason is not None
    assert reason.startswith("llama.cpp is unreachable at http://127.0.0.1:11435")
    assert "raw connection pool" not in reason


def test_llama_cpp_chat_normalizes_connection_error(monkeypatch) -> None:
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("raw connection pool details")

    monkeypatch.setattr("voxium.llama_cpp_client.requests.post", fake_post)
    out = llama_cpp_chat(
        "http://127.0.0.1:11435",
        "plain.gguf",
        "hello",
        timeout=1.0,
    )
    assert out.ok is False
    assert out.error is not None
    assert out.error.startswith("llama.cpp is unreachable at http://127.0.0.1:11435")
    assert "raw connection pool" not in out.error


def test_llama_cpp_reachable_ok_200(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(200, {}),
    )
    ok, reason = llama_cpp_reachable("http://127.0.0.1:11435")
    assert ok and reason is None


def test_llama_cpp_reachable_503_json_loading(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(503, {"error": {"message": "busy"}}),
    )
    ok, reason = llama_cpp_reachable("http://h:1/")
    assert not ok
    assert reason == "busy"


def test_llama_cpp_reachable_503_non_dict_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(503, {"error": "x"}),
    )
    ok, reason = llama_cpp_reachable("http://h:1/")
    assert not ok
    assert reason == "loading"


def test_llama_cpp_reachable_503_bad_json(monkeypatch) -> None:
    import json

    class R503:
        status_code = 503

        def json(self):
            raise json.JSONDecodeError("e", "doc", 0)

    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get", lambda _u, timeout: R503()
    )
    ok, reason = llama_cpp_reachable("http://h:1/")
    assert not ok
    assert reason == "loading"


def test_llama_cpp_reachable_http_418(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(418, {}),
    )
    ok, reason = llama_cpp_reachable("http://h:1/")
    assert not ok
    assert reason == "HTTP 418"


def test_llama_cpp_reachable_timeout_error(monkeypatch) -> None:
    def _raise_to(_u, timeout):
        raise requests.Timeout("t")

    monkeypatch.setattr("voxium.llama_cpp_client.requests.get", _raise_to)
    ok, reason = llama_cpp_reachable("http://127.0.0.1:11435")
    assert not ok
    assert "timed out" in (reason or "")


def test_llama_cpp_reachable_generic_request_exception(monkeypatch) -> None:
    def _raise_http(_u, timeout):
        raise requests.RequestException("weird")

    monkeypatch.setattr("voxium.llama_cpp_client.requests.get", _raise_http)
    ok, reason = llama_cpp_reachable("http://127.0.0.1:11435")
    assert not ok
    assert "request failed" in (reason or "")


def test_llama_cpp_loaded_model_request_exception(monkeypatch) -> None:
    def _raise(_u, timeout):
        raise requests.RequestException("x")

    monkeypatch.setattr("voxium.llama_cpp_client.requests.get", _raise)
    assert llama_cpp_loaded_model("http://h:1/") is None


def test_llama_cpp_loaded_model_bad_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(500, {}),
    )
    assert llama_cpp_loaded_model("http://h:1/") is None


def test_llama_cpp_loaded_model_invalid_json(monkeypatch) -> None:
    import json

    class R200:
        status_code = 200

        def json(self):
            raise json.JSONDecodeError("e", "doc", 0)

    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get", lambda _u, timeout: R200()
    )
    assert llama_cpp_loaded_model("http://h:1/") is None


def test_llama_cpp_loaded_model_empty_data(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(200, {"data": []}),
    )
    assert llama_cpp_loaded_model("http://h:1/") is None


def test_llama_cpp_loaded_model_first_item_not_dict(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.get",
        lambda _u, timeout: _DummyResponse(200, {"data": [1]}),
    )
    assert llama_cpp_loaded_model("http://h:1/") is None


def test_llama_cpp_chat_non_200(monkeypatch) -> None:
    class R500:
        status_code = 500
        text = "body" * 200

    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.post",
        lambda *a, **k: R500(),
    )
    out = llama_cpp_chat("http://h:1/", "m", "t", timeout=1.0)
    assert not out.ok
    assert "HTTP 500" in (out.error or "")


def test_llama_cpp_chat_invalid_json_response(monkeypatch) -> None:
    import json

    class R200BadJson:
        status_code = 200

        def json(self):
            raise json.JSONDecodeError("e", "doc", 0)

    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.post", lambda *a, **k: R200BadJson()
    )
    out = llama_cpp_chat("http://h:1/", "m", "t", timeout=1.0)
    assert not out.ok
    assert "Invalid JSON" in (out.error or "")


def test_llama_cpp_chat_ok_minimal_choices(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.post",
        lambda *a, **k: _DummyResponse(200, {"choices": []}),
    )
    out = llama_cpp_chat("http://h:1/", "m", "t", timeout=1.0)
    assert out.ok and out.text == ""


def test_llama_cpp_chat_ok_malformed_choice_message(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.post",
        lambda *a, **k: _DummyResponse(200, {"choices": [{"message": "nope"}]}),
    )
    out = llama_cpp_chat("http://h:1/", "m", "t", timeout=1.0)
    assert out.ok
    assert out.text == ""


def test_llama_cpp_chat_ok_with_usage(monkeypatch) -> None:
    payload = {
        "choices": [{"message": {"content": "  hi  "}}],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 1,
            "total_tokens": 4,
        },
    }
    monkeypatch.setattr(
        "voxium.llama_cpp_client.requests.post",
        lambda *a, **k: _DummyResponse(200, payload),
    )
    out = llama_cpp_chat("http://h:1/", "m", "t", timeout=1.0)
    assert out.ok
    assert out.text == "hi"
    assert out.prompt_tokens == 3
    assert out.completion_tokens == 1
    assert out.total_tokens == 4
    assert out.raw_status == 200


def test_usage_int_invalid_value_returns_none() -> None:
    from voxium.llama_cpp_client import _usage_int

    assert _usage_int({"prompt_tokens": "nope"}, "prompt_tokens") is None
    assert _usage_int(None, "x") is None
    assert _usage_int({"prompt_tokens": None}, "prompt_tokens") is None
