"""Recording HUD strings (PTT) — pure helpers for tests, brand in caller."""
from __future__ import annotations

import math

import numpy as np
from rich.console import Group
from rich.text import Text

# Seven-level strip (one glyph per time column) — true incoming level via bin-wise max |sample|.
WAVEFORM_BARS: str = "▁▂▃▄▅▆▇"


def rms_to_dbfs(rms: float) -> float:
    """dBFS for float samples in [-1, 1], ref full scale 1.0."""
    if rms <= 0.0 or not math.isfinite(rms):
        return -100.0
    try:
        return 20.0 * math.log10(rms)
    except ValueError:
        return -100.0


def format_recording_hud(
    sample_count: int,
    sum_sq: float,
    peak: float,
    n_chunks: int,
    sample_rate: int,
    next_reminder_in_s: float | None,
) -> str:
    """
    One-line status for the console while recording: duration, size, level, next reminder.
    `next_reminder_in_s` is time until the next 15s reminder beep; None to omit.
    """
    if sample_rate <= 0:
        sample_rate = 1
    dur = sample_count / float(sample_rate)
    rms = math.sqrt(max(0.0, sum_sq / max(1, sample_count)))
    db = rms_to_dbfs(rms)
    size_mb = (sample_count * 4) / 1_048_576.0  # float32
    next_bit = (
        f"  ·  reminder beep in ~{max(0.0, next_reminder_in_s):.0f}s"
        if next_reminder_in_s is not None
        else ""
    )
    return (
        f"capture {dur:.1f}s  ·  {size_mb:.2f} MiB  ·  {sample_count:,d} samples  "
        f"·  peak {peak:.3f}  ·  RMS {db:.0f} dBFS  ·  {n_chunks} chunks{next_bit}"
    )


def format_recording_hud_minimal(
    sample_count: int,
    sum_sq: float,
    peak: float,
    n_chunks: int,
    sample_rate: int,
    next_reminder_in_s: float | None,
) -> str:
    """Fits the ~40-column minimal TTY layout (one status line, truncated in caller if needed)."""
    if sample_rate <= 0:
        sample_rate = 1
    dur = sample_count / float(sample_rate)
    rms = math.sqrt(max(0.0, sum_sq / max(1, sample_count)))
    db = rms_to_dbfs(rms)
    rem = ""
    if next_reminder_in_s is not None and next_reminder_in_s < 1e4:
        rem = f" ~{max(0.0, next_reminder_in_s):.0f}s b"
    return (f"REC {dur:4.1f}s  pk{peak:4.2f}  {db:3.0f}dB  n{n_chunks:3d}{rem}")[:40]


def _rgb_for_amplitude01(level: float) -> str:
    """Map 0..1 to a green (quiet) → amber → red (hot) RGB for the terminal (true level cue)."""
    t = min(1.0, max(0.0, level))
    r = int(30 + 225 * (t**1.1))
    g = int(220 * (1.0 - 0.85 * t) + 20)
    b = int(50 + 70 * (1.0 - t))
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_hex_for_level(level: float) -> str:
    """Same green → amber → red as the live PTT strip (e.g. standby spectrum summary)."""
    return _rgb_for_amplitude01(level)


def colored_mono_waveform_text(
    samples: np.ndarray,
    n_columns: int,
    *,
    peak_ref: float,
) -> Text:
    """
    One row of :data:`WAVEFORM_BARS` glyphs, **RGB-colored** by per-column level.

    *samples* is mono float ``[-1, 1]``; display uses **max absolute** per column (true wave shape
    in time), normalized by *peak_ref* (e.g. running capture peak) so the strip tracks real level.
    """
    n = max(8, min(int(n_columns), 200))
    x = np.asarray(samples, dtype=np.float64).ravel()
    if x.size < 2:
        return Text("  " + "·" * min(40, n), style="dim #4b5563")
    x = x - float(np.mean(x))
    pr = max(float(peak_ref), 1.0e-4)
    step = x.size / float(n)
    out = Text()
    out.append("  ")
    for c in range(n):
        a = int(round(c * step))
        b = int(round((c + 1) * step))
        b = min(b, x.size)
        sl = x[a:b] if a < b else x[:0]
        if sl.size:
            alev = float(np.max(np.abs(sl)))
        else:
            alev = 0.0
        tlev = min(1.0, alev / pr)
        gidx = int(tlev * 6.999)
        gidx = max(0, min(6, gidx))
        st = _rgb_for_amplitude01(tlev)
        out.append(WAVEFORM_BARS[gidx], style=st)
    return out


def build_recording_hud_rich(
    sample_count: int,
    sum_sq: float,
    peak: float,
    n_chunks: int,
    sample_rate: int,
    next_reminder_in_s: float | None,
    audio_tail: np.ndarray,
    *,
    panel_inner_width: int,
) -> Group:
    """
    Two-line PTT block: same stats as :func:`format_recording_hud`, plus a colored live waveform
    of *audio_tail* (recent mono capture), scaled by *peak* (running max abs in this take).
    """
    stats = format_recording_hud(
        sample_count, sum_sq, peak, n_chunks, sample_rate, next_reminder_in_s
    )
    n_w = max(8, min(panel_inner_width - 2, 100))
    wave = colored_mono_waveform_text(
        audio_tail, n_w, peak_ref=max(float(peak), 1.0e-3)
    )
    return Group(
        Text(stats, style="dim #86efac"),
        wave,
    )
