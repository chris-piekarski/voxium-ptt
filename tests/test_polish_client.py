"""Client-side tests for the optional `/polish` step."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from voxium import app
from voxium.llama_cpp_daemon import ManagedLlamaCpp
from voxium.polish_model_registry import DEFAULT_TRUSTED_POLISH_MODEL_ID
from voxium.slash_commands import SlashLineResult


class _DummyResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _set_config(**overrides):
    defaults = {
        "polish": True,
        "minimal": True,
        "server_url": "http://127.0.0.1:8002/transcribe",
        "polish_model": "auto",
        "polish_timeout": 5.0,
        "llama_cpp_auto_start": False,
        "llama_cpp_url": "http://127.0.0.1:11435",
        "polish_keep_alive": "10m",
        "llama_cpp_gpu_layers": "auto",
        "llama_cpp_ctx_size": 0,
        "llama_cpp_cmd": "",
        "polish_max_concurrent": 2,
        "hotkey": "f9",
        "recovery_hotkey": "f8",
        "retry_hotkey": "f6",
        "mode_hotkey": "f7",
        "file_config": {},
    }
    defaults.update(overrides)
    app.config = SimpleNamespace(**defaults)


def test_maybe_polish_transcript_merges_success_metrics(monkeypatch) -> None:
    _set_config()
    app.last_transcription_metrics = {"model": {"name": "base"}}
    monkeypatch.setattr("voxium.app.ensure_llama_cpp_for_polish", lambda: None)
    monkeypatch.setattr(
        "voxium.app.requests.post",
        lambda *args, **kwargs: _DummyResponse(
            200,
            {
                "text": "Hello, world.",
                "polish": {
                    "enabled": True,
                    "attempted": True,
                    "applied": True,
                    "model": DEFAULT_TRUSTED_POLISH_MODEL_ID,
                    "backend": "llama.cpp",
                    "seconds": 0.42,
                    "error": None,
                },
                "metrics": {
                    "polish": {
                        "model": DEFAULT_TRUSTED_POLISH_MODEL_ID,
                        "backend": "llama.cpp",
                        "seconds": 0.42,
                    }
                },
            },
        ),
    )

    out = app.maybe_polish_transcript("hello world")

    assert out == "Hello, world."
    assert app.last_transcription_metrics is not None
    assert (
        app.last_transcription_metrics["polish"]["model"]
        == DEFAULT_TRUSTED_POLISH_MODEL_ID
    )
    assert app.last_transcription_metrics["polish"]["applied"] is True


def test_maybe_polish_transcript_ensures_runtime_before_request(monkeypatch) -> None:
    _set_config()
    calls: list[bool] = []
    app.last_transcription_metrics = {"model": {"name": "base"}}
    monkeypatch.setattr(
        "voxium.app.ensure_llama_cpp_for_polish",
        lambda: calls.append(True),
    )
    monkeypatch.setattr(
        "voxium.app.requests.post",
        lambda *args, **kwargs: _DummyResponse(200, {"text": "copy"}),
    )

    out = app.maybe_polish_transcript("raw copy")

    assert out == "copy"
    assert calls == [True]


def test_ensure_llama_cpp_for_polish_force_restarts_owned_daemon(
    monkeypatch, tmp_path
) -> None:
    _set_config(llama_cpp_auto_start=True)
    app._llama_cpp_polish_ready_checked = True
    app.managed_llama_cpp = ManagedLlamaCpp(process=object(), started_by_voxium=True)
    stopped: list[object] = []
    ensured: list[dict] = []

    def fake_stop(managed) -> None:
        stopped.append(managed)

    monkeypatch.setattr("voxium.app.stop_managed_llama_cpp", fake_stop)
    monkeypatch.setattr(
        "voxium.app.ensure_polish_model_downloaded",
        lambda **_kwargs: SimpleNamespace(
            name=DEFAULT_TRUSTED_POLISH_MODEL_ID,
            path=Path(tmp_path / "plain.gguf"),
        ),
    )

    def fake_ensure(**kwargs):
        ensured.append(kwargs)
        return None, [("ready", "info")]

    monkeypatch.setattr("voxium.app.ensure_llama_cpp_daemon", fake_ensure)
    monkeypatch.setattr(
        "voxium.app.llama_cpp_reachable", lambda *_args, **_kwargs: (True, None)
    )
    monkeypatch.setattr("voxium.app.cli_log", lambda *_args, **_kwargs: None)

    app.ensure_llama_cpp_for_polish(force=True)

    assert len(stopped) == 1
    assert app.managed_llama_cpp is None
    assert ensured and ensured[0]["base_url"] == "http://127.0.0.1:11435"
    assert ensured[0]["model_alias"] == DEFAULT_TRUSTED_POLISH_MODEL_ID


def test_ensure_llama_cpp_for_polish_skips_when_already_checked(monkeypatch) -> None:
    _set_config(llama_cpp_auto_start=True)
    app._llama_cpp_polish_ready_checked = True
    calls: list[bool] = []
    monkeypatch.setattr(
        "voxium.app.ensure_llama_cpp_daemon",
        lambda **_kwargs: calls.append(True),
    )

    app.ensure_llama_cpp_for_polish()

    assert calls == []


def test_ensure_llama_cpp_for_polish_keeps_retry_enabled_when_not_reachable(
    monkeypatch,
    tmp_path,
) -> None:
    _set_config(llama_cpp_auto_start=True)
    app._llama_cpp_polish_ready_checked = False
    monkeypatch.setattr(
        "voxium.app.ensure_polish_model_downloaded",
        lambda **_kwargs: SimpleNamespace(
            name=DEFAULT_TRUSTED_POLISH_MODEL_ID,
            path=Path(tmp_path / "plain.gguf"),
        ),
    )
    monkeypatch.setattr(
        "voxium.app.ensure_llama_cpp_daemon",
        lambda **_kwargs: (None, [("offline", "warning")]),
    )
    monkeypatch.setattr(
        "voxium.app.llama_cpp_reachable",
        lambda *_args, **_kwargs: (False, "offline"),
    )
    monkeypatch.setattr("voxium.app.cli_log", lambda *_args, **_kwargs: None)

    app.ensure_llama_cpp_for_polish()

    assert app._llama_cpp_polish_ready_checked is False


def test_ensure_llama_cpp_for_polish_marks_checked_when_existing_daemon_is_reachable(
    monkeypatch,
) -> None:
    _set_config(llama_cpp_auto_start=False)
    app._llama_cpp_polish_ready_checked = False
    monkeypatch.setattr(
        "voxium.app.llama_cpp_reachable",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr("voxium.app.cli_log", lambda *_args, **_kwargs: None)

    app.ensure_llama_cpp_for_polish()

    assert app._llama_cpp_polish_ready_checked is True


def test_apply_slash_runtime_changes_enables_polish_and_retries(monkeypatch) -> None:
    _set_config(polish=False, minimal=False)
    app._llama_cpp_polish_ready_checked = True
    ensured: list[tuple[bool, bool]] = []
    flushed: list[bool] = []

    def fake_ensure(*, force: bool = False, **kwargs) -> None:
        ensured.append((force, app._llama_cpp_polish_ready_checked))

    monkeypatch.setattr("voxium.app.ensure_llama_cpp_for_polish", fake_ensure)
    monkeypatch.setattr(
        "voxium.app.flush_client_telemetry_block",
        lambda *args, **kwargs: flushed.append(True),
    )

    app.apply_slash_runtime_changes(SlashLineResult(text="ok", polish_enabled=True))

    assert app.config.polish is True
    assert ensured == [(False, False)]
    assert flushed == [True]


def test_apply_slash_runtime_changes_model_change_forces_refresh(monkeypatch) -> None:
    _set_config(polish=True, minimal=False)
    app._llama_cpp_polish_ready_checked = True
    ensured: list[tuple[bool, bool, str | None]] = []
    flushed: list[bool] = []

    def fake_ensure(*, force: bool = False, **kwargs) -> None:
        ensured.append(
            (force, app._llama_cpp_polish_ready_checked, app.config.polish_model)
        )

    monkeypatch.setattr("voxium.app.ensure_llama_cpp_for_polish", fake_ensure)
    monkeypatch.setattr(
        "voxium.app.flush_client_telemetry_block",
        lambda *args, **kwargs: flushed.append(True),
    )

    app.apply_slash_runtime_changes(
        SlashLineResult(text="ok", polish_model="qwen2.5-3b-q4km")
    )

    assert app.config.polish_model == "qwen2.5-3b-q4km"
    assert ensured == [(True, False, "qwen2.5-3b-q4km")]
    assert flushed == [True]


def test_apply_slash_runtime_changes_persists_hotkeys(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "transcription:\n  model: tiny\nhotkeys:\n  mode: f7\n",
        encoding="utf-8",
    )
    _set_config(file_config={"hotkeys": {"mode": "f7"}}, minimal=True)
    app.ptt_status_box = None
    monkeypatch.setattr(app, "CONFIG_PATH", cfg)

    app.apply_slash_runtime_changes(
        SlashLineResult(text="ok", hotkeys={"record": "f10", "recovery": "f11"})
    )

    assert app.config.hotkey == "f10"
    assert app.config.recovery_hotkey == "f11"
    assert app.config.file_config["hotkeys"]["record"] == "f10"
    assert app.config.file_config["hotkeys"]["recovery"] == "f11"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["transcription"]["model"] == "tiny"
    assert data["hotkeys"] == {"mode": "f7", "record": "f10", "recovery": "f11"}


def test_transcribe_and_paste_records_persistent_stats_source(monkeypatch) -> None:
    _set_config(minimal=True, ux_chatter=False)
    app.state = app.State.IDLE
    app.vox_pending_audio.clear()
    metrics = {"audio_seconds": 1.5, "model": {"decoder_tokens": 10}}
    recorded: list[tuple[dict | None, str]] = []

    def fake_transcribe(_audio):
        app.last_transcription_metrics = metrics
        return "copy that"

    monkeypatch.setattr(app, "transcribe", fake_transcribe)
    monkeypatch.setattr(
        app,
        "_record_persistent_stats",
        lambda got_metrics, *, source: recorded.append((got_metrics, source)),
    )
    monkeypatch.setattr(app, "is_client_shutting_down", lambda: False)
    monkeypatch.setattr(app, "is_hallucination", lambda _text: False)
    monkeypatch.setattr(app, "set_spectrum_from_mono_float", lambda *_args: None)
    monkeypatch.setattr(app, "get_transcript_history", lambda: None)
    monkeypatch.setattr(app, "paste_text", lambda _text: None)
    monkeypatch.setattr(app, "beep_success", lambda: None)
    monkeypatch.setattr(app, "set_terminal_title", lambda: None)
    monkeypatch.setattr(app, "take_readback", lambda: "readback")
    monkeypatch.setattr(app, "show_status", lambda *_args: None)
    monkeypatch.setattr(app, "log_transcription_summary", lambda *_args: None)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    app.transcribe_and_paste(object(), source="vox")

    assert recorded == [(metrics, "vox")]


def test_maybe_polish_transcript_records_fallback_metrics_on_http_error(
    monkeypatch,
) -> None:
    _set_config()
    app.last_transcription_metrics = {"model": {"name": "base"}}
    monkeypatch.setattr("voxium.app.ensure_llama_cpp_for_polish", lambda: None)
    monkeypatch.setattr(
        "voxium.app.requests.post",
        lambda *args, **kwargs: _DummyResponse(
            503,
            {"error": "polish_saturated", "detail": "busy"},
        ),
    )

    out = app.maybe_polish_transcript("raw copy")

    assert out == "raw copy"
    assert app.last_transcription_metrics is not None
    assert app.last_transcription_metrics["polish"]["backend"] == "llama.cpp"
    assert app.last_transcription_metrics["polish"]["applied"] is False
    assert "HTTP 503" in str(app.last_transcription_metrics["polish"]["error"])


def test_maybe_polish_transcript_records_fallback_metrics_on_request_exception(
    monkeypatch,
) -> None:
    _set_config()
    app.last_transcription_metrics = {"model": {"name": "base"}}
    monkeypatch.setattr("voxium.app.ensure_llama_cpp_for_polish", lambda: None)

    def fake_post(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("voxium.app.requests.post", fake_post)

    out = app.maybe_polish_transcript("raw copy")

    assert out == "raw copy"
    assert app.last_transcription_metrics is not None
    assert app.last_transcription_metrics["polish"]["backend"] == "llama.cpp"
    assert app.last_transcription_metrics["polish"]["applied"] is False
    assert "RuntimeError" in str(app.last_transcription_metrics["polish"]["error"])
