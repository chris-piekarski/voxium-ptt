import math

import numpy as np

from voxium.recording_ui import (
    colored_mono_waveform_text,
    format_recording_hud,
    format_recording_hud_minimal,
    rms_to_dbfs,
    WAVEFORM_BARS,
)


def test_rms_to_dbfs() -> None:
    assert rms_to_dbfs(0) == -100.0
    assert rms_to_dbfs(-1) == -100.0
    d = rms_to_dbfs(0.1)
    assert d < 0.0
    assert math.isfinite(d)


def test_format_recording_hud_contains_dur() -> None:
    s = format_recording_hud(48000, 0.1, 0.5, 3, 48000, 12.0)
    assert "1.0" in s or "48000" in s
    assert "dBFS" in s
    assert "12" in s or "reminder" in s


def test_format_minimal_capped() -> None:
    s = format_recording_hud_minimal(4800, 0.01, 0.1, 2, 48000, 3.0)
    assert len(s) <= 40
    assert "REC" in s


def test_colored_mono_waveform_uses_level_bars() -> None:
    t = 0.2 * np.sin(np.linspace(0, 4 * np.pi, 8_000)).astype(np.float32)
    w = colored_mono_waveform_text(t, 32, peak_ref=0.2)
    s = str(w)
    assert any(c in s for c in WAVEFORM_BARS)
    assert len(s) >= 32
