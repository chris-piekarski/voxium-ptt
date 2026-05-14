"""
Wire-protocol and route-registration tests for ``/transcribe-stream``.

These tests stub the WhisperModel via ``get_model`` so the FastAPI route can run
without loading a real CTranslate2 model. They cover:

- ``session_open`` payload shape and pinned protocol version
- happy-path ``partial`` after a binary audio frame
- ``end`` flush → final ``partial`` with ``is_final=True``
- malformed audio frame (odd byte length) → ``error code=invalid_frame``
- unknown control message → ``error code=unknown_message_type``
- loopback enforcement: non-loopback origin → close code 1008
- ``_register_streaming_routes`` is gated by the env / config kill switch
"""

from __future__ import annotations

# pylint: disable=redefined-outer-name  # standard pytest fixture parameter pattern

import logging

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voxium import whisper_server
from voxium.transcribe_stream import PROTOCOL_VERSION

SAMPLE_RATE = 16_000


@pytest.fixture(autouse=True)
def _ensure_logger(monkeypatch):
    """Tests don't go through ``main()`` so ``whisper_server.logger`` is None by default."""
    if whisper_server.logger is None:
        monkeypatch.setattr(
            whisper_server, "logger", logging.getLogger("voxium_whisper_server_test")
        )


class _StubModel:
    """Minimal WhisperModel stand-in returning a deterministic transcript."""

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, **_kwargs):
        self.calls += 1

        class _Seg:
            def __init__(self, text: str) -> None:
                self.text = text

        return ([_Seg(text=f"call-{self.calls}")], object())


@pytest.fixture
def streaming_app(monkeypatch):
    """
    A FastAPI app with the streaming routes wired in, but the model load short-circuited
    to a stub that does not need CUDA / faster-whisper.
    """
    monkeypatch.setattr(whisper_server, "get_model", lambda *a, **k: _StubModel())
    monkeypatch.setattr(whisper_server, "is_loopback_host", lambda h: True)

    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=True,
        streaming_max_concurrent=4,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)
    monkeypatch.setattr(whisper_server, "_streaming_open_sessions", 0)

    app = FastAPI()
    assert whisper_server._register_streaming_routes(app) is True
    return app


def _silence_bytes(seconds: float) -> bytes:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32).tobytes()


