"""Recording HUD strings (PTT) — pure helpers for tests, brand in caller."""
from __future__ import annotations

import math
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
