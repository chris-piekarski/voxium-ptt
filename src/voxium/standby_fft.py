"""
Real rFFT spectrum of the last successful PTT/STT take — Unicode block bar row for standby.

Hann window, log-spaced energy bands, normalized magnitudes (stored), then :func:`spectrum_line_for_tick`
re-renders each standby tick with stronger motion (roll + modulation + gamma) while preserving the
capture’s spectral *shape* — display-only; no live mic FFT in standby.
Thread-safe; updated when a decode is accepted (same path as :func:`voxium.app.get_transcript_history`).
"""

from __future__ import annotations

import threading
from typing import Final

import numpy as np
import numpy.typing as npt
from rich.text import Text

from voxium.recording_ui import rgb_hex_for_level

# Match standby copy / terminal width: wider strip than the old 12 placeholder.
SPECTRUM_DISPLAY_WIDTH: Final[int] = 32
SPECTRUM_BARS: Final[str] = "▁▂▃▄▅▆▇"
_MAX_FFT_SAMPLES: Final[int] = 240_000
_F_LO_HZ: Final[float] = 80.0
_F_HI_MAX_HZ: Final[float] = 7_500.0

_lock = threading.Lock()
# Normalized log-band magnitudes 0..1; drives animated bar row.
_last_spectrum_mags: np.ndarray | None = None


def reset_spectrum_state() -> None:
    """Clear stored band magnitudes (e.g. tests, or a fresh process)."""
    global _last_spectrum_mags
    with _lock:
        _last_spectrum_mags = None


def get_spectrum_display_line() -> str:
    """Snapshot at animation tick 0 (no phase); see :func:`spectrum_line_for_tick`."""
    return spectrum_line_for_tick(0)


def spectrum_line_for_tick(tick: int) -> str:
    """
    Bar row for standby refresh *tick* — same **stored** log-band rFFT (last good take), re-mapped
    with strong phase motion so the strip reads *alive* on-station. Not a re-capture (no live mic
    in standby); animation is display-only on the last on-wire snapshot.
    """
    with _lock:
        m = None if _last_spectrum_mags is None else _last_spectrum_mags.copy()
    if m is None or m.size != SPECTRUM_DISPLAY_WIDTH:
        return SPECTRUM_BARS[0] * SPECTRUM_DISPLAY_WIDTH
    return _render_animated_mags(m, int(tick))


