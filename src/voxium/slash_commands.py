"""Operator slash lines (e.g. ``/help``, ``/mic``, ``/gpu``) — parsing and string answers are testable. Brand: docs/brand.md."""

from __future__ import annotations

import json
from dataclasses import dataclass
import pyperclip

from rich.text import Text

from voxium.constants import DEFAULT_HOTKEYS, HOTKEY_ORDER
from voxium.disk_usage_report import format_repo_disk_usage_text
from voxium.hotkey_rules import normalize_hotkey_name, sanitize_hotkey_config
from voxium.metrics_table import format_gpu_metrics_plaintext
from voxium.model_disk import is_trusted_model_on_disk
from voxium.model_registry import (
    DEFAULT_MODEL_NAME,
    TRUSTED_MODELS,
    validate_model_name,
)
from voxium.polish_models import validate_polish_model_tag
from voxium import polish_profile
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
    server_stats: bool
    mic_capture: bool


@dataclass(frozen=True)
class SlashLineResult:
    """Output for one committed slash line: downlink text and optional client-side side effects."""

    text: str
    selected_model: str | None = None
    result_rich: Text | None = None
    polish_model: str | None = None
    polish_enabled: bool | None = None
    hotkeys: dict[str, str] | None = None
    stream_enabled: bool | None = None


