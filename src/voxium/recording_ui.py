"""Recording HUD strings (PTT) — pure helpers for tests, brand in caller."""

from __future__ import annotations

import colorsys
import math

import numpy as np
import numpy.typing as npt
from rich.console import Group
from rich.text import Text

from .speech_guards import has_speech

# Eight-level strip (one glyph per time column) — true incoming level via bin-wise max |sample|.
WAVEFORM_BARS: str = "▁▂▃▄▅▆▇█"
_PARTIAL_BLOCKS: str = " ▁▂▃▄▅▆▇█"
_METER_ROWS = 2
_METER_UNITS_PER_ROW = 8
_METER_VERTICAL_GAIN = 1.15

# Peak-hold state for the live waveform (decays across HUD frames).
# This gives a faint "ghost" trail for recent loud moments, like an analog scope.
# Kept module-local so the public function signature stays unchanged.
_PEAK_HOLD_DECAY = 0.90  # per HUD update (~0.5s)
_waveform_peak_holds: list[float] = []


def _reset_waveform_peak_hold_for_tests() -> None:
    """Reset the internal peak-hold buffer. Only intended for tests."""
    global _waveform_peak_holds
    _waveform_peak_holds = []


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
    `next_reminder_in_s` is time until the next 15s double-blip; None to omit.
    """
    if sample_rate <= 0:
        sample_rate = 1
    dur = sample_count / float(sample_rate)
    rms = math.sqrt(max(0.0, sum_sq / max(1, sample_count)))
    db = rms_to_dbfs(rms)
    size_mb = (sample_count * 4) / 1_048_576.0  # float32
    next_bit = (
        f"  ·  2 blips in ~{max(0.0, next_reminder_in_s):.0f}s"
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
        rem = f" ~{max(0.0, next_reminder_in_s):.0f}s2b"
    return (f"REC {dur:4.1f}s  pk{peak:4.2f}  {db:3.0f}dB  n{n_chunks:3d}{rem}")[:40]


def voice_activity_pip(audio: np.ndarray, sample_rate: int) -> Text:
    """Compact live voice activity indicator for the HUD stats line.

    Returns a small styled pip:
      " ●" (bright green) when speech is detected in the tail
      " ○" (dim) otherwise.

    This is appended to the existing stats line so the Group shape (stats, wave)
    stays exactly the same — waveform remains the dominant visual.
    """
    if audio is None or len(audio) < int(sample_rate * 0.05):
        return Text(" ○", style="dim color(238)")

    try:
        active = has_speech(audio, sample_rate, threshold=0.012, segment_ms=50)
    except Exception:
        active = False

    if active:
        return Text(" ●", style="bold #86efac")
    return Text(" ○", style="dim color(238)")


def _rgb_for_amplitude01(level: float) -> str:
    """
    Map 0..1 to a broad RGB sweep for non-ANSI surfaces.

    The live meter itself now uses ANSI-256 for richer color, but standby code still reuses this
    helper for a quiet→hot cue.
    """
    t = min(1.0, max(0.0, level))
    hue = max(0.0, min(0.76, 0.76 - 0.76 * t))
    sat = 0.72 + 0.22 * t
    val = 0.48 + 0.44 * t
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def rgb_hex_for_level(level: float) -> str:
    """Same green → amber → red as the live PTT strip (e.g. standby spectrum summary)."""
    return _rgb_for_amplitude01(level)


def _ansi256_cube_index(channel01: float) -> int:
    return max(0, min(5, int(round(max(0.0, min(1.0, channel01)) * 5.0))))


def _ansi256_style_for_meter(level: float, crest: float, row: int) -> str:
    """
    ANSI-256 color for one meter cell.

    Color is derived from actual audio qualities:

    - louder bins move from cool/dim toward warm/bright (headroom cue)
    - higher crest factor nudges the hue slightly cooler for transient-heavy bins

    That keeps the meter meaningful instead of rainbow-by-position.
    """
    t = max(0.0, min(1.0, level))
    crest01 = max(0.0, min(1.0, crest))
    if t < 0.035:
        grey = 238 if row == 0 else 240
        return f"color({grey})"
    # Quiet = blue/cyan, nominal speech = green, hot = yellow/orange/red.
    base_hue = 0.66 - (0.66 * (t**0.78))
    hue = max(0.0, min(0.72, base_hue + (0.07 * crest01) - (0.015 * row)))
    sat = 0.28 + (0.58 * t) + (0.12 * (1.0 - crest01))
    sat = max(0.18, min(0.97, sat))
    val = (0.24 + 0.76 * (t**0.88)) * (0.88 if row == 0 else 1.0)
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    idx = (
        16
        + (36 * _ansi256_cube_index(r))
        + (6 * _ansi256_cube_index(g))
        + _ansi256_cube_index(b)
    )
    return f"color({idx})"


def _partial_block(units: int) -> str:
    return _PARTIAL_BLOCKS[max(0, min(_METER_UNITS_PER_ROW, int(units)))]


def colored_mono_waveform_text(
    samples: np.ndarray,
    n_columns: int,
    *,
    peak_ref: float,
) -> Text:
    """
    Two-row live meter with denser ANSI-256 color and a slightly taller visual response.

    *samples* is mono float ``[-1, 1]``; display uses per-column peak blended with RMS so the meter
    stays lively without looking too jagged. Levels are normalized by *peak_ref* (e.g. running
    capture peak), then lifted by 15% to make the active PTT meter read taller on screen.
    """
    global _waveform_peak_holds

    n = max(12, min(int(n_columns), 240))
    x: npt.NDArray[np.float64] = np.asarray(samples, dtype=np.float64).ravel()
    if x.size < 2:
        return Text("·" * min(48, n), style="dim color(240)")
    x = x - float(np.mean(x))
    pr = max(float(peak_ref), 1.0e-4)
    step = x.size / float(n)
    levels: list[float] = []
    crests: list[float] = []
    for c in range(n):
        a = int(round(c * step))
        b = int(round((c + 1) * step))
        b = min(b, x.size)
        sl = x[a:b] if a < b else x[:0]
        if sl.size:
            amax = float(np.max(np.abs(sl)))
            arms = math.sqrt(float(np.mean(sl * sl)))
            alev = (0.72 * amax) + (0.28 * arms)
            crest = min(1.0, max(0.0, (amax / max(arms, 1.0e-4) - 1.0) / 3.0))
        else:
            alev = 0.0
            crest = 0.0
        tlev = min(1.0, (alev / pr) * _METER_VERTICAL_GAIN)
        levels.append(tlev)
        crests.append(crest)

    if not any(level > 1.0e-3 for level in levels):
        # Clear hold state when the current tail is silent so it doesn't linger forever
        _waveform_peak_holds = []
        return Text("·" * min(48, n), style="dim color(240)")

    # === Peak-hold decay logic (analog scope "ghost" trail) ===
    if len(_waveform_peak_holds) != len(levels):
        _waveform_peak_holds = [0.0] * len(levels)

    held_levels: list[float] = []
    for i, live in enumerate(levels):
        prev = _waveform_peak_holds[i]
        held = max(live, prev * _PEAK_HOLD_DECAY)
        _waveform_peak_holds[i] = held
        held_levels.append(held)

    top = Text()
    bottom = Text()
    max_units = _METER_ROWS * _METER_UNITS_PER_ROW
    for idx, tlev in enumerate(levels):
        crest = crests[idx]
        held = held_levels[idx]

        # Live level determines the bright part
        live_units = int(round((tlev**0.82) * max_units))
        live_units = max(0, min(max_units, live_units))

        # Held peak can extend a little higher (ghost)
        held_units = int(round((held**0.82) * max_units))
        held_units = max(live_units, min(max_units, held_units))

        top_units = max(0, held_units - _METER_UNITS_PER_ROW)
        bottom_units = min(_METER_UNITS_PER_ROW, held_units)

        # Render held ghost with dim style if it exceeds the live level
        is_ghost_top = held > tlev + 0.06 and top_units > 0
        is_ghost_bottom = held > tlev + 0.06 and bottom_units > live_units

        top.append(
            _partial_block(top_units),
            style=(
                "color(237)"
                if is_ghost_top
                else (
                    _ansi256_style_for_meter(tlev, crest, 0)
                    if top_units
                    else "color(238)"
                )
            ),
        )
        bottom.append(
            _partial_block(bottom_units),
            style=(
                "color(238)"
                if is_ghost_bottom
                else (
                    _ansi256_style_for_meter(min(1.0, tlev * 1.04), crest, 1)
                    if bottom_units
                    else "color(240)"
                )
            ),
        )
    return Text.assemble(top, "\n", bottom)


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
    PTT/VOX recording block: stats plus a taller, denser ANSI-256 live meter of *audio_tail*
    (recent mono capture), scaled by *peak* (running max abs in this take).
    """
    stats_text = Text(
        format_recording_hud(
            sample_count, sum_sq, peak, n_chunks, sample_rate, next_reminder_in_s
        ),
        style="dim #86efac",
    )
    pip = voice_activity_pip(audio_tail, sample_rate)
    stats_line = Text.assemble(stats_text, pip)

    n_w = max(12, min(panel_inner_width, 160))
    wave = colored_mono_waveform_text(
        audio_tail, n_w, peak_ref=max(float(peak), 1.0e-3)
    )
    return Group(
        stats_line,
        wave,
    )