def _band_levels_for_tick(m: np.ndarray, tick: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Same animation as the plain bar row: *v* = per-band display level 0..1, *idx* = 0..6 bar index.
    """
    t = float(tick)
    i = np.arange(SPECTRUM_DISPLAY_WIDTH, dtype=np.float64)
    base = m.astype(np.float64, copy=False)
    shift = int(np.floor(2.0 * np.sin(0.14 * t)))
    m_work = np.roll(base, shift, axis=0)
    p1 = 0.50 + 0.50 * (0.5 + 0.5 * np.sin(0.30 * t + 0.22 * i))
    p2 = 0.55 + 0.45 * (0.5 + 0.5 * np.sin(0.48 * t - 0.32 * i))
    p3 = 0.10 * (0.5 + 0.5 * np.sin(0.72 * t + 0.40 * i))
    v = np.clip(m_work * p1 * p2 + p3, 0.0, 1.0)
    v = np.clip(np.power(np.maximum(v, 1.0e-9), 0.88), 0.0, 1.0)
    idx = np.floor(v * 6.999).astype(np.int32)
    idx = np.clip(idx, 0, 6)
    return v, idx


def _render_animated_mags(m: np.ndarray, tick: int) -> str:
    """
    Animate the normalized band vector *m* (0..1) for the 7-level Unicode bar strip.

    The prior pulse/shimmer kept multipliers near ~0.8–1.0, so glyphs rarely changed; the standby
    thread also ticked slowly. Wider gain, a slow :func:`np.roll` of the same band vector, a fast
    phase in *tick*, an additive travelling wave, and a mild γ push more frames across level
    boundaries while the underlying rFFT profile remains recognizable.
    """
    v, idx = _band_levels_for_tick(m, tick)
    return "".join(SPECTRUM_BARS[j] for j in idx)


def spectrum_rich_for_tick(tick: int) -> Text:
    """
    Same motion as :func:`spectrum_line_for_tick`, with **per-bar** green→amber→red (live HUD cue).

    Display-only summary of the last on-wire spectrum, not a new capture.
    """
    with _lock:
        m = None if _last_spectrum_mags is None else _last_spectrum_mags.copy()
    dim = "dim #4b5563"
    if m is None or m.size != SPECTRUM_DISPLAY_WIDTH:
        row = Text()
        for _ in range(SPECTRUM_DISPLAY_WIDTH):
            row.append(SPECTRUM_BARS[0], style=dim)
        return row
    v, idx = _band_levels_for_tick(m, int(tick))
    tf = float(tick)
    tcol = np.arange(SPECTRUM_DISPLAY_WIDTH, dtype=np.float64)
    cphase = 0.5 + 0.5 * np.sin(0.28 * tf + 0.19 * tcol)
    out = Text()
    for j in range(SPECTRUM_DISPLAY_WIDTH):
        ch = SPECTRUM_BARS[idx[j]]
        tlev = float(np.clip(0.78 * v[j] + 0.22 * cphase[j], 0.0, 1.0))
        out.append(ch, style=rgb_hex_for_level(tlev))
    return out


def set_spectrum_from_mono_float(audio: np.ndarray, sample_rate: int) -> None:
    """
    Compute a fixed-width bar string from *mono* float audio ``[-1, 1]`` and store for standby.

    Called for the same buffer that was sent to the transcriber on a successful VOX take.
    """
    global _last_spectrum_mags

    sr = int(sample_rate) if sample_rate > 0 else 1
    x: npt.NDArray[np.float64] = np.asarray(audio, dtype=np.float64).ravel()
    n = int(x.size)
    zflat = np.zeros(SPECTRUM_DISPLAY_WIDTH, dtype=np.float64)
    if n < 64:
        with _lock:
            _last_spectrum_mags = zflat.copy()
        return

    if n > _MAX_FFT_SAMPLES:
        x = x[-_MAX_FFT_SAMPLES:]

    x = x - np.mean(x)
    w = np.hanning(x.shape[0])
    xw = x * w
    spec = np.abs(np.fft.rfft(xw))
    freqs = np.fft.rfftfreq(xw.shape[0], 1.0 / float(sr))
    f_hi = min(_F_HI_MAX_HZ, 0.499 * float(sr))
    f_lo = _F_LO_HZ

    # Ignore DC; ignore bins outside speech band
    mags, _edges = _aggregate_bands_log(spec, freqs, f_lo, f_hi, SPECTRUM_DISPLAY_WIDTH)
    mags = np.log1p(mags)
    rng = float(mags.max() - mags.min())
    if rng < 1e-15 or not np.isfinite(rng):
        m_norm = np.zeros(SPECTRUM_DISPLAY_WIDTH, dtype=np.float64)
    else:
        m_norm = (mags - mags.min()) / rng

    with _lock:
        _last_spectrum_mags = m_norm.astype(np.float64, copy=True)


def _aggregate_bands_log(
    spec: np.ndarray,
    freqs: np.ndarray,
    f_lo: float,
    f_hi: float,
    n_bands: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Log-spaced frequency edges; each band sums *spec* in that interval (roughly 1/3-octave feel)."""
    import math

    a = max(f_lo, 1.0)
    fmax = float(np.max(freqs)) if freqs.size else f_hi
    b = min(f_hi, fmax) if fmax > a else min(f_hi, a * 100.0)
    b = max(b, a * 1.01)
    edges = np.logspace(math.log10(a), math.log10(b), n_bands + 1)
    wspec = spec.copy()
    m = (freqs > 0) & (freqs >= f_lo) & (freqs <= f_hi)
    wspec[~m] = 0.0
    b_idx = np.digitize(freqs, edges) - 1
    b_idx = np.clip(b_idx, 0, n_bands - 1)
    mags = np.bincount(b_idx, weights=wspec, minlength=n_bands)[:n_bands]
    return mags, edges
