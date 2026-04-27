"""rFFT standby strip (``standby_fft``)."""

import numpy as np

from voxium.standby_fft import (
    SPECTRUM_BARS,
    SPECTRUM_DISPLAY_WIDTH,
    get_spectrum_display_line,
    set_spectrum_from_mono_float,
    spectrum_line_for_tick,
    spectrum_rich_for_tick,
)


def test_flat_line_if_too_short() -> None:
    set_spectrum_from_mono_float(np.zeros(10, dtype=np.float32), 16_000)
    line = get_spectrum_display_line()
    assert len(line) == SPECTRUM_DISPLAY_WIDTH
    assert line == SPECTRUM_BARS[0] * SPECTRUM_DISPLAY_WIDTH


def test_sine_energy_peaks_toward_band() -> None:
    """1 kHz tone at 16 kHz rate should put most energy in the ~1k log band (not flat)."""
    sr = 16_000
    n = sr  # 1 s
    t = np.arange(n, dtype=np.float32) / float(sr)
    x = 0.4 * np.sin(2.0 * np.pi * 1_000.0 * t)
    set_spectrum_from_mono_float(x, sr)
    line = get_spectrum_display_line()
    assert len(line) == SPECTRUM_DISPLAY_WIDTH
    assert not all(c == SPECTRUM_BARS[0] for c in line)
    # Single-tone + window sidelobes: at least one strong bar after per-row normalization
    assert any(c in SPECTRUM_BARS[-3:] for c in line)


def test_silence_mostly_flat() -> None:
    set_spectrum_from_mono_float(np.zeros(8_000, dtype=np.float32), 16_000)
    line = get_spectrum_display_line()
    low = sum(1 for c in line if c == SPECTRUM_BARS[0])
    assert low >= SPECTRUM_DISPLAY_WIDTH // 2


def test_nonflat_spectrum_varies_with_tick() -> None:
    """Animation should change the bar row over time (same rFFT, phase modulation)."""
    sr = 16_000
    t = np.arange(8_000, dtype=np.float32) / float(sr)
    x = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)
    set_spectrum_from_mono_float(x, sr)
    seen = {spectrum_line_for_tick(k) for k in range(0, 300, 3)}
    assert len(seen) >= 3


def test_spectrum_rich_matches_width_and_animates() -> None:
    sr = 16_000
    t = np.arange(8_000, dtype=np.float32) / float(sr)
    x = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)
    set_spectrum_from_mono_float(x, sr)
    a = spectrum_rich_for_tick(0)
    b = spectrum_rich_for_tick(15)
    assert len(a.plain) == SPECTRUM_DISPLAY_WIDTH
    assert a.plain == spectrum_line_for_tick(0)
    assert b.plain == spectrum_line_for_tick(15)
    assert a.plain != b.plain
