import math

import numpy as np

from voxium.recording_ui import (
    build_recording_hud_rich,
    colored_mono_waveform_text,
    format_recording_hud,
    format_recording_hud_minimal,
    rms_to_dbfs,
    WAVEFORM_BARS,
)


def test_rms_to_dbfs() -> None:
    assert rms_to_dbfs(0) == -100.0
    assert rms_to_dbfs(-1) == -100.0
    assert rms_to_dbfs(float("nan")) == -100.0
    d = rms_to_dbfs(0.1)
    assert d < 0.0
    assert math.isfinite(d)


def test_rms_to_dbfs_log10_valueerror_returns_floor(monkeypatch) -> None:
    import voxium.recording_ui as ru

    def _boom(_x: float) -> float:
        raise ValueError

    monkeypatch.setattr(ru.math, "log10", _boom)
    assert rms_to_dbfs(0.5) == -100.0


def test_format_recording_hud_contains_dur() -> None:
    s = format_recording_hud(48000, 0.1, 0.5, 3, 48000, 12.0)
    assert "1.0" in s or "48000" in s
    assert "dBFS" in s
    assert "12" in s and "2 blips" in s


def test_format_minimal_capped() -> None:
    s = format_recording_hud_minimal(4800, 0.01, 0.1, 2, 48000, 3.0)
    assert len(s) <= 40
    assert "REC" in s


def test_format_hud_uses_min_sample_rate() -> None:
    s = format_recording_hud(100, 0.0, 0.0, 0, 0, None)
    assert "100" in s and "dBFS" in s


def test_format_minimal_omits_reminder_when_very_large() -> None:
    s = format_recording_hud_minimal(100, 0.0, 0.0, 0, 48000, 20_000.0)
    assert "REC" in s
    assert "~" not in s


def test_colored_mono_waveform_short_buffer() -> None:
    t = np.array([0.1], dtype=np.float32)
    w = colored_mono_waveform_text(t, 40, peak_ref=0.1)
    assert "·" in str(w) or w.plain


def test_colored_mono_waveform_uses_level_bars() -> None:
    t = 0.2 * np.sin(np.linspace(0, 4 * np.pi, 8_000)).astype(np.float32)
    w = colored_mono_waveform_text(t, 32, peak_ref=0.2)
    s = w.plain
    assert any(c in s for c in WAVEFORM_BARS)
    assert "\n" in s
    assert len(s.replace("\n", "")) >= 32
    assert w.spans


def test_colored_mono_waveform_uses_many_palette_spans() -> None:
    t = np.linspace(-1.0, 1.0, 16_000, dtype=np.float32)
    w = colored_mono_waveform_text(t, 48, peak_ref=1.0)
    styles = {span.style for span in w.spans if span.style}
    assert len(styles) >= 8


def test_build_recording_hud_rich_group() -> None:
    tail = np.sin(np.linspace(0, 2, 800, dtype=np.float32)) * 0.1
    g = build_recording_hud_rich(
        800, 0.1, 0.2, 1, 16000, 10.0, tail, panel_inner_width=50
    )
    assert g.renderables
