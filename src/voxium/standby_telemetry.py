"""
On-station standby line: **real** local + Zulu, rFFT / log-band strip of the last good STT take, path
from capture, tail from last decode (docs/brand.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rich.text import Text

from voxium.morse_code import morse_marquee_rows_for_tick
from voxium.standby_fft import SPECTRUM_DISPLAY_WIDTH, spectrum_rich_for_tick

_DEFAULT_SR = 16000
_DEFAULT_CH = 1
# If PTT key hold and on-wire audio differ by more than this, show both (otherwise one duration).
_PTT_KEY_WIRE_TOLERANCE_S = 0.12


def _local_and_utc_hms() -> str:
    """Same instant: ``HH:MM:SS`` in the terminal’s local zone, and ``HH:MM:SSZ`` in UTC."""
    now = datetime.now().astimezone()
    local_hms = now.strftime("%H:%M:%S")
    utc_hms = now.astimezone(timezone.utc).strftime("%H:%M:%S")
    return f"{local_hms} local · {utc_hms}Z"


def _spectrum_segment(tick: int, ctx: dict[str, Any]) -> Text:
    """
    Animated spectrum row (RGB) or a fixed 32-char string for tests via ``last_spectrum_fft``.
    """
    s = ctx.get("last_spectrum_fft")
    if isinstance(s, str) and len(s) == SPECTRUM_DISPLAY_WIDTH:
        return Text(s, style="dim #86efac")
    return spectrum_rich_for_tick(tick)


def _format_path_brief(ctx: dict[str, Any]) -> str:
    """
    Middle segment: last on-wire capture length (and PTT key only if it differs), else
    rate/channels — no per-device name (keeps the line short; use ``/mic`` for the path).
    """
    wall = ctx.get("last_ptt_wall_s")
    aud = ctx.get("last_ptt_audio_s")
    if wall is not None and aud is not None:
        try:
            w, a = float(wall), float(aud)
        except (TypeError, ValueError):
            w, a = -1.0, -1.0
        if w >= 0 and a >= 0:
            seg = f"{a:.1f}s on wire"
            if abs(w - a) > _PTT_KEY_WIRE_TOLERANCE_S:
                seg = f"{seg} (key {w:.1f}s)"
            return seg

    sr = int(ctx.get("sample_rate_hz") or _DEFAULT_SR)
    ch = int(ctx.get("channels") or _DEFAULT_CH)
    ch_w = f"{ch} ch" if ch != 1 else "mono"
    return f"{sr / 1000:.0f} kHz {ch_w} — PTT a take to sample the path"


def _tail_from_context(ctx: dict[str, Any]) -> str:
    if not ctx.get("has_last_decode"):
        if ctx.get("morse_audio_playing"):
            return "CW audio on · no decode yet in this run"
        return "no decode yet in this run — PTT a clear take"

    parts: list[str] = []
    if ctx.get("morse_audio_playing"):
        parts.append("CW audio on")
    rtf = ctx.get("last_realtime_factor")
    if rtf is not None:
        try:
            parts.append(f"last RTF {float(rtf):.2f}×")
        except (TypeError, ValueError):
            pass
    pk = ctx.get("last_capture_peak")
    if pk is not None:
        try:
            parts.append(f"pk {float(pk):.3f}")
        except (TypeError, ValueError):
            pass
    rms = ctx.get("last_capture_rms_dbfs")
    if rms is not None:
        try:
            parts.append(f"RMS {float(rms):.0f} dBFS")
        except (TypeError, ValueError):
            pass
    name = (ctx.get("last_model_name") or "").strip()
    if name:
        parts.append(f"model {name[:28]}")

    if not parts:
        return "last pass on record (metrics partial)"
    return " · ".join(parts)


def build_standby_detail_line(
    tick: int,
    context: dict[str, Any] | None = None,
    *,
    content_width: int | None = None,
) -> Text:
    """
    Lines under the green **◉ PTT/VOX · Standing by** head: the first is local + Zulu,
    path, and tail (if ``ux_chatter_wit`` is set, that string leads at the far left in place
    of “Standing by.”; otherwise the line starts with “Standing by.”). The lower row is
    the animated rFFT block strip; when transcript text is available, a CW trainer label row
    appears above the Morse marquee to the right of the spectrum.
    """
    t = 0 if tick is None else int(tick)
    ctx = context or {}
    spec = _spectrum_segment(t, ctx)
    line2 = spec
    label_line: Text | None = None
    transcript = str(ctx.get("last_transcript_text") or "").strip()
    if transcript and content_width is not None:
        separator = "  "
        morse_width = int(content_width) - SPECTRUM_DISPLAY_WIDTH - len(separator)
        if morse_width > 0:
            labels, code = morse_marquee_rows_for_tick(transcript, t, morse_width)
            label_line = (
                Text(" " * SPECTRUM_DISPLAY_WIDTH, style="dim #4b5563")
                + Text(separator, style="dim #4ade80")
                + Text(labels, style="dim #fde68a")
            )
            line2 = (
                spec
                + Text(separator, style="dim #4ade80")
                + Text(code, style="dim #fbbf24")
            )
    path = _format_path_brief(ctx)
    clock = _local_and_utc_hms()
    tail = _tail_from_context(ctx)
    w = (ctx.get("ux_chatter_wit") or "").strip() if ctx else ""
    if w:
        prefix = f"{w}  "
    else:
        prefix = "Standing by.  "
    line1 = Text(prefix, style="dim #cbd5e1") + Text(
        f"  {clock}  ·  {path}  ·  {tail}", style="dim #cbd5e1"
    )
    if label_line is not None:
        return (
            line1
            + Text("\n", style="dim #cbd5e1")
            + label_line
            + Text("\n", style="dim #cbd5e1")
            + line2
        )
    return line1 + Text("\n", style="dim #cbd5e1") + line2
