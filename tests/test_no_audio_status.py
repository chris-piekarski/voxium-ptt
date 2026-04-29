"""Regression tests for the no-audio/no-speech UX indicator."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from voxium import app


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