def test_session_open_payload_shape(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            opened = ws.receive_json()
            ws.send_json({"type": "end"})
            # Drain to graceful close
            try:
                while True:
                    ws.receive_json()
            except Exception:
                pass
    assert opened["type"] == "session_open"
    assert opened["version"] == PROTOCOL_VERSION
    assert opened["sample_rate"] == SAMPLE_RATE
    assert opened["channels"] == 1
    assert opened["dtype"] == "float32"
    assert opened["byte_order"] == "little"
    assert opened["window_seconds"] == pytest.approx(5.0)
    assert "session_id" in opened


def test_partial_after_audio_frame(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            ws.receive_json()  # session_open
            ws.send_bytes(_silence_bytes(0.5))
            partial = ws.receive_json()
            ws.send_json({"type": "end"})
            try:
                while True:
                    ws.receive_json()
            except Exception:
                pass
    assert partial["type"] == "partial"
    assert partial["seq"] == 1
    assert partial["is_final"] is False
    assert partial["audio_seconds"] == pytest.approx(0.5)
    assert isinstance(partial["text"], str)


def test_end_flush_emits_final_partial(streaming_app) -> None:
    final_partial = None
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            ws.receive_json()  # session_open
            ws.send_bytes(_silence_bytes(0.5))
            ws.receive_json()  # partial
            ws.send_json({"type": "end"})
            # The next non-keepalive partial should be the final one.
            for _ in range(5):
                msg = ws.receive_json()
                if msg.get("type") == "partial" and msg.get("is_final"):
                    final_partial = msg
                    break
    assert final_partial is not None
    assert final_partial["is_final"] is True


def test_invalid_frame_byte_length(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            ws.receive_json()  # session_open
            # 5 bytes is not a multiple of 4 (float32)
            ws.send_bytes(b"\x00\x00\x00\x00\x00")
            err = ws.receive_json()
    assert err["type"] == "error"
    assert err["code"] == "invalid_frame"


def test_unknown_message_type_rejected(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            ws.receive_json()  # session_open
            ws.send_json({"type": "no-such-type"})
            err = ws.receive_json()
    assert err["type"] == "error"
    assert err["code"] == "unknown_message_type"


def test_invalid_json_text_frame_rejected(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        with client.websocket_connect("/transcribe-stream") as ws:
            ws.receive_json()  # session_open
            ws.send_text("not-json{")
            err = ws.receive_json()
    assert err["type"] == "error"
    assert err["code"] == "invalid_json"


def test_loopback_gate_rejects_non_loopback(monkeypatch) -> None:
    """When the loopback gate is active and client is not loopback, WS closes with 1008."""
    monkeypatch.setattr(whisper_server, "get_model", lambda *a, **k: _StubModel())
    monkeypatch.setattr(whisper_server, "is_loopback_host", lambda h: False)
    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=True,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)
    monkeypatch.setattr(whisper_server, "_streaming_open_sessions", 0)

    app = FastAPI()
    whisper_server._register_streaming_routes(app)

    with TestClient(app) as client:
        with pytest.raises(Exception) as excinfo:
            with client.websocket_connect("/transcribe-stream") as ws:
                ws.receive_json()
        # Starlette's TestClient raises WebSocketDisconnect on a server-initiated close.
        text = repr(excinfo.value)
        assert "1008" in text or "WebSocketDisconnect" in text


def test_register_streaming_routes_skipped_when_env_disabled(monkeypatch) -> None:
    monkeypatch.setenv("VOXIUM_STREAM_ENDPOINT_ENABLED", "false")
    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=True,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)
    app = FastAPI()
    assert whisper_server._register_streaming_routes(app) is False
    # Route really wasn't added.
    paths = {r.path for r in app.routes}
    assert "/transcribe-stream" not in paths
    assert "/transcribe-stream/stats" not in paths


def test_register_streaming_routes_skipped_when_config_disabled(monkeypatch) -> None:
    monkeypatch.delenv("VOXIUM_STREAM_ENDPOINT_ENABLED", raising=False)
    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=False,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)
    app = FastAPI()
    assert whisper_server._register_streaming_routes(app) is False
    paths = {r.path for r in app.routes}
    assert "/transcribe-stream" not in paths


def test_concurrency_gate_rejects_extra_session(monkeypatch) -> None:
    monkeypatch.setattr(whisper_server, "get_model", lambda *a, **k: _StubModel())
    monkeypatch.setattr(whisper_server, "is_loopback_host", lambda h: True)
    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=True,
        streaming_max_concurrent=1,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)
    monkeypatch.setattr(whisper_server, "_streaming_open_sessions", 0)
    app = FastAPI()
    whisper_server._register_streaming_routes(app)

    with TestClient(app) as client:
        with client.websocket_connect("/transcribe-stream") as ws_first:
            ws_first.receive_json()  # session_open
            # Second session should be capped — server emits error then closes.
            with client.websocket_connect("/transcribe-stream") as ws_second:
                msg = ws_second.receive_json()
                assert msg["type"] == "error"
                assert msg["code"] == "max_sessions"


def test_health_includes_streaming_fields(monkeypatch) -> None:
    monkeypatch.setattr(whisper_server, "get_model", lambda *a, **k: _StubModel())
    monkeypatch.setattr(whisper_server, "is_loopback_host", lambda h: True)
    fake_config = whisper_server.ServerConfig(
        model="small.en",
        device="cpu",
        compute="int8",
        timeout=60,
        vad_enabled=False,
        host="127.0.0.1",
        port=8002,
        gpu_metrics_enabled=False,
        metrics_sample_interval=0.5,
        streaming_endpoint_enabled=True,
    )
    monkeypatch.setattr(whisper_server, "config", fake_config)

    # Stub external probes the /health route uses.
    monkeypatch.setattr(whisper_server, "gpu_probe", None)
    monkeypatch.setattr(
        whisper_server, "llama_cpp_reachable", lambda *a, **k: (False, "stubbed")
    )
    monkeypatch.setattr(whisper_server, "llama_cpp_loaded_model", lambda *a, **k: None)
    monkeypatch.setattr(
        whisper_server,
        "get_actual_device",
        lambda: {"device": "cpu", "cuda_available": False, "cuda_device_count": 0},
    )
    monkeypatch.setattr(
        whisper_server, "faster_whisper_distribution_info", {"version": "stub"}
    )

    body = whisper_server.health()
    assert "streaming_enabled" in body
    assert body["streaming_enabled"] is True
    assert body["streaming_protocol_version"] == PROTOCOL_VERSION
    assert body["streaming_max_concurrent"] == 4
    assert body["streaming_open_sessions"] >= 0


def test_stream_stats_endpoint(streaming_app) -> None:
    with TestClient(streaming_app) as client:
        resp = client.get("/transcribe-stream/stats")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "open_sessions",
        "total_sessions",
        "total_decodes",
        "frames_received",
        "max_concurrent",
        "enabled",
    ):
        assert key in body
