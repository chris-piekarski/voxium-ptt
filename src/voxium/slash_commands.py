"""Operator slash lines (e.g. ``/help``, ``/mic``, ``/gpu``) — parsing and string answers are testable. Brand: docs/brand.md."""

from __future__ import annotations

import json
from dataclasses import dataclass
import pyperclip

from rich.text import Text

from voxium.disk_usage_report import format_repo_disk_usage_text
from voxium.metrics_table import format_gpu_metrics_plaintext
from voxium.model_disk import is_trusted_model_on_disk
from voxium.model_registry import TRUSTED_MODELS, validate_model_name
from voxium.session_history import SessionTranscriptHistory


def _first_cmd(line: str) -> str:
    s = line.strip()
    if not s.startswith("/"):
        return ""
    parts = s.split()
    return parts[0].lstrip("/").lower() if parts else ""


@dataclass(frozen=True)
class SlashDataNeeds:
    """What the client should fetch before :func:`run_slash_line` (avoid extra I/O on ``/help``)."""

    server_gpu: bool
    mic_capture: bool


@dataclass(frozen=True)
class SlashLineResult:
    """Output for one committed slash line: downlink text and optional client-side side effects."""

    text: str
    selected_model: str | None = None
    result_rich: Text | None = None


def slash_data_needs(line: str) -> SlashDataNeeds:
    c = _first_cmd(line)
    return SlashDataNeeds(
        server_gpu=c in ("gpu", "g", "cuda"),
        mic_capture=c in ("mic", "m", "microphone", "input", "audio"),
    )


def format_mic_report(mic_info: dict | None) -> str:
    """Plain-text readout for ``/mic`` (``build_audio_capture_info``-shaped dict from the client)."""
    if not mic_info:
        return "Mic path was not sampled (no capture info). Check audio input, copy."

    if mic_info.get("error"):
        return f"Audio input error: {mic_info['error']}\nCheck default input and PortAudio, copy."

    lines: list[str] = []
    lines.append("PTT / capture path (this machine):")
    be = mic_info.get("backend") or {}
    lines.append(
        f"  • stack: {be.get('api', '—')} / {be.get('library', '—')}"
        + (
            f" {be.get('sounddevice_version', '')}"
            if be.get("sounddevice_version")
            else ""
        )
    )
    if be.get("portaudio_version_text"):
        lines.append(f"  • PortAudio: {be['portaudio_version_text']}")
    dev = mic_info.get("device") or {}
    if dev:
        lines.append(f"  • default input index: {dev.get('index', '—')}")
        lines.append(f"  • name: {dev.get('name', '—')}")
        if dev.get("max_input_channels") is not None:
            lines.append(f"  • max input channels: {dev['max_input_channels']}")
        if dev.get("default_samplerate_hz") is not None:
            lines.append(f"  • default sample rate: {dev['default_samplerate_hz']} Hz")
        for key in (
            "default_low_input_latency_seconds",
            "default_high_input_latency_seconds",
        ):
            if dev.get(key) is not None:
                label = "low input latency" if "low" in key else "high input latency"
                lines.append(f"  • {label} (s): {dev[key]}")
    host = mic_info.get("host_api") or {}
    if host.get("name"):
        lines.append(f"  • host API: {host['name']}")
    fmt = mic_info.get("format") or {}
    if fmt:
        lines.append(
            f"  • client format: {fmt.get('channels', '—')} ch, {fmt.get('dtype', '—')}, "
            f"{fmt.get('sample_rate_hz', '—')} Hz (PTT path)"
        )
    st = mic_info.get("stream")
    if isinstance(st, dict) and st:
        lines.append(f"  • active stream: {json.dumps(st, default=str)[:300]}")
    return "\n".join(lines)


def build_models_catalog_rich(session_model: str | None) -> tuple[str, Text]:
    """
    Plain + Rich bodies for ``/models`` (no id): [ACTIVE] session, [ON DISK] under ``models/``, copy.
    """
    body = Text()
    body.append(
        "Allow-listed Systran models (same stack as `voxium models`). Pick one for this session:\n\n",
        style="dim #ddd6fe",
    )
    plain_lines: list[str] = [
        "Allow-listed Systran models (same stack as `voxium models`). Pick one for this session:",
        "",
    ]
    for name in sorted(TRUSTED_MODELS.keys()):
        meta = TRUSTED_MODELS[name]
        desc = meta.get("description", "")
        vram = meta.get("vram", "")
        repo = meta.get("repo", "")
        on_disk = is_trusted_model_on_disk(name)
        is_active = session_model is not None and session_model == name
        tags: list[str] = []
        if is_active:
            tags.append("[ACTIVE]")
        if on_disk:
            tags.append("[ON DISK]")
        tag_s = ("  " + "  ".join(tags)) if tags else ""
        line_text = f"  • {name} — {desc}  VRAM {vram}{tag_s}"
        plain_lines.append(line_text)
        plain_lines.append(f"      {repo}")
        if is_active:
            line_style = "bold #22d3ee"
        elif on_disk:
            line_style = "bold #4ade80"
        else:
            line_style = "dim #ddd6fe"
        body.append(line_text + "\n", style=line_style)
        body.append(f"      {repo}\n", style="dim #a78bfa")
    plain_lines.append("")
    plain_lines.append(
        "  [ACTIVE] — session model for this client.  [ON DISK] — snapshot under models/, copy."
    )
    plain_lines.append("")
    plain_lines.append("  Select:  /models <id>   example:  /models large-v3")
    body.append("\n")
    body.append(
        "  [ACTIVE] — session model for this client.  [ON DISK] — snapshot under models/, copy.\n\n",
        style="dim #94a3b8",
    )
    body.append(
        "  Select:  /models <id>   example:  /models large-v3", style="dim #94a3b8"
    )
    return "\n".join(plain_lines), body


