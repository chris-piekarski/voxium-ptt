"""Operator slash lines (e.g. ``/help``, ``/mic``, ``/gpu``) — parsing and string answers are testable. Brand: docs/brand.md."""

from __future__ import annotations

import json
from dataclasses import dataclass
import pyperclip

from rich.text import Text

from voxium.disk_usage_report import format_repo_disk_usage_text
from voxium.metrics_table import format_gpu_metrics_plaintext
from voxium.model_disk import is_trusted_model_on_disk
from voxium.model_registry import (
    DEFAULT_MODEL_NAME,
    TRUSTED_MODELS,
    validate_model_name,
)
from voxium.polish_models import validate_polish_model_tag
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
    list_available_polish_models,
    list_local_polish_models,
)
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
    server_health: bool
    mic_capture: bool


@dataclass(frozen=True)
class SlashLineResult:
    """Output for one committed slash line: downlink text and optional client-side side effects."""

    text: str
    selected_model: str | None = None
    result_rich: Text | None = None
    polish_model: str | None = None
    polish_enabled: bool | None = None


def slash_data_needs(line: str) -> SlashDataNeeds:
    c = _first_cmd(line)
    return SlashDataNeeds(
        server_gpu=c in ("gpu", "g", "cuda"),
        server_health=c in ("health",),
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


def format_health_report(server_health: dict | None) -> str:
    """Plain-text readout for the loopback server `/health` payload."""
    if not server_health:
        return "Server health is unavailable. Check the local /transcribe server, copy."

    status = str(server_health.get("status") or "unknown")
    model = str(server_health.get("model") or "—")
    device = str(server_health.get("device") or "—")
    compute = str(server_health.get("compute") or "—")
    timeout_s = server_health.get("timeout_seconds")
    vad_enabled = bool(server_health.get("vad_enabled"))
    lines: list[str] = [
        "Loopback server health:",
        f"  • status: {status}",
        f"  • transcribe: transcriber {model} · device {device} · compute {compute}",
        f"  • VAD: {'on' if vad_enabled else 'off'} · timeout {timeout_s if timeout_s is not None else '—'}s",
    ]

    model_repo = server_health.get("model_repo")
    if model_repo:
        lines.append(f"  • model repo: {model_repo}")

    gpu_enabled = bool(server_health.get("gpu_metrics_enabled"))
    gpu_provider = server_health.get("gpu_metrics_provider")
    gpu_reason = server_health.get("gpu_metrics_unavailable_reason")
    gpu_line = f"  • GPU metrics: {'on' if gpu_enabled else 'off'}"
    if gpu_provider:
        gpu_line += f" ({gpu_provider})"
    elif gpu_reason:
        gpu_line += f" — {gpu_reason}"
    lines.append(gpu_line)

    if "polish_backend_default" in server_health:
        backend = str(server_health.get("polish_backend_default") or "—")
        polish_default = str(server_health.get("polish_default_model") or "—")
        polish_enabled = bool(server_health.get("polish_enabled_default"))
        lines.append(
            f"  • re-encode default: {'on' if polish_enabled else 'off'} · re-encoder {polish_default} · backend {backend}"
        )
        reachable = server_health.get("polish_llama_cpp_reachable")
        loaded = server_health.get("polish_loaded_model")
        reach_text = "yes" if reachable else "no"
        runtime_line = f"  • llama.cpp: reachable {reach_text}"
        reach_reason = server_health.get("polish_llama_cpp_reachable_reason")
        if reach_reason and not reachable:
            runtime_line += f" ({reach_reason})"
        if loaded:
            runtime_line += f" · loaded {loaded}"
        lines.append(runtime_line)
        keep_alive = server_health.get("polish_keep_alive_default")
        polish_timeout = server_health.get("polish_timeout_seconds")
        lines.append(
            f"  • re-encode timeout: {polish_timeout if polish_timeout is not None else '—'}s · idle unload {keep_alive if keep_alive is not None else '—'}"
        )

    fw = server_health.get("faster_whisper")
    if isinstance(fw, dict) and fw.get("version"):
        lines.append(f"  • faster-whisper: {fw.get('version')}")

    return "\n".join(lines)


def _line_style(*, active: bool = False, installed: bool = False) -> str:
    if active:
        return "bold #22d3ee"
    if installed:
        return "bold #4ade80"
    return "dim #ddd6fe"


def _render_catalog(
    title: str,
    rows: list[tuple[str, str, str]],
    *,
    footer_lines: list[str] | None = None,
) -> tuple[str, Text]:
    plain_lines = [title, ""]
    body = Text()
    body.append(title + "\n\n", style="dim #ddd6fe")
    for line, detail, style in rows:
        plain_lines.append(line)
        if detail:
            plain_lines.append(detail)
        body.append(line + "\n", style=style)
        if detail:
            body.append(detail + "\n", style="dim #a78bfa")
    if footer_lines:
        plain_lines.append("")
        plain_lines.extend(footer_lines)
        body.append("\n")
        for footer in footer_lines:
            body.append(footer + "\n", style="dim #94a3b8")
    return "\n".join(plain_lines), body


def build_transcribe_models_catalog_rich(
    session_model: str | None,
    *,
    installed_only: bool = False,
) -> tuple[str, Text]:
    title = (
        "Transcribers installed under models/ (Systran faster-whisper)"
        if installed_only
        else "Transcribers (Systran faster-whisper allow-list)"
    )
    rows: list[tuple[str, str, str]] = []
    for name in sorted(TRUSTED_MODELS):
        meta = TRUSTED_MODELS[name]
        installed = is_trusted_model_on_disk(name)
        if installed_only and not installed:
            continue
        tags: list[str] = []
        if session_model == name:
            tags.append("[ACTIVE]")
        if name == DEFAULT_MODEL_NAME:
            tags.append("[DEFAULT]")
        if installed:
            tags.append("[INSTALLED]")
        tag_s = f" {' '.join(tags)}" if tags else ""
        line = (
            f"  • {name}{tag_s} — {meta.get('description', '')} · "
            f"VRAM {meta.get('vram', '—')}"
        )
        detail = f"      repo: {meta.get('repo', '—')}"
        rows.append(
            (
                line,
                detail,
                _line_style(active=session_model == name, installed=installed),
            )
        )
    if not rows:
        rows.append(("  • none installed yet", "", "dim #ddd6fe"))
    footer_lines = [
        "Use /models transcribe use <id> to switch this session.",
        "Shorthand still works: /models <id>.",
        (
            "Show only downloaded transcriber weights: /models transcribe installed"
            if not installed_only
            else "Show the full transcriber allow-list: /models transcribe list"
        ),
    ]
    return _render_catalog(title, rows, footer_lines=footer_lines)


def build_models_catalog_rich(session_model: str | None) -> tuple[str, Text]:
    """Backward-compatible wrapper for the transcribe catalog."""
    return build_transcribe_models_catalog_rich(session_model, installed_only=False)


def build_polish_models_catalog_rich(
    session_polish_model: str | None,
    *,
    polish_enabled: bool,
    installed_only: bool = False,
) -> tuple[str, Text]:
    title = (
        "Re-encoder models installed under models/polish (trusted ids + local GGUF)"
        if installed_only
        else "Re-encoder models (trusted ids for llama.cpp plus local GGUF under models/polish)"
    )
    installed_local = list_local_polish_models()
    installed_by_name = {model.name: model for model in installed_local}
    rows: list[tuple[str, str, str]] = []
    for model in list_available_polish_models():
        local = installed_by_name.get(model.model_id)
        if installed_only and local is None:
            continue
        tags: list[str] = []
        if session_polish_model == model.model_id:
            tags.append("[ACTIVE]")
        if model.model_id == DEFAULT_TRUSTED_POLISH_MODEL_ID:
            tags.append("[DEFAULT]")
        if local is not None:
            tags.append("[INSTALLED]")
        tag_s = f" {' '.join(tags)}" if tags else ""
        line = (
            f"  • {model.model_id}{tag_s} — {model.description} · "
            f"{model.size_text} · backend {model.backend}"
        )
        detail = f"      source: {model.repo_id} · file: {model.filename}"
        rows.append(
            (
                line,
                detail,
                _line_style(
                    active=session_polish_model == model.model_id,
                    installed=local is not None,
                ),
            )
        )
    custom_rows = [m for m in installed_local if not m.is_trusted]
    if custom_rows:
        rows.append(("", "", "dim #ddd6fe"))
        for local_gguf in custom_rows:
            line = (
                f"  • {local_gguf.name} [LOCAL] — {local_gguf.description or 'Local custom GGUF'} "
                f"· {local_gguf.size_gib_text}"
            )
            detail = f"      path: {local_gguf.path}"
            rows.append(
                (
                    line,
                    detail,
                    _line_style(
                        active=session_polish_model == local_gguf.name, installed=True
                    ),
                )
            )
    if not rows:
        rows.append(("  • none installed yet", "", "dim #ddd6fe"))
    footer_lines = [
        f"Re-encode is {'on' if polish_enabled else 'off'} for this session (config flag: polish).",
        f"`auto` resolves to the registry default {DEFAULT_TRUSTED_POLISH_MODEL_ID}.",
        "Use /models polish use <id|auto|local:...> to select; missing trusted ids are downloaded automatically.",
        "Toggle the pass with /models polish on or /models polish off (alias: /re-encode on|off).",
        (
            "Show only downloaded re-encoder models: /models polish installed"
            if not installed_only
            else "Show the full re-encoder registry: /models polish list"
        ),
    ]
    return _render_catalog(title, rows, footer_lines=footer_lines)


def _format_models_status(
    session_model: str | None,
    polish_enabled: bool,
    polish_model: str | None,
) -> str:
    pmod = polish_model or POLISH_DEFAULT_MODEL
    pe = "on" if polish_enabled else "off"
    st = session_model or DEFAULT_MODEL_NAME
    return (
        "Models\n"
        f"  Transcribe: active {st} · default {DEFAULT_MODEL_NAME}\n"
        f"  Re-encode: {pe} · active {pmod} · default {DEFAULT_TRUSTED_POLISH_MODEL_ID} (config: polish)\n"
        "  Backend: transcribe=faster-whisper · re-encode=llama.cpp\n"
        "  Views: /models transcribe list | /models transcribe installed | /models polish list | /models polish installed\n"
        "  Select: /models transcribe use <id> | /models polish use <id> | /models polish on|off"
    )


def _run_models_line(
    line: str,
    *,
    session_model: str | None = None,
    polish_enabled: bool = False,
    polish_model: str | None = None,
) -> SlashLineResult:
    parts = line.strip().split()
    if len(parts) == 1:
        return SlashLineResult(
            text=_format_models_status(session_model, polish_enabled, polish_model)
        )

    sub1 = parts[1].lower()
    if sub1 == "transcribe":
        if len(parts) == 2:
            plain, rich = build_transcribe_models_catalog_rich(
                session_model,
                installed_only=False,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        action = parts[2].lower()
        if action == "list":
            plain, rich = build_transcribe_models_catalog_rich(
                session_model,
                installed_only=False,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        if action == "installed":
            plain, rich = build_transcribe_models_catalog_rich(
                session_model,
                installed_only=True,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        if action == "use":
            if len(parts) != 4:
                return SlashLineResult(
                    text="Use /models transcribe use <model_id>, copy."
                )
            try:
                model = validate_model_name(parts[3])
            except ValueError as exc:
                return SlashLineResult(text=str(exc))
            return SlashLineResult(
                text=f"Transcribe model set: {model!r} (this session), copy.",
                selected_model=model,
            )
        if len(parts) == 3:
            try:
                model = validate_model_name(parts[2])
            except ValueError as exc:
                return SlashLineResult(text=str(exc))
            return SlashLineResult(
                text=f"Transcribe model set: {model!r} (this session), copy.",
                selected_model=model,
            )
        return SlashLineResult(
            text="Use /models transcribe list, /models transcribe installed, or /models transcribe use <id>, copy."
        )

    if sub1 == "polish":
        if len(parts) == 2:
            plain, rich = build_polish_models_catalog_rich(
                polish_model,
                polish_enabled=polish_enabled,
                installed_only=False,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        action = parts[2].lower()
        if action == "list":
            plain, rich = build_polish_models_catalog_rich(
                polish_model,
                polish_enabled=polish_enabled,
                installed_only=False,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        if action == "installed":
            plain, rich = build_polish_models_catalog_rich(
                polish_model,
                polish_enabled=polish_enabled,
                installed_only=True,
            )
            return SlashLineResult(text=plain, result_rich=rich)
        if action in ("on", "off") and len(parts) == 3:
            return SlashLineResult(
                text=f"Re-encode: {action} (this session), copy.",
                polish_enabled=(action == "on"),
            )
        if action in {"use", "model"}:
            if len(parts) < 4:
                return SlashLineResult(
                    text="Use /models polish use <id|auto|local:...>, copy."
                )
            requested = " ".join(parts[3:]).strip()
        else:
            requested = " ".join(parts[2:]).strip()
        try:
            tag = validate_polish_model_tag(requested)
        except ValueError as exc:
            return SlashLineResult(text=str(exc))
        return SlashLineResult(
            text=(
                f"Re-encoder set: {tag!r} (this session). "
                "Trusted ids download automatically if missing, copy."
            ),
            polish_model=tag,
        )

    if len(parts) == 2:
        try:
            model = validate_model_name(parts[1])
        except ValueError as exc:
            return SlashLineResult(text=str(exc))
        return SlashLineResult(
            text=f"Transcribe model set: {model!r} (this session), copy.",
            selected_model=model,
        )
    return SlashLineResult(
        text="Use /models, /models transcribe list|installed|use <id>, or /models polish list|installed|use <id>|on|off, copy."
    )


def _run_polish_line(
    line: str,
    *,
    session_model: str | None = None,
    polish_enabled: bool = False,
    polish_model: str | None = None,
) -> SlashLineResult:
    parts = line.strip().split()
    if len(parts) == 1:
        plain, rich = build_polish_models_catalog_rich(
            polish_model,
            polish_enabled=polish_enabled,
            installed_only=False,
        )
        return SlashLineResult(text=plain, result_rich=rich)
    if len(parts) == 2 and parts[1].lower() in ("on", "off"):
        sub = parts[1].lower()
        return SlashLineResult(
            text=f"Re-encode: {sub} (this session), copy.",
            polish_enabled=(sub == "on"),
        )
    if len(parts) == 2 and parts[1].lower() in ("list", "installed"):
        installed_only = parts[1].lower() == "installed"
        plain, rich = build_polish_models_catalog_rich(
            polish_model,
            polish_enabled=polish_enabled,
            installed_only=installed_only,
        )
        return SlashLineResult(text=plain, result_rich=rich)
    if len(parts) >= 2 and parts[1].lower() in {"use", "model"}:
        if len(parts) < 3:
            return SlashLineResult(
                text="Use /re-encode use <id|auto|local:...> (or /polish use …), copy."
            )
        requested = " ".join(parts[2:]).strip()
    elif len(parts) == 2:
        requested = parts[1]
    else:
        return SlashLineResult(
            text="Use /re-encode, /re-encode list, /re-encode installed, /re-encode on|off, or /re-encode use <id|auto|local:...> (same as /polish), copy."
        )
    try:
        tag = validate_polish_model_tag(requested)
    except ValueError as exc:
        return SlashLineResult(text=str(exc))
    return SlashLineResult(
        text=(
            f"Re-encoder set: {tag!r} (this session). "
            "Trusted ids download automatically if missing, copy."
        ),
        polish_model=tag,
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
    server_health: dict | None = None,
    mic_info: dict | None = None,
    session_model: str | None = None,
    polish_enabled: bool = False,
    polish_model: str | None = None,
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
    if first in ("health",):
        return SlashLineResult(text=format_health_report(server_health))
    if first in ("gpu", "g", "cuda"):
        return SlashLineResult(text=format_gpu_metrics_plaintext(gpu))
    if first in ("models", "model"):
        return _run_models_line(
            s,
            session_model=session_model,
            polish_enabled=polish_enabled,
            polish_model=polish_model,
        )
    if first in ("polish", "p", "re-encode", "reencode"):
        return _run_polish_line(
            s,
            session_model=session_model,
            polish_enabled=polish_enabled,
            polish_model=polish_model,
        )
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
        "  • /health — loopback server readiness: transcribe path, GPU metrics, re-encode (llama.cpp) state\n"
        "  • /mic — default input, PortAudio, device / host API (this machine)\n"
        "  • /gpu — same GPU readout as the inference metrics box (local server /gpu)\n"
        "  • /models — summary; /models transcribe list|installed|use <id>; /models polish … (re-encode lane, same as /re-encode), copy.\n"
        "  • /re-encode or /polish — re-encode lane: list|installed|use <id>|on|off (same as /models polish), copy.\n"
        "  • /history — PTT/VOX transcripts (RAM only); /history <n> full line; /history copy <n>; /history clear; /history search <text> — filter the list, copy.\n"
        "  • /disk — same readout as make disk-usage: models/, logs/, and tools/llama.cpp/ under the repo, copy."
    )
