"""ASGI contract test: /health (no model load, minimal global init)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
    with TestClient(ws.app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model"] == "base"
    assert "device" in body