def format_models_catalog() -> str:
    """Plain-text only (no styling); prefer :func:`build_models_catalog_rich` in the client."""
    plain, _ = build_models_catalog_rich(None)
    return plain


def _run_models_line(line: str, *, session_model: str | None = None) -> SlashLineResult:
    parts = line.strip().split()
    if len(parts) == 1:
        plain, rich = build_models_catalog_rich(session_model)
        return SlashLineResult(text=plain, result_rich=rich)
    if len(parts) > 2:
        return SlashLineResult(
            text="Use one model id per line, e.g. /models base or /models large-v3, copy."
        )
    try:
        m = validate_model_name(parts[1])
    except ValueError as e:
        return SlashLineResult(text=str(e))
    return SlashLineResult(
        text=(
            f"Session model set to {m!r}. PTT /transcribe will use it until you change it or restart, copy."
        ),
        selected_model=m,
    )


def _run_history_line(
    parts: list[str],
    *,
    transcript_history: SessionTranscriptHistory | None,
) -> SlashLineResult:
    if transcript_history is None:
        return SlashLineResult(
            text="Session history is not available in this mode, copy."
        )
    if len(parts) == 1:
        return SlashLineResult(text=transcript_history.format_list_text())
    if len(parts) >= 2 and parts[1].lower() == "clear":
        if len(parts) > 2:
            return SlashLineResult(
                text="Use /history clear alone — nothing after “clear”, copy."
            )
        n, had_pending = transcript_history.purge_all()
        if n == 0 and not had_pending:
            return SlashLineResult(
                text="Session transcript buffer was already empty (RAM only; nothing to clear), copy."
            )
        bits: list[str] = []
        if n:
            bits.append(
                f"{n} transcript line{'s' if n != 1 else ''} removed from this run"
            )
        if had_pending:
            bits.append("pending re-transmit audio dropped")
        return SlashLineResult(
            text="Cleared: " + " · ".join(bits) + ". RAM-only — not on disk, copy."
        )
    if len(parts) >= 2 and parts[1].lower() == "search":
        query = " ".join(parts[2:]).strip()
        if not query:
            return SlashLineResult(
                text="Use /history search <words> — e.g. /history search budget Q3, copy."
            )
        return SlashLineResult(text=transcript_history.format_list_text_filtered(query))
    if len(parts) >= 2 and parts[1].lower() == "copy":
        if len(parts) < 3:
            return SlashLineResult(
                text="Use /history copy <n> with n ≥ 1 (1 = most recent), copy."
            )
        try:
            n = int(parts[2])
        except ValueError:
            return SlashLineResult(
                text="Copy needs a number, e.g. /history copy 1 (most recent), copy."
            )
        t = transcript_history.text_by_display_index(n)
        if t is None:
            return SlashLineResult(text=f"No entry #{n} in this session buffer, copy.")
        try:
            pyperclip.copy(t)
        except Exception as e:
            return SlashLineResult(text=f"Clipboard failed: {e}")
        return SlashLineResult(
            text=f"Copied #{n} to the clipboard ({len(t)} chars), copy."
        )
    try:
        n = int(parts[1])
    except ValueError:
        return SlashLineResult(
            text=(
                "Use /history, /history clear, /history search <text>, /history <n> (full line), "
                "or /history copy <n>, copy."
            )
        )
    t = transcript_history.text_by_display_index(n)
    if t is None:
        return SlashLineResult(text=f"No entry #{n} in this session buffer, copy.")
    return SlashLineResult(text=f"#{n} (full text)\n\n{t}\n")


def run_slash_line(
    line: str,
    *,
    gpu: dict | None = None,
    mic_info: dict | None = None,
    session_model: str | None = None,
    transcript_history: SessionTranscriptHistory | None = None,
) -> SlashLineResult:
    """
    Handle one full line the operator committed (``/...``). Return :class:`SlashLineResult`
    (plain-text for the downlink, not the green PTT strip).
    """
    s = line.strip()
    if not s.startswith("/"):
        return SlashLineResult(
            text="Commands start with a leading slash, e.g. /help (standing by on channel)."
        )
    parts = s.split()
    first = parts[0].lstrip("/").lower() if parts else ""
    if first in ("", "help", "?", "h"):
        return SlashLineResult(text=_help_text())
    if first in ("mic", "m", "microphone", "input", "audio"):
        return SlashLineResult(text=format_mic_report(mic_info))
    if first in ("gpu", "g", "cuda"):
        return SlashLineResult(text=format_gpu_metrics_plaintext(gpu))
    if first in ("models", "model"):
        return _run_models_line(s, session_model=session_model)
    if first in ("history", "hist", "transcripts"):
        return _run_history_line(parts, transcript_history=transcript_history)
    if first in ("disk", "du", "usage"):
        return SlashLineResult(text=format_repo_disk_usage_text().rstrip("\n"))
    return SlashLineResult(
        text=f"Not wired up yet: {first!r}. Try /help for what is available on the channel, copy."
    )


def _help_text() -> str:
    return (
        "Slashed commands (downlink / session log; PTT is key separate).\n"
        "  • /help — this list\n"
        "  • /mic — default input, PortAudio, device / host API (this machine)\n"
        "  • /gpu — same GPU readout as the inference metrics box (local server /gpu)\n"
        "  • /models — list allow-listed models; /models <id> sets the session model, copy.\n"
        "  • /history — PTT/VOX transcripts (RAM only); /history <n> full line; /history copy <n>; /history clear; /history search <text> — filter the list, copy.\n"
        "  • /disk — same readout as make disk-usage: models/ and logs/ under the repo, copy."
    )