def slash_data_needs(line: str) -> SlashDataNeeds:
    c = _first_cmd(line)
    return SlashDataNeeds(
        server_gpu=c in ("gpu", "g", "cuda"),
        server_health=c in ("health",),
        server_stats=c in ("stats", "stat"),
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


def format_health_report(
    server_health: dict | None, *, session_model: str | None = None
) -> str:
    """Plain-text readout for the loopback server `/health` payload."""
    if not server_health:
        return "Server health is unavailable. Check the local /transcribe server, copy."

    status = str(server_health.get("status") or "unknown")
    model = str(server_health.get("startup_model") or server_health.get("model") or "—")
    device = str(server_health.get("device") or "—")
    compute = str(server_health.get("compute") or "—")
    timeout_s = server_health.get("timeout_seconds")
    vad_enabled = bool(server_health.get("vad_enabled"))
    lines: list[str] = [
        "Loopback server health:",
        f"  • status: {status}",
        f"  • transcribe default: server booted with {model} · device {device} · compute {compute}",
        f"  • VAD: {'on' if vad_enabled else 'off'} · timeout {timeout_s if timeout_s is not None else '—'}s",
    ]

    loaded_models = server_health.get("loaded_transcribe_models")
    if isinstance(loaded_models, list) and loaded_models:
        disp = ", ".join(str(x) for x in loaded_models if str(x).strip())
        if disp:
            lines.append(f"  • transcribe loaded: {disp}")
    warmed_models = server_health.get("warmed_transcribe_models")
    if isinstance(warmed_models, list) and warmed_models:
        disp = ", ".join(str(x) for x in warmed_models if str(x).strip())
        if disp:
            lines.append(f"  • transcribe warmed: {disp}")
    if session_model:
        lines.append(f"  • transcribe this client: {session_model}")

    model_repo = server_health.get("startup_model_repo") or server_health.get(
        "model_repo"
    )
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


def _int_stat(stats: dict | None, key: str) -> int:
    if not isinstance(stats, dict):
        return 0
    try:
        return int(stats.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float_stat(stats: dict | None, key: str) -> float:
    if not isinstance(stats, dict):
        return 0.0
    try:
        return float(stats.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def _fmt_seconds(value: float) -> str:
    return f"{value:,.1f}s"


def format_stats_report(
    persistent_stats: dict | None,
    *,
    server_stats: dict | None = None,
) -> str:
    local = persistent_stats if isinstance(persistent_stats, dict) else {}
    by_source = local.get("by_source") if isinstance(local, dict) else {}
    if not isinstance(by_source, dict):
        by_source = {}

    lines: list[str] = [
        "Inference stats:",
        "  local persisted totals (survive client/server restarts):",
        f"    • requests: {_fmt_int(_int_stat(local, 'inference_requests_total'))}",
        (
            "    • source split: "
            f"PTT {_fmt_int(_int_stat(by_source, 'ptt'))} · "
            f"VOX {_fmt_int(_int_stat(by_source, 'vox'))} · "
            f"re-transmit {_fmt_int(_int_stat(by_source, 'retry'))}"
        ),
        f"    • audio processed: {_fmt_seconds(_float_stat(local, 'audio_seconds_total'))}",
        f"    • input bytes: {_fmt_int(_int_stat(local, 'input_bytes_total'))}",
        (
            "    • timings: "
            f"STT {_fmt_seconds(_float_stat(local, 'transcription_seconds_total'))} · "
            f"request {_fmt_seconds(_float_stat(local, 'request_seconds_total'))}"
        ),
        (
            "    • tokens: "
            f"decoder {_fmt_int(_int_stat(local, 'decoder_tokens_total'))} · "
            f"re-encode {_fmt_int(_int_stat(local, 'polish_tokens_total'))} "
            f"({_fmt_int(_int_stat(local, 'polish_prompt_tokens_total'))} in / "
            f"{_fmt_int(_int_stat(local, 'polish_completion_tokens_total'))} out)"
        ),
        (
            "    • output: "
            f"{_fmt_int(_int_stat(local, 'output_words_total'))} words · "
            f"{_fmt_int(_int_stat(local, 'output_chars_total'))} chars"
        ),
    ]
    if local.get("updated_at"):
        lines.append(f"    • updated: {local['updated_at']}")

    if isinstance(server_stats, dict) and server_stats:
        model_metrics = server_stats.get("model_metrics")
        if not isinstance(model_metrics, dict):
            model_metrics = {}
        lines.extend(
            [
                "",
                "  server-process totals (reset when the /transcribe server restarts):",
                f"    • requests: {_fmt_int(_int_stat(server_stats, 'request_count'))}",
                f"    • audio processed: {_fmt_seconds(_float_stat(server_stats, 'total_audio_processed_seconds'))}",
                f"    • input bytes: {_fmt_int(_int_stat(server_stats, 'input_bytes_processed'))}",
                (
                    "    • timings: "
                    f"STT {_fmt_seconds(_float_stat(server_stats, 'total_transcription_seconds'))} · "
                    f"request {_fmt_seconds(_float_stat(server_stats, 'total_request_seconds'))}"
                ),
                (
                    "    • tokens: "
                    f"decoder {_fmt_int(_int_stat(model_metrics, 'decoder_tokens_generated'))}"
                ),
                (
                    "    • output: "
                    f"{_fmt_int(_int_stat(model_metrics, 'output_words_generated'))} words · "
                    f"{_fmt_int(_int_stat(server_stats, 'output_chars_generated'))} chars"
                ),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "  server-process totals: unavailable (server /stats did not answer).",
            ]
        )

    lines.append(
        "Note: local stats count audio requests that reached /transcribe; local no-speech filters do not count."
    )
    return "\n".join(lines)


def _transcription_model_from_file_config(file_config: dict | None) -> str | None:
    """
    Return ``transcription.model`` from operator config (``~/.config/voxium/config.yaml``) when
    set to a trusted id; else ``None`` (fresh launches use ``--model`` / product default).
    """
    if not file_config:
        return None
    t = file_config.get("transcription")
    if not isinstance(t, dict):
        return None
    raw = t.get("model")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return validate_model_name(s)
    except ValueError:
        return None


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
        "Polish + UX chatter models installed under models/polish (trusted ids + local GGUF)"
        if installed_only
        else "Polish + UX chatter models (trusted ids for llama.cpp plus local GGUF under models/polish)"
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
        f"Re-encode is {'on' if polish_enabled else 'off'} for this session (config flag: polish); UX chatter uses this same selected model.",
        f"`auto` resolves to the registry default {DEFAULT_TRUSTED_POLISH_MODEL_ID}.",
        "Use /models polish use <id|auto|local:...> to select the shared polish/chatter model; missing trusted ids are downloaded automatically.",
        "Toggle the pass with /models polish on or /models polish off (alias: /re-encode on|off).",
        (
            "Show only downloaded shared GGUFs: /models polish installed"
            if not installed_only
            else "Show the full shared model lane: /models polish list"
        ),
    ]
    return _render_catalog(title, rows, footer_lines=footer_lines)


def _format_models_status(
    session_model: str | None,
    polish_enabled: bool,
    polish_model: str | None,
    file_config: dict | None = None,
) -> str:
    pmod = polish_model or POLISH_DEFAULT_MODEL
    pe = "on" if polish_enabled else "off"
    st = session_model or DEFAULT_MODEL_NAME
    pinned = _transcription_model_from_file_config(file_config)
    pin_disp = pinned if pinned is not None else "—"
    tr = f"  Transcribe: this run: {st} · config: {pin_disp} · product default: {DEFAULT_MODEL_NAME}\n"
    if pinned is not None and pinned != DEFAULT_MODEL_NAME:
        tr += (
            f"  Hint: for {DEFAULT_MODEL_NAME!r} on every launch, set or remove "
            "`transcription.model` in ~/.config/voxium/config.yaml, or set "
            "`WHISPER_MODEL` to that id (overrides the file; same as the local server), copy.\n"
        )
    return (
        "Models\n"
        + tr
        + f"  Shared polish/chatter: re-encode {pe} · active {pmod} · default {DEFAULT_TRUSTED_POLISH_MODEL_ID} (config: polish)\n"
        "  Backend: transcribe=faster-whisper · polish/chatter=llama.cpp\n"
        "  Views: /models transcribe list | /models transcribe installed | /models polish list | /models polish installed\n"
        "  Select: /models transcribe use <id> | /models polish use <id> | /models polish on|off"
    )


_HOTKEY_OPERATOR_ACTIONS: dict[str, str] = {
    "ptt": "record",
    "record": "record",
    "transmit": "record",
    "replay": "recovery",
    "recovery": "recovery",
}
_HOTKEY_OPERATOR_LABELS: dict[str, str] = {
    "record": "PTT",
    "recovery": "replay",
}


def _current_hotkeys_from_context(current_hotkeys: dict | None) -> dict[str, str]:
    if not isinstance(current_hotkeys, dict):
        current_hotkeys = {}
    merged = {**DEFAULT_HOTKEYS}
    for action, value in current_hotkeys.items():
        if action in DEFAULT_HOTKEYS:
            merged[action] = normalize_hotkey_name(value)
    return sanitize_hotkey_config(merged)


def _format_hotkeys_status(current_hotkeys: dict | None) -> str:
    keys = _current_hotkeys_from_context(current_hotkeys)
    return (
        "Hotkeys\n"
        f"  PTT: {keys['record'].upper()}\n"
        f"  Replay: {keys['recovery'].upper()}\n"
        f"  Re-transmit: {keys['retry'].upper()}\n"
        f"  PTT↔VOX mode: {keys['mode'].upper()}\n"
        "Use /hotkeys ptt <f1..f12> or /hotkeys replay <f1..f12>, copy."
    )


def _run_hotkeys_line(
    parts: list[str], *, current_hotkeys: dict | None = None
) -> SlashLineResult:
    keys = _current_hotkeys_from_context(current_hotkeys)
    if len(parts) == 1:
        return SlashLineResult(text=_format_hotkeys_status(keys))
    if len(parts) != 3:
        return SlashLineResult(
            text="Use /hotkeys ptt <f1..f12> or /hotkeys replay <f1..f12>, copy."
        )
    action_raw = parts[1].lower()
    action = _HOTKEY_OPERATOR_ACTIONS.get(action_raw)
    if action is None:
        return SlashLineResult(text="Hotkeys can set: ptt or replay, copy.")
    wanted = normalize_hotkey_name(parts[2])
    if wanted not in HOTKEY_ORDER:
        return SlashLineResult(text="Hotkey must be F1 through F12, copy.")
    for other_action, other_key in keys.items():
        if other_action != action and other_key == wanted:
            other_label = _HOTKEY_OPERATOR_LABELS.get(other_action, other_action)
            return SlashLineResult(
                text=(
                    f"{wanted.upper()} is already assigned to {other_label}; "
                    "choose a free F-key, copy."
                )
            )
    clean = sanitize_hotkey_config({**keys, action: wanted})
    if clean[action] != wanted:
        return SlashLineResult(
            text=f"{wanted.upper()} is not available for {_HOTKEY_OPERATOR_LABELS[action]}, copy."
        )
    label = _HOTKEY_OPERATOR_LABELS[action]
    return SlashLineResult(
        text=f"{label} hotkey set to {wanted.upper()} and saved to config.yaml, copy.",
        hotkeys={action: wanted},
    )


def _run_models_line(
    line: str,
    *,
    session_model: str | None = None,
    polish_enabled: bool = False,
    polish_model: str | None = None,
    file_config: dict | None = None,
) -> SlashLineResult:
    parts = line.strip().split()
    if len(parts) == 1:
        return SlashLineResult(
            text=_format_models_status(
                session_model, polish_enabled, polish_model, file_config=file_config
            )
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
                f"Shared polish/chatter model set: {tag!r} (this session). "
                "Re-encode uses it when enabled; trusted ids download automatically if missing, copy."
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
            f"Shared polish/chatter model set: {tag!r} (this session). "
            "Re-encode uses it when enabled; trusted ids download automatically if missing, copy."
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


def _run_stream_line(
    parts: list[str], *, stream_enabled: bool | None
) -> SlashLineResult:
    """
    ``/stream`` (or ``/stream status``) shows current state; ``/stream on|off``
    toggles for the session.

    Effect is per-session: the client toggles ``config.stream_transcribe`` so the
    NEXT take opens (or skips) a /transcribe-stream WebSocket. Active sessions are
    closed on ``off`` via the runtime hook in :mod:`voxium.app`.
    """
    sub = (parts[1].lower() if len(parts) > 1 else "").strip()
    enabled_now = bool(stream_enabled)
    if sub in ("", "status", "show"):
        if len(parts) > 2 and sub != "":
            return SlashLineResult(
                text="Use /stream alone — nothing after the subcommand, copy."
            )
        state = "on" if enabled_now else "off"
        return SlashLineResult(
            text=(
                f"Streaming: {state} for this session. "
                "Type-out from the wire while the carrier's keyed; "
                "polish + paste at PTT release are unchanged. "
                "Use /stream on or /stream off."
            )
        )
    if sub in ("on", "off"):
        if len(parts) > 2:
            return SlashLineResult(text="Use /stream on or /stream off alone, copy.")
        return SlashLineResult(
            text=f"Streaming: {sub} (this session), copy.",
            stream_enabled=(sub == "on"),
        )
    return SlashLineResult(text="Use /stream, /stream on, or /stream off, copy.")


def _run_profile_line(parts: list[str]) -> SlashLineResult:
    """``/profile`` shows the runtime llama-server profile; ``/profile reset`` clears it."""
    sub = (parts[1].lower() if len(parts) > 1 else "").strip()
    if sub in ("reset", "clear", "wipe"):
        if len(parts) > 2:
            return SlashLineResult(
                text="Use /profile reset alone — nothing after the subcommand, copy."
            )
        polish_profile.reset()
        return SlashLineResult(
            text="Runtime profile cleared, copy. Trigger more calls and run /profile again."
        )
    if sub and sub not in ("show", "view"):
        return SlashLineResult(text="Use /profile (show) or /profile reset, copy.")
    return SlashLineResult(text=polish_profile.format_profile_report())


def run_slash_line(
    line: str,
    *,
    gpu: dict | None = None,
    server_health: dict | None = None,
    server_stats: dict | None = None,
    mic_info: dict | None = None,
    session_model: str | None = None,
    polish_enabled: bool = False,
    polish_model: str | None = None,
    current_hotkeys: dict | None = None,
    transcript_history: SessionTranscriptHistory | None = None,
    file_config: dict | None = None,
    persistent_stats: dict | None = None,
    stream_enabled: bool | None = None,
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
        return SlashLineResult(
            text=format_health_report(server_health, session_model=session_model)
        )
    if first in ("gpu", "g", "cuda"):
        return SlashLineResult(text=format_gpu_metrics_plaintext(gpu))
    if first in ("stats", "stat"):
        return SlashLineResult(
            text=format_stats_report(persistent_stats, server_stats=server_stats)
        )
    if first in ("profile", "prof"):
        return _run_profile_line(parts)
    if first in ("stream", "live"):
        return _run_stream_line(parts, stream_enabled=stream_enabled)
    if first in ("models", "model"):
        return _run_models_line(
            s,
            session_model=session_model,
            polish_enabled=polish_enabled,
            polish_model=polish_model,
            file_config=file_config,
        )
    if first in ("polish", "p", "re-encode", "reencode"):
        return _run_polish_line(
            s,
            session_model=session_model,
            polish_enabled=polish_enabled,
            polish_model=polish_model,
        )
    if first in ("hotkeys", "hotkey", "keys"):
        return _run_hotkeys_line(parts, current_hotkeys=current_hotkeys)
    if first in ("history", "hist", "transcripts"):
        return _run_history_line(parts, transcript_history=transcript_history)
    if first in ("disk", "du", "usage"):
        return SlashLineResult(text=format_repo_disk_usage_text().rstrip("\n"))
    return SlashLineResult(
        text=f"Not wired up yet: {first!r}. Try /help for what is available on the channel, copy."
    )


def _help_text() -> str:
    return (
        "Slash command downlink (local session; PTT/VOX keys keep working outside this box).\n"
        "Type `/`, use Tab for completions, then Enter to run. Aliases are shown in parentheses.\n\n"
        "Status and diagnostics\n"
        "  • /help (/?, /h) — this list\n"
        "  • /health — loopback server readiness: transcribe, GPU metrics, re-encode state\n"
        "  • /mic — default input, PortAudio, device, host API\n"
        "  • /gpu (/g) — same GPU readout as the inference metrics box\n"
        "  • /stats — persisted inference totals plus current server-process counters\n"
        "  • /profile (/prof) — recent llama-server timings per slot (prefill vs decode); /profile reset clears\n"
        "  • /stream (/live) — flip live transcribe streaming on/off for this session\n"
        "  • /disk (/du) — local data size: models/, logs/, tools/llama.cpp/\n\n"
        "Models and local stack\n"
        "  • /models — active transcribe + shared polish/chatter summary\n"
        "  • /models transcribe list|installed|use <id> — inspect or switch STT model for this session\n"
        "  • /models polish list|installed|use <id>|on|off — shared polish + UX chatter model lane (also controls re-encode)\n"
        "  • /re-encode or /polish — shortcut for the same re-encode lane controls\n\n"
        "Operator keys and history\n"
        "  • /hotkeys — show bindings\n"
        "  • /hotkeys ptt <f1..f12> — save the PTT transmit key to config.yaml\n"
        "  • /hotkeys replay <f1..f12> — save the replay key to config.yaml\n"
        "  • /history — RAM-only PTT/VOX transcripts for this run\n"
        "  • /history <n> | /history copy <n> | /history search <text> | /history clear — inspect, copy, filter, or clear session copy\n\n"
        "Examples: /hotkeys ptt f10 · /models polish off · /history copy 1"
    )
