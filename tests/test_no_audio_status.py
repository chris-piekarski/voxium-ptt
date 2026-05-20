"""Regression tests for the no-audio/no-speech UX indicator."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from voxium import app


class _FakePortAudioError(Exception):
    """Stand-in for ``sounddevice.PortAudioError`` so tests don't need PortAudio."""


def _install_fake_sd_failing_input_stream(monkeypatch) -> None:
    """Replace ``app.sd`` so opening an ``InputStream`` raises PortAudioError.

    Mirrors the production failure mode where PortAudio reports no default
    input device (device index -1) — the path the new error branch handles.
    """

    def _raise(*_args, **_kwargs):
        raise _FakePortAudioError("no default input device")

    fake_sd = SimpleNamespace(
        InputStream=_raise,
        PortAudioError=_FakePortAudioError,
    )
    monkeypatch.setattr(app, "sd", fake_sd)


def test_transcribe_and_paste_shows_no_audio_status(monkeypatch) -> None:
    statuses: list[tuple[str, str]] = []
    rearmed: list[bool] = []

    app.config = SimpleNamespace(minimal=False, quiet=False)
    app.client_shutdown_event.clear()
    app.vox_pending_audio.clear()
    app.vox_stream = None
    app._set_input_mode("ptt")

    monkeypatch.setattr(app, "transcribe", lambda _audio: "")
    monkeypatch.setattr(app, "beep_error", lambda: None)
    monkeypatch.setattr(app, "set_terminal_title", lambda: None)
    monkeypatch.setattr(app, "get_transcript_history", lambda: None)
    monkeypatch.setattr(
        app,
        "show_status",
        lambda status, detail="": statuses.append((status, detail)),
    )
    monkeypatch.setattr(
        app, "_arm_status_after_log_scrollback", lambda: rearmed.append(True)
    )
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    app.transcribe_and_paste(np.zeros(app.SAMPLE_RATE, dtype=np.float32))

    assert statuses
    assert statuses[0][0] == app.STATUS_NO_AUDIO
    assert "No speech detected" in statuses[0][1]
    assert rearmed == [True]


def test_start_recording_returns_false_when_input_stream_fails(monkeypatch) -> None:
    """PortAudio failure during PTT capture must surface NO AUDIO, not crash the listener."""
    _install_fake_sd_failing_input_stream(monkeypatch)

    statuses: list[tuple[str, str]] = []
    close_calls: list[dict] = []
    log_calls: list[tuple[str, str]] = []

    app.config = SimpleNamespace(minimal=False, quiet=False, hotkey="ctrl+shift+space")
    monkeypatch.setattr(app, "stream", None, raising=False)
    monkeypatch.setattr(app, "recording_started_at", 0.0, raising=False)
    monkeypatch.setattr(app, "audio_chunks", [], raising=False)

    monkeypatch.setattr(app, "_stop_morse_audio", lambda: None)
    monkeypatch.setattr(app, "get_active_window", lambda: None)
    monkeypatch.setattr(app, "_maybe_open_streaming_session", lambda: None)
    monkeypatch.setattr(
        app,
        "_close_streaming_session",
        lambda *, graceful: close_calls.append({"graceful": graceful}),
    )
    monkeypatch.setattr(
        app, "cli_log", lambda message, level="info": log_calls.append((message, level))
    )
    monkeypatch.setattr(app, "beep_error", lambda: None)
    monkeypatch.setattr(app, "set_terminal_title", lambda: None)
    monkeypatch.setattr(
        app,
        "show_status",
        lambda status, detail="": statuses.append((status, detail)),
    )

    result = app.start_recording()

    assert result is False
    assert app.recording_started_at is None
    assert close_calls == [{"graceful": False}]
    assert statuses and statuses[-1][0] == app.STATUS_NO_AUDIO
    assert "No input device" in statuses[-1][1]
    # Operator must see the underlying reason in the log, with error severity.
    assert any(
        "Microphone unavailable" in msg and level == "error" for msg, level in log_calls
    )


def test_start_vox_listening_returns_false_when_input_stream_fails(monkeypatch) -> None:
    """PortAudio failure when arming VOX must clear vox state so the caller can fall back to PTT."""
    _install_fake_sd_failing_input_stream(monkeypatch)

    log_calls: list[tuple[str, str]] = []

    # Guard branch at the top of _start_vox_listening requires stream is None.
    monkeypatch.setattr(app, "stream", None, raising=False)
    monkeypatch.setattr(app, "vox_stream", object(), raising=False)
    monkeypatch.setattr(app, "vox_chunker", object(), raising=False)
    monkeypatch.setattr(app, "_stop_morse_audio", lambda: None)
    monkeypatch.setattr(
        app, "cli_log", lambda message, level="info": log_calls.append((message, level))
    )
    monkeypatch.setattr(app, "beep_error", lambda: None)

    result = app._start_vox_listening()

    assert result is False
    # vox state must be cleared so a subsequent attempt (or PTT fallback) starts clean.
    assert app.vox_stream is None
    assert app.vox_chunker is None
    assert any(
        "Microphone unavailable" in msg and level == "error" for msg, level in log_calls
    )
