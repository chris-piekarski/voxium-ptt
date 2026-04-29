"""ASGI contract tests for the local `/polish` endpoint."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from voxium.llama_cpp_client import LlamaCppChatResult
from voxium.polish_model_registry import DEFAULT_TRUSTED_POLISH_MODEL_ID

pytest.importorskip("ctranslate2", reason="Whisper server stack not installed")


def _prime_server(ws) -> None:
    ws.config = ws.ServerConfig(
        model="base",
        device="cpu",
        compute="int8",
        timeout=120,
        vad_enabled=True,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.25,
        polish_default_model="auto",
    )
    ws.stats = ws.ServerStats()
    ws.logger = ws.setup_logging("CRITICAL")
    ws.gpu_probe = ws.GpuProbe(False)
    ws._polish_semaphore = threading.BoundedSemaphore(1)


def test_polish_success_includes_effective_model_and_metrics(monkeypatch) -> None:
    from voxium import whisper_server as ws

    _prime_server(ws)
    monkeypatch.setattr(
        ws,
        "ensure_polish_model_downloaded",
        lambda **_kwargs: SimpleNamespace(
            name=DEFAULT_TRUSTED_POLISH_MODEL_ID,
            path="/tmp/plain.gguf",
        ),
    )
    monkeypatch.setattr(
        ws,
        "llama_cpp_chat",
        lambda *args, **kwargs: LlamaCppChatResult(
            ok=True,
            text="Hello, world.",
            error=None,
            seconds=0.42,
            prompt_tokens=10,
            completion_tokens=7,
            total_tokens=17,
            raw_status=200,
        ),
    )
    body = ws.polish_endpoint(ws.PolishRequestBody(text="hello world"))
    assert body["text"] == "Hello, world."
    assert body["text_raw"] == "hello world"
    assert body["polish"]["applied"] is True
    assert body["polish"]["model"] == DEFAULT_TRUSTED_POLISH_MODEL_ID
    assert body["metrics"]["polish"]["model"] == DEFAULT_TRUSTED_POLISH_MODEL_ID
    assert body["metrics"]["polish"]["backend"] == "llama.cpp"
    assert body["metrics"]["polish"]["total_tokens"] == 17
    assert body["polish"]["tokens_in"] == 10
    assert body["polish"]["tokens_out"] == 7
    assert body["polish"]["total_tokens"] == 17
    assert "prepare_seconds" in body["polish"] and "handler_seconds" in body["polish"]
    assert (
        body["metrics"]["polish"]["prepare_seconds"]
        == body["polish"]["prepare_seconds"]
    )
    assert (
        body["metrics"]["polish"]["handler_seconds"]
        == body["polish"]["handler_seconds"]
    )


def test_polish_failure_falls_back_to_raw_text(monkeypatch) -> None:
    from voxium import whisper_server as ws

    _prime_server(ws)
    monkeypatch.setattr(
        ws,
        "ensure_polish_model_downloaded",
        lambda **_kwargs: SimpleNamespace(
            name=DEFAULT_TRUSTED_POLISH_MODEL_ID,
            path="/tmp/plain.gguf",
        ),
    )
    monkeypatch.setattr(
        ws,
        "llama_cpp_chat",
        lambda *args, **kwargs: LlamaCppChatResult(
            ok=False,
            text="",
            error="backend timeout",
            seconds=1.25,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            raw_status=504,
        ),
    )
    body = ws.polish_endpoint(ws.PolishRequestBody(text="raw copy"))
    assert body["text"] == "raw copy"
    assert body["text_raw"] == "raw copy"
    assert body["polish"]["applied"] is False
    assert body["polish"]["error"] == "backend timeout"
    assert body["metrics"]["polish"]["model"] == DEFAULT_TRUSTED_POLISH_MODEL_ID


def test_polish_saturation_returns_503() -> None:
    from voxium import whisper_server as ws

    class _SaturatedSemaphore:
        def acquire(self, blocking: bool = True) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            raise AssertionError("release should not be called when acquire fails")

    _prime_server(ws)
    ws.ensure_polish_model_downloaded = lambda **_kwargs: SimpleNamespace(
        name=DEFAULT_TRUSTED_POLISH_MODEL_ID,
        path="/tmp/plain.gguf",
    )
    ws._polish_semaphore = _SaturatedSemaphore()
    r = ws.polish_endpoint(ws.PolishRequestBody(text="raw copy"))
    assert r.status_code == 503
    body = r.body.decode("utf-8")
    assert "polish_saturated" in body


def test_polish_rejects_unknown_backend() -> None:
    from voxium import whisper_server as ws

    _prime_server(ws)
    with pytest.raises(ws.HTTPException) as excinfo:
        ws.polish_endpoint(ws.PolishRequestBody(text="raw copy", backend="ollama"))
    assert excinfo.value.status_code == 400
