"""ASGI contract test: /health (no model load, minimal global init)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

# Heavy imports: ctranslate2, etc.
pytest.importorskip("ctranslate2", reason="Whisper server stack not installed")


def test_health_returns_ok_json() -> None:
    from voxium import whisper_server as ws

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
    )
    ws.stats = ws.ServerStats()
    ws.logger = ws.setup_logging("CRITICAL")
    ws.gpu_probe = ws.GpuProbe(False)
    body = ws.health()
    assert body["status"] == "ok"
    assert body["model"] == "base"
    assert "device" in body
    assert body["polish_backend_default"] == "llama.cpp"
    assert body["polish_default_model"] == "auto"
    assert body["polish_enabled_default"] is True
    assert "polish_keep_alive_default" in body
    assert "polish_llama_cpp_reachable" in body
    assert "polish_loaded_model" in body
    assert "polish_model_loaded" in body


def test_ensure_model_rejects_bad_id() -> None:
    from voxium import whisper_server as ws

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
    )
    ws.stats = ws.ServerStats()
    ws.logger = ws.setup_logging("CRITICAL")
    ws.gpu_probe = ws.GpuProbe(False)
    with pytest.raises(HTTPException) as excinfo:
        ws.ensure_model_start(ws.EnsureModelBody(model="not-a-valid-model-name"))
    assert excinfo.value.status_code == 400
