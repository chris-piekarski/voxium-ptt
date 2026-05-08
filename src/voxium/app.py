#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import platform
from collections import deque
import subprocess
import sys
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import numpy as np
import pyperclip
import requests
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from scipy.io import wavfile
import yaml

from voxium.capture_enrich import enrich_capture_with_recording
from voxium.console_status import (
    PttSessionStatusBox,
    build_polish_ensure_stack_downlink_panel,
    build_polish_slash_ensure_downlink_panel,
    build_status_box_panel,
    print_agent_telemetry_panel,
    print_input_mode_downlink,
    print_slash_command_downlink,
    status_uses_recording_hud_line,
    voxium_panel_width,
)
from voxium.cli_argv import normalize_cli_args
from voxium.constants import (
    APP_VERSION,
    DEFAULT_METRICS_SAMPLE_INTERVAL,
    DEFAULT_SERVER_COMPUTE,
    DEFAULT_SERVER_DEVICE,
    DEFAULT_SERVER_MODEL,
    DEFAULT_SERVER_START_TIMEOUT,
    DEFAULT_SERVER_TIMEOUT,
    env_polish_enabled_default,
    DEFAULT_SERVER_URL,
    DEFAULT_HOTKEYS,
    HOTKEY_ORDER,
    LOG_LEVELS,
    SAMPLE_RATE,
)
from voxium.exit_pause import pause_console_before_exit
from voxium.ensure_model_client import ensure_model_on_loopback_server
from voxium.http_detail import http_error_detail_text
from voxium.radio_readback import (
    take_edge_inference_detail,
    take_edge_inference_rexmit_detail,
    take_readback,
    take_readback_rexmit,
)
from voxium.recording_ui import (
    build_recording_hud_rich,
    format_recording_hud,
    format_recording_hud_minimal,
    rms_to_dbfs,
)
from voxium.hotkey_rules import (
    hotkey_config_changed,
    normalize_hotkey_name,
    sanitize_hotkey_config,
)
from voxium.json_sanitize import json_safe_audio_value, round_audio_float
from voxium.local_server_cmd import LocalServerLaunchConfig, argv_after_interpreter
from voxium.loopback import (
    get_gpu_url,
    get_health_url,
    get_server_endpoint_url,
    is_loopback_host,
    is_loopback_url,
    normalize_loopback_host,
    normalize_loopback_url,
)
from voxium.metrics_table import (
    build_ptt_log_metrics_layout,
    format_polish_usage_suffix,
)
from voxium.metrics_text import describe_server
from voxium.model_arg import trusted_model_arg
from voxium.model_disk import is_trusted_model_on_disk
from voxium.model_registry import (
    DEFAULT_MODEL_NAME,
    TRUSTED_MODEL_HELP,
    TRUSTED_MODELS,
    validate_model_name,
)
from voxium.morse_audio import MorseAudioController
from voxium.llama_cpp_daemon import (
    ManagedLlamaCpp,
    ensure_llama_cpp_daemon,
    llama_server_cli_path,
    stop_managed_llama_cpp,
)
from voxium.llama_cpp_client import llama_cpp_loaded_model, llama_cpp_reachable
from voxium.paths import (
    default_server_log_path,
    ensure_runtime_dirs,
    instance_lock_path,
    llama_cpp_dir,
    logs_dir,
    polish_models_dir,
    repo_root,
)
from voxium.persistent_stats import config_stats_path, load_stats, record_stats
from voxium.ptt_keying import (
    PTT_ACTION_START,
    PTT_ACTION_STOP,
    PttKeyTracker,
    handle_ptt_press,
    handle_ptt_release,
)
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
    list_available_polish_models,
    list_local_polish_models,
)
from voxium.polish_models import validate_polish_model_tag
from voxium.polish_policy import parse_sleep_idle_seconds
from voxium.polish_provision import (
    ensure_default_polish_assets,
    ensure_polish_model_downloaded,
    ensure_windows_llama_cpp_runtime,
    wrap_hf_download_progress,
)
from voxium.resolve_log import resolve_log_level as resolve_log_level_pure
from voxium.slash_complete import apply_slash_tab, format_slash_command_hints
from voxium.slash_commands import SlashLineResult, run_slash_line, slash_data_needs
from voxium.session_history import SessionTranscriptHistory
from voxium import polish_profile
from voxium.terminal_focus import is_our_terminal_focused
from voxium.speech_guards import has_speech, is_hallucination
from voxium.standby_fft import set_spectrum_from_mono_float
from voxium.vox_chunker import UtteranceChunker


class _SoundDeviceProxy:
    """Load PortAudio (``sounddevice``) on first use so CLI help / non-audio commands work without it."""

    _mod: Any = None

    def _ensure(self) -> Any:
        if self._mod is None:
            import sounddevice as _sd

            self._mod = _sd
        return self._mod

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ensure(), name)


class _PynputKeyboardProxy:
    """Import ``pynput.keyboard`` only when the interactive client actually needs it."""

    _mod: Any = None

    def _ensure(self) -> Any:
        if self._mod is None:
            from pynput import keyboard as _keyboard

            self._mod = _keyboard
        return self._mod

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ensure(), name)


sd: Any = _SoundDeviceProxy()
keyboard: Any = _PynputKeyboardProxy()

SYSTEM = platform.system()


TERMINALS = {
    "Linux": [
        "gnome-terminal",
        "xterm",
        "konsole",
        "alacritty",
        "kitty",
        "terminator",
        "tilix",
        "xfce4-terminal",
        "urxvt",
        "st",
        "sakura",
        "guake",
        "tilda",
        "hyper",
        "wezterm",
    ],
    "Windows": [
        "WindowsTerminal",
        "cmd.exe",
        "powershell",
        "pwsh",
        "ConEmu",
        "mintty",
        "Hyper",
        "Terminus",
    ],
    "Darwin": ["Terminal", "iTerm", "iTerm2", "Hyper", "kitty", "alacritty", "wezterm"],
}


class SmartDefaultsFormatter(argparse.ArgumentDefaultsHelpFormatter):

    def _get_help_string(self, action):
        if isinstance(action, argparse.BooleanOptionalAction):
            if action.default is True:
                return f"{action.help} (default: enabled)"
            if action.default is False:
                return f"{action.help} (default: disabled)"
        if action.default in (None, False) or action.default is argparse.SUPPRESS:
            return action.help
        return super()._get_help_string(action)


class State:
    IDLE = 0
    RECORDING = 1
    TRANSCRIBING = 2
    COMMAND_INPUT = 3


state = State.IDLE
state_lock = threading.Lock()
audio_chunks: list[np.ndarray] = []
stream: sd.InputStream | None = None
target_window = None
config = None
history = None


def get_transcript_history() -> SessionTranscriptHistory | None:
    """The single session :class:`SessionTranscriptHistory` (same object PTT/VOX and /history use)."""
    return history


last_transcription_metrics: dict | None = None
current_audio_capture_info: dict | None = None
last_audio_capture_info: dict | None = None
audio_capture_statuses: list[str] = []
recording_started_at: float | None = None
recording_audio_lock = threading.Lock()
recording_sum_sq: float = 0.0
recording_sample_count: int = 0
recording_peak_abs: float = 0.0
recording_monitor_event = threading.Event()
recording_monitor_thread: threading.Thread | None = None
client_shutdown_event = threading.Event()
console = Console()
# Background UX chatter may print a violet Downlink; serialize with other console prints.
_ux_chatter_downlink_lock = threading.Lock()
ptt_status_box: PttSessionStatusBox | None = None
_telemetry_log_buffer: list[tuple[str, str]] = []
managed_server_process: subprocess.Popen | None = None
managed_llama_cpp: ManagedLlamaCpp | None = None
morse_audio_controller: MorseAudioController | None = None
_llama_cpp_polish_ready_checked: bool = False
_llama_cpp_ux_ready_checked: bool = False
server_log_handle = None


_last_hotkey_time: float = 0.0
_last_paste_time: float = 0.0
# PTT start: block accidental double-press. PTT stop: no extra delay (same key, clear intent).
HOTKEY_DEBOUNCE_PTT_START_MS = 220
HOTKEY_DEBOUNCE_PTT_STOP_MS = 0
# Back-compat for slash / recovery handlers that read HOTKEY_DEBOUNCE_MS
HOTKEY_DEBOUNCE_MS = HOTKEY_DEBOUNCE_PTT_START_MS
RECORDING_HUD_THREAD_JOIN_S = 0.3
# Operator ``/…`` line (Rich footer); only used when not ``--minimal``.
_slash_buffer: str = ""
_slash_tab_cycle: int = 0
_SLASH_LINE_MAX = 2048
PASTE_DEBOUNCE_MS = 500
RECORDING_HUD_INTERVAL_S = 0.5
RECORDING_REMINDER_INTERVAL_S = 15.0
VOX_HUD_RING_MAX = 96_000
_ptt_key_tracker = PttKeyTracker()

# PTT (default) vs VOX (open-mic) — toggled with --mode-hotkey (F7)
input_mode: str = "ptt"  # "ptt" | "vox"
input_mode_lock = threading.Lock()
vox_stream: sd.InputStream | None = None
vox_chunker: UtteranceChunker | None = None
vox_hud_lock = threading.Lock()
vox_ring: np.ndarray = np.empty(0, dtype=np.float32)
# Each item: (mono audio, foreground window id at utterance time) — same idea as PTT's
# target at record start, so focus+paste goes to the operator's editor/terminal.
vox_pending_audio: deque[tuple[np.ndarray, Any]] = deque()
vox_pending_lock = threading.Lock()
vox_monitor_event = threading.Event()
vox_monitor_thread: threading.Thread | None = None

# Blue PTT log :class:`Panel` dim footer (static unless UX chatter fills it in
# :func:`log_transcription_summary`).
_PTT_LOG_PANEL_SUBTITLE_DEFAULT = "PTT & VOX log — local loopback only"

# Ctrl+C / clean exit (static unless UX chatter fills in :func:`_print_shutdown_farewell`).
_VOXIUM_SHUTDOWN_DEFAULT = "Voxium: 73 / 10-7 — going clear, copy."

# Green status strip — PTT/VOX & local stack (brand: docs/brand.md)
STATUS_VOX_ON_STATION = "◉ PTT/VOX · Standing by"
STATUS_VOX_OPEN = "🎙️ PTT/VOX · VOX (OPEN MIC)"
STATUS_VOX_COPY = "🖥️ PTT/VOX · COPY"
STATUS_VOX_COPY_REXMIT = "🖥️ PTT/VOX · COPY (RE-XMIT)"
STATUS_PTT_ACTIVE = "📻 PTT ACTIVE"
STATUS_EDGE_INFERENCE = "🤖 EDGE INFERENCE"
STATUS_EDGE_INFERENCE_REXMIT = "🤖 EDGE INFERENCE (RE-XMIT)"
STATUS_VOX_LAST_COPY = "↩️ PTT/VOX · LAST COPY"
STATUS_NO_AUDIO = "❌ NO AUDIO"
# One short window title: radio = PTT, robot = local inference (tab bar read; override: env VOXIUM_WINDOW_TITLE).
VOXIUM_WINDOW_TITLE = "Voxium 📻🤖"

DEBUG_PASTE = False

CONFIG_PATH = Path.home() / ".config" / "voxium" / "config.yaml"


def _apply_transcription_model_defaults(merged: dict[str, Any]) -> None:
    """
    Ensure ``transcription.model`` is set: file value when present, else product default, then
    valid ``WHISPER_MODEL`` (same as the local server) if set. Matches ``scripts/windows/Voxium.ps1``,
    which seeds ``WHISPER_MODEL=small.en`` so boot aligns with :data:`voxium.model_registry.DEFAULT_MODEL_NAME`.
    """
    t = dict(merged.get("transcription") or {})
    raw = (t.get("model") or "").strip()
    if raw:
        try:
            t["model"] = validate_model_name(raw)
        except ValueError:
            t["model"] = DEFAULT_MODEL_NAME
    else:
        t["model"] = DEFAULT_MODEL_NAME
    w = (os.environ.get("WHISPER_MODEL") or "").strip()
    if w:
        try:
            t["model"] = validate_model_name(w)
        except ValueError:
            pass
    merged["transcription"] = t


def load_config_file() -> dict:
    if not CONFIG_PATH.exists():
        merged: dict[str, Any] = {}
    else:
        try:
            with open(CONFIG_PATH) as f:
                raw = yaml.safe_load(f) or {}
            if not raw:
                merged = {}
            else:
                from voxium.config import VoxiumUserConfig

                merged = VoxiumUserConfig.model_validate(raw).model_dump()
        except Exception:
            merged = {}
    _apply_transcription_model_defaults(merged)
    return merged


def persist_hotkey_config(changes: dict[str, str]) -> None:
    """
    Persist canonical hotkey bindings to the operator config while preserving unrelated sections.

    Slash commands expose operator-facing names (``ptt`` / ``replay``), but the config uses the
    existing canonical keys (``record`` / ``recovery``).
    """
    if not changes:
        return
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception:
            raw = {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    hotkeys = raw.get("hotkeys")
    if not isinstance(hotkeys, dict):
        hotkeys = {}
    for action, key_name in changes.items():
        hotkeys[action] = normalize_hotkey_name(key_name)
    raw["hotkeys"] = hotkeys
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False)


def _merge_run_file_config(args: Any) -> dict:
    """Merge ``~/.config/voxium/config.yaml`` with ``voxium run`` CLI overrides (e.g. UX chatter)."""
    _fc = load_config_file()
    server = _fc.get("server")
    server_cfg = server if isinstance(server, dict) else {}
    transcription = _fc.get("transcription")
    transcription_cfg = transcription if isinstance(transcription, dict) else {}
    u = {
        **dict(_fc.get("ux_chatter") or {}),
        # ``args.ux_chatter`` is the argparse result (default is seeded from yaml in
        # :func:`add_run_options` when the run parser is built).
        "enabled": bool(getattr(args, "ux_chatter", False)),
    }
    u["base_url"] = str(
        u.get("base_url") or server_cfg.get("llama_cpp_url") or "http://127.0.0.1:11435"
    ).strip()
    u["model"] = str(
        u.get("model")
        or transcription_cfg.get("polish_model")
        or DEFAULT_TRUSTED_POLISH_MODEL_ID
    )
    u.setdefault("auto_start", True)
    u.setdefault("auto_pull", True)
    u["auto_start"] = bool(u["auto_start"])
    u["auto_pull"] = bool(u["auto_pull"])
    if not is_loopback_url(u["base_url"]):
        u["base_url"] = str(server_cfg.get("llama_cpp_url") or "").strip()
    if not is_loopback_url(u["base_url"]):
        u["base_url"] = "http://127.0.0.1:11435"
    return {**_fc, "ux_chatter": u}


def _ux_chatter_on_complete(line_result, rt) -> None:
    """Violet Downlink for the UX chatter pass — no transcript text, metrics only."""
    if not config or getattr(config, "minimal", False):
        return
    from voxium.ux_chatter import format_ux_chatter_downlink_line

    out = format_ux_chatter_downlink_line(rt, line_result)
    if out is None:
        return
    msg, level = out
    with _ux_chatter_downlink_lock:
        print_agent_telemetry_panel(
            console,
            [(msg, level)],
            downlink_subtitle="experience",
        )


def _status_detail_for_edge_inference(*, rexmit: bool = False) -> str:
    """Second line under EDGE INFERENCE: LLM when UX chatter is on, else static pool."""
    if not (config and getattr(config, "ux_chatter", False)):
        return (
            take_edge_inference_rexmit_detail()
            if rexmit
            else take_edge_inference_detail()
        )
    from voxium.ux_chatter import (
        is_ux_chatter_wanted,
        request_ux_chatter_edge_line_full,
        ux_chatter_runtime_from_config,
    )

    fc = getattr(config, "file_config", None) or {}
    if not is_ux_chatter_wanted(
        cli_enabled=bool(getattr(config, "ux_chatter", False)), file_config=fc
    ):
        return (
            take_edge_inference_rexmit_detail()
            if rexmit
            else take_edge_inference_detail()
        )
    rt = ux_chatter_runtime_from_config(fc)
    full = request_ux_chatter_edge_line_full(rt, rexmit=rexmit)
    _ux_chatter_on_complete(full, rt)
    w = (full.wit or "").strip()
    if w:
        return w
    return (
        take_edge_inference_rexmit_detail() if rexmit else take_edge_inference_detail()
    )


def _ux_resolve_copy_wit(text: str, uxf: Any) -> str:
    """COPY subline: sync **copy** wit, or one extra copy request, else :func:`take_readback`."""
    w = (uxf.wit or "").strip() if uxf is not None else ""
    if w:
        return w
    t = (text or "").strip()
    if not t or not (config and getattr(config, "ux_chatter", False)):
        return take_readback()
    from voxium.ux_chatter import (
        is_ux_chatter_wanted,
        request_ux_chatter_line_full,
        ux_chatter_runtime_from_config,
    )

    fc = getattr(config, "file_config", None) or {}
    if not is_ux_chatter_wanted(
        cli_enabled=bool(getattr(config, "ux_chatter", False)), file_config=fc
    ):
        return take_readback()
    rt = ux_chatter_runtime_from_config(fc)
    one = request_ux_chatter_line_full(rt, t, purpose="copy")
    return (one.wit or "").strip() or take_readback()


def add_output_options(
    parser: argparse.ArgumentParser,
    *,
    include_verbose: bool = True,
    include_log_level: bool = True,
):

    if include_verbose:
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Increase console detail. Repeat for more detail.",
        )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress nonessential console output.",
    )
    if include_log_level:
        parser.add_argument(
            "--log-level",
            choices=LOG_LEVELS,
            default=None,
            help="Server log level. Overrides --verbose/--quiet for server logs.",
        )


def apply_runtime_hotkey_safety(args) -> bool:

    requested = {
        "record": args.hotkey,
        "recovery": args.recovery_hotkey,
        "retry": args.retry_hotkey,
        "mode": getattr(args, "mode_hotkey", None) or "f7",
    }
    clean = sanitize_hotkey_config(requested)
    args.hotkey = clean["record"]
    args.recovery_hotkey = clean["recovery"]
    args.retry_hotkey = clean["retry"]
    args.mode_hotkey = clean["mode"]
    left = {name: normalize_hotkey_name(value) for name, value in requested.items()}
    return any(left.get(k) != clean.get(k) for k in clean)


def add_run_options(parser: argparse.ArgumentParser, file_config: dict):

    configured_hotkeys = file_config.get("hotkeys", {})
    hotkeys = sanitize_hotkey_config(configured_hotkeys)
    parser.set_defaults(
        _config_hotkeys_adjusted=hotkey_config_changed(configured_hotkeys, hotkeys)
    )
    trans = file_config.get("transcription", {})
    ui = file_config.get("ui", {})
    hist = file_config.get("history", {})
    server = file_config.get("server", {})

    server_url_default = trans.get("server_url") or DEFAULT_SERVER_URL
    if not is_loopback_url(server_url_default):
        server_url_default = DEFAULT_SERVER_URL

    server_group = parser.add_argument_group(
        "Voxium server",
        "Local transcription server (faster-whisper) that Voxium starts or reuses.",
    )
    server_group.add_argument(
        "--server-url",
        default=server_url_default,
        help="HTTP loopback transcription URL",
    )
    server_group.add_argument(
        "--server-start-timeout",
        type=int,
        default=trans.get("server_start_timeout", DEFAULT_SERVER_START_TIMEOUT),
        help="Seconds to wait for server startup/model load",
    )
    server_group.add_argument(
        "--server-log-file",
        default=server.get("log_file", str(default_server_log_path())),
        help="Managed server log path",
    )
    server_group.add_argument(
        "--server-device",
        choices=("auto", "cuda", "cpu"),
        default=server.get("device") or DEFAULT_SERVER_DEVICE,
        help="Device for the local server (default: cuda — local GPU; use cpu if CUDA is unavailable)",
    )
    server_group.add_argument(
        "--server-compute",
        choices=("auto", "float16", "int8"),
        default=server.get("compute") or DEFAULT_SERVER_COMPUTE,
        help="Compute type for the local server (default: float16 for GPU)",
    )
    server_group.add_argument(
        "--server-timeout",
        type=int,
        default=server.get("timeout", DEFAULT_SERVER_TIMEOUT),
        help="Per-request server timeout in seconds",
    )
    server_group.add_argument(
        "--server-vad",
        action=argparse.BooleanOptionalAction,
        default=server.get("vad_enabled", True),
        help="Enable/disable server VAD filtering when Voxium starts the server",
    )
    server_group.add_argument(
        "--server-gpu-metrics",
        action=argparse.BooleanOptionalAction,
        default=server.get("gpu_metrics_enabled", True),
        help="Enable/disable per-request GPU metrics when Voxium starts the server",
    )
    server_group.add_argument(
        "--metrics-sample-interval",
        type=float,
        default=server.get("metrics_sample_interval", DEFAULT_METRICS_SAMPLE_INTERVAL),
        help="GPU metrics sampling interval in seconds",
    )
    llama_cpp_url_default = server.get("llama_cpp_url") or "http://127.0.0.1:11435"
    if not is_loopback_url(llama_cpp_url_default):
        llama_cpp_url_default = "http://127.0.0.1:11435"
    server_group.add_argument(
        "--llama-cpp-url",
        default=llama_cpp_url_default,
        help="llama.cpp loopback URL (managed re-encode runtime health) — must stay on the ground net",
    )
    server_group.add_argument(
        "--llama-cpp-auto-start",
        action=argparse.BooleanOptionalAction,
        default=bool(server.get("llama_cpp_auto_start", True)),
        help="When re-encode (polish) is enabled, start/stop a local llama-server if none is reachable",
    )
    server_group.add_argument(
        "--llama-cpp-cmd",
        default=str(server.get("llama_cpp_cmd") or ""),
        help="Optional explicit path to llama-server(.exe); default = tools/llama.cpp then PATH",
    )
    server_group.add_argument(
        "--llama-cpp-gpu-layers",
        default=str(server.get("llama_cpp_gpu_layers", "auto") or "auto"),
        help="llama-server --n-gpu-layers for re-encode runtime (default: auto)",
    )
    server_group.add_argument(
        "--llama-cpp-ctx-size",
        type=int,
        default=int(server.get("llama_cpp_ctx_size", 0) or 0),
        help="llama-server --ctx-size for re-encode (0 = model default)",
    )
    polish_timeout_d = float(server.get("polish_timeout", 25.0) or 25.0)
    server_group.add_argument(
        "--polish-timeout",
        type=float,
        default=polish_timeout_d,
        help="Per-/polish timeout (seconds) for managed server and for client POST /polish",
    )
    server_group.add_argument(
        "--polish-keep-alive",
        default=str(server.get("polish_keep_alive", "-1") or "-1"),
        help="Idle unload window for llama.cpp re-encode runtime (default: -1, keep loaded; e.g. 10m, 0, -1)",
    )
    server_group.add_argument(
        "--polish-warmup-on-start",
        action=argparse.BooleanOptionalAction,
        default=bool(server.get("polish_warmup_on_start", True)),
        help="After STT model load, probe/warm the local llama.cpp re-encode runtime (default: on)",
    )
    server_group.add_argument(
        "--polish-max-concurrent",
        type=int,
        default=int(server.get("polish_max_concurrent", 2) or 2),
        help="Max concurrent /polish (re-encode) requests the managed server accepts (semaphore cap)",
    )

    transcription_group = parser.add_argument_group("Transcription")
    transcription_group.add_argument(
        "--model",
        "-m",
        type=trusted_model_arg,
        default=trans.get("model") or DEFAULT_MODEL_NAME,
        metavar="MODEL",
        help=(
            f"Voxium model (Systran faster-whisper only): {TRUSTED_MODEL_HELP} "
            f"(default: {DEFAULT_MODEL_NAME} if unset in config)"
        ),
    )
    transcription_group.add_argument(
        "--language",
        "-l",
        default=trans.get("language"),
        help="Language code for transcription",
    )
    polish_m_default = trans.get("polish_model")
    if not polish_m_default:
        polish_m_default = POLISH_DEFAULT_MODEL
    transcription_group.add_argument(
        "--polish",
        action=argparse.BooleanOptionalAction,
        default=bool(trans.get("polish_enabled", True)),
        help="After STT, run a local re-encode pass (POST /polish, llama.cpp) before paste (default: on)",
    )
    transcription_group.add_argument(
        "--polish-model",
        type=str,
        default=polish_m_default,
        metavar="MODEL",
        help=(
            "Shared polish/chatter GGUF id (or auto; config: --polish-model). "
            "See `voxium models polish list` for trusted options and installed status."
        ),
    )

    hotkey_group = parser.add_argument_group("Hotkeys and UI")
    # Parser defaults: coerce YAML ints (e.g. 6) to f6 / f7 (strings) so we never compare 7 to f7.
    hotkey_group.add_argument(
        "--hotkey",
        "-k",
        default=normalize_hotkey_name(hotkeys.get("record", "f9") or "f9"),
        help="Record/stop F1-F12 hotkey. Examples: f8, f10, f12",
    )
    hotkey_group.add_argument(
        "--recovery-hotkey",
        default=normalize_hotkey_name(hotkeys.get("recovery", "f8") or "f8"),
        help="Hotkey (F1–F12) to cycle replay: re-paste PTT/VOX transcripts (newest first, wraps)",
    )
    hotkey_group.add_argument(
        "--retry-hotkey",
        default=normalize_hotkey_name(hotkeys.get("retry", "f6") or "f6"),
        help="Hotkey (F1–F12) to re-transmit: re-run transcription on the last pending capture",
    )
    hotkey_group.add_argument(
        "--mode-hotkey",
        default=normalize_hotkey_name(hotkeys.get("mode", "f7") or "f7"),
        help="Hotkey (F1–F12) to toggle PTT (push-to-talk) vs VOX (open-mic, utterance gating)",
    )
    hotkey_group.add_argument(
        "--minimal",
        "-M",
        action="store_true",
        default=ui.get("minimal", False),
        help="Minimal UI - only show status",
    )
    hotkey_group.add_argument(
        "--slash-global",
        action=argparse.BooleanOptionalAction,
        default=bool(ui.get("slash_global", False)),
        help="Treat / command input as global; default (false) = only when this terminal window is focused (Windows / Linux X11; see docs)",
    )

    uxc = file_config.get("ux_chatter")
    uxc = uxc if isinstance(uxc, dict) else {}
    uxc_group = parser.add_argument_group(
        "UX chatter (shared polish model, default on)",
        "Short HAM-style one-liners in the console. Chatter uses the same selected "
        "polish GGUF and --llama-cpp-url as re-encode; --ux-chatter only toggles the UI copy.",
    )
    uxc_group.add_argument(
        "--ux-chatter",
        action=argparse.BooleanOptionalAction,
        default=bool(uxc.get("enabled", True)),
        help=(
            "Client-only UX chatter using the active polish llama.cpp model. "
            "Default: on. Use --no-ux-chatter to disable, or set VOXIUM_UX_CHATTER=0 to force off."
        ),
    )

    history_group = parser.add_argument_group("History")
    history_group.add_argument(
        "--history-limit",
        type=int,
        default=hist.get("limit", 42),
        help="Maximum transcriptions to keep in this process (RAM; session-only)",
    )
    history_group.add_argument(
        "--history-max-chars",
        type=int,
        default=hist.get("max_total_chars", 512_000),
        help="Max total characters of transcript text in RAM (oldest entries dropped first)",
    )
    history_group.add_argument(
        "--history-pending-mib",
        type=int,
        default=hist.get("pending_audio_max_mib", 32),
        metavar="N",
        help="Max MiB for the last capture in RAM (re-transmit; default F6). Use 0 to disable",
    )


def add_server_options(parser: argparse.ArgumentParser):

    parser.add_argument(
        "--model",
        "-m",
        type=trusted_model_arg,
        default=DEFAULT_SERVER_MODEL,
        metavar="MODEL",
        help=f"Voxium model (Systran faster-whisper only): {TRUSTED_MODEL_HELP}",
    )
    parser.add_argument(
        "--device",
        "-d",
        choices=("auto", "cuda", "cpu"),
        default=DEFAULT_SERVER_DEVICE,
        help="Device: auto, cuda, cpu",
    )
    parser.add_argument(
        "--compute",
        "-c",
        choices=("auto", "float16", "int8"),
        default=DEFAULT_SERVER_COMPUTE,
        help="Compute type: auto, float16, int8",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="HTTP loopback host to bind to"
    )
    parser.add_argument("--port", "-p", type=int, default=8002, help="Port to bind to")
    parser.add_argument(
        "--timeout",
        "-t",
        type=int,
        default=DEFAULT_SERVER_TIMEOUT,
        help="Transcription timeout in seconds",
    )
    parser.add_argument(
        "--vad",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable VAD filtering",
    )
    parser.add_argument(
        "--gpu-metrics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable per-request GPU metrics sampling",
    )
    parser.add_argument(
        "--metrics-sample-interval",
        type=float,
        default=DEFAULT_METRICS_SAMPLE_INTERVAL,
        help="GPU metrics sampling interval in seconds",
    )
    parser.add_argument(
        "--llama-cpp-url",
        default=os.environ.get("VOXIUM_LLAMA_CPP_URL", "http://127.0.0.1:11435"),
        help="llama.cpp loopback base URL for re-encode /polish (default: http://127.0.0.1:11435)",
    )
    parser.add_argument(
        "--polish-default-model",
        default=os.environ.get("VOXIUM_POLISH_MODEL", POLISH_DEFAULT_MODEL),
        metavar="MODEL",
        help=(
            f"Default re-encoder id for /polish (default: {POLISH_DEFAULT_MODEL}; "
            f"registry default resolves to {DEFAULT_TRUSTED_POLISH_MODEL_ID})"
        ),
    )
    parser.add_argument(
        "--polish-timeout",
        type=float,
        default=float(os.environ.get("VOXIUM_POLISH_TIMEOUT", "25")),
        help="Per-/polish request timeout in seconds (default: 25)",
    )
    parser.add_argument(
        "--polish-enabled-by-default",
        action=argparse.BooleanOptionalAction,
        default=env_polish_enabled_default(),
        help=(
            "Server /health hint: client re-encode default (default: on; "
            "set VOXIUM_POLISH_ENABLED=0 to opt out)"
        ),
    )
    parser.add_argument(
        "--polish-keep-alive",
        default=os.environ.get("VOXIUM_POLISH_KEEP_ALIVE", "-1"),
        help="llama.cpp idle unload window for /polish re-encode (default: -1, keep loaded)",
    )
    parser.add_argument(
        "--polish-warmup-on-start",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("VOXIUM_POLISH_WARMUP", "1").lower()
        not in ("0", "false", "no", "off"),
        help="Probe/warm the local llama.cpp re-encode runtime at startup (default: on)",
    )
    parser.add_argument(
        "--polish-max-concurrent",
        type=int,
        default=int(os.environ.get("VOXIUM_POLISH_MAX_CONCURRENT", "2")),
        help="Max concurrent /polish re-encode requests (semaphore, default: 2)",
    )


def add_server_query_options(parser: argparse.ArgumentParser, file_config: dict):

    trans = file_config.get("transcription", {})
    server_url_default = trans.get("server_url") or DEFAULT_SERVER_URL
    if not is_loopback_url(server_url_default):
        server_url_default = DEFAULT_SERVER_URL
    parser.add_argument(
        "--server-url",
        default=server_url_default,
        help="HTTP loopback transcription URL",
    )
    parser.add_argument(
        "--timeout", type=float, default=3.0, help="HTTP request timeout in seconds"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of a formatted panel",
    )


def build_parser(file_config: dict) -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog="voxium",
        usage="voxium [command] [options]",
        description=(
            "Voxium — PTT (push-to-talk) voice in, text out, over local loopback. "
            "Radio: *VOX* at the mic. Stack: a moon-and-back run of *your* hardware+software+model+coding-agent path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  voxium                                  Open the PTT & VOX path (default command)
  voxium --model large-v3 -v              Same as: voxium run --model large-v3 -v
  voxium run --server-device cpu         CPU stack (default server device is cuda)
  voxium stats                            Ground readout: server totals
  voxium models                           Model manager summary for transcribe + re-encode
  voxium models transcribe installed      Show downloaded STT models under models/
  voxium models polish list               Shared polish/chatter GGUF ids and install state
  voxium models polish pull               Provision default shared GGUF (and runtime where supported)
  voxium health --json                    Downlink: server health as JSON
  voxium server --help                    Foreground server (diagnostics; normal use: voxium run)
        """,
    )
    parser.add_argument("--version", action="version", version=f"Voxium {APP_VERSION}")

    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        prog="voxium run",
        help="Start the PTT client (default command) and local /transcribe stack if needed",
        description="Start the client and, if needed, the local transcription server — your normal mission loop.",
        formatter_class=SmartDefaultsFormatter,
    )
    add_output_options(run_parser)
    add_run_options(run_parser, file_config)

    server_parser = subparsers.add_parser(
        "server",
        prog="voxium server",
        help="Run the /transcribe server in the foreground (stack diagnostics)",
        description="Foreground server for debugging. Normal PTT use: `voxium run` (server comes up as needed).",
        formatter_class=SmartDefaultsFormatter,
    )
    add_output_options(server_parser)
    add_server_options(server_parser)

    health_parser = subparsers.add_parser(
        "health",
        prog="voxium health",
        help="Downlink: local server health",
        formatter_class=SmartDefaultsFormatter,
    )
    add_output_options(health_parser, include_verbose=False, include_log_level=False)
    add_server_query_options(health_parser, file_config)

    stats_parser = subparsers.add_parser(
        "stats",
        prog="voxium stats",
        help="Downlink: local server inference totals",
        formatter_class=SmartDefaultsFormatter,
    )
    add_output_options(stats_parser, include_verbose=False, include_log_level=False)
    add_server_query_options(stats_parser, file_config)

    models_parser = subparsers.add_parser(
        "models",
        prog="voxium models",
        help="Inspect transcribe + shared polish/chatter inventory and install state",
        formatter_class=SmartDefaultsFormatter,
    )
    models_parser.add_argument(
        "lane",
        nargs="?",
        help="Optional lane: transcribe or polish (re-encode)",
    )
    models_parser.add_argument(
        "action",
        nargs="?",
        help="Optional action: list, installed, or pull (re-encode / polish only)",
    )
    models_parser.add_argument(
        "model_id",
        nargs="?",
        help="Optional shared polish/chatter model id for pull",
    )
    models_parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of a formatted table",
    )
    return parser


def parse_args(argv: list[str] | None = None):

    file_config = load_config_file()
    parser = build_parser(file_config)
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(normalize_cli_args(raw_args))
    if hasattr(args, "model"):
        try:
            args.model = validate_model_name(args.model)
        except ValueError as exc:
            parser.error(str(exc))
    if hasattr(args, "polish_model") and args.polish_model is not None:
        try:
            args.polish_model = validate_polish_model_tag(args.polish_model)
        except ValueError as exc:
            parser.error(str(exc))
    if hasattr(args, "polish_default_model") and args.polish_default_model is not None:
        try:
            args.polish_default_model = validate_polish_model_tag(
                args.polish_default_model
            )
        except ValueError as exc:
            parser.error(str(exc))
    if hasattr(args, "llama_cpp_url") and not is_loopback_url(args.llama_cpp_url):
        parser.error(
            "llama-cpp-url must be http loopback (127.0.0.1, localhost, or ::1)"
        )
    # Force IPv4 for loopback URLs so requests skip the IPv6→IPv4 fallback stall
    # (~1-2s per request on Windows + WSL2). Voxium servers all bind 127.0.0.1.
    if hasattr(args, "server_url"):
        args.server_url = normalize_loopback_url(args.server_url)
    if hasattr(args, "llama_cpp_url"):
        args.llama_cpp_url = normalize_loopback_url(args.llama_cpp_url)
    return args


def check_dependencies():
    """Fail with a **single** report: Linux used to exit before the PortAudio check, hiding multiple fixes."""
    problems: list[str] = []

    if SYSTEM == "Linux":
        missing = []
        for cmd in ("xdotool", "xclip"):
            try:
                subprocess.run(["which", cmd], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                missing.append(cmd)
        if missing:
            problems.append(
                "Linux paste / focus helpers are missing: "
                + ", ".join(missing)
                + "\n  Install (Debian/Ubuntu/WSL): sudo apt update && sudo apt install -y "
                + " ".join(missing)
            )

    try:
        devices = sd.query_devices()
        if not any(d["max_input_channels"] > 0 for d in devices):
            problems.append(
                "No mic / input device — PTT path blocked. Check the default input in OS sound settings."
            )
    except OSError as e:
        err = str(e).lower()
        if "portaudio" in err:
            extra = (
                "  Windows: run `scripts\\windows\\Setup-Voxium.ps1` from the repo (or `Setup-Voxium.cmd`), "
                "or `python -m pip install --force-reinstall sounddevice` in your `.venv`. "
                "Install the **Microsoft Visual C++ Redistributable (x64)** if the wheel still fails to load.\n"
            )
            if SYSTEM != "Windows":
                extra = (
                    "  Debian/Ubuntu/WSL: sudo apt update && sudo apt install -y portaudio19-dev\n"
                    "  macOS (Homebrew): brew install portaudio\n"
                )
            problems.append(
                "PortAudio is not available (required for the microphone).\n"
                f"{extra}"
                f"  Details: {e}"
            )
        else:
            problems.append(f"Audio path error: {e}")
    except Exception as e:
        problems.append(f"Audio path error: {e}")

    if problems:
        print(
            "Voxium run cannot start until the following are satisfied:\n\n"
            + "\n\n".join(f"({i}) {p}" for i, p in enumerate(problems, start=1))
            + "\n\n"
            "Without `voxium run`, you can still use:  voxium --help   voxium models   voxium health\n",
            file=sys.stderr,
        )
        pause_console_before_exit()
        sys.exit(1)


def get_server_health(timeout: float = 2.0) -> dict | None:

    try:
        resp = requests.get(get_health_url(config.server_url), timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def get_server_gpu_metrics() -> dict | None:
    """``GET /gpu`` JSON ``gpu`` field, or error shape on 503, or None if request fails."""
    if config is None or not is_loopback_url(config.server_url):
        return None
    try:
        resp = requests.get(get_gpu_url(config.server_url), timeout=2.0)
        if resp.status_code == 503:
            j = {}
            try:
                j = resp.json()
            except Exception:
                pass
            return {
                "_error": j.get("error", "gpu_metrics_unavailable"),
                "_reason": j.get("reason", ""),
            }
        resp.raise_for_status()
        data = resp.json()
        return data.get("gpu") if isinstance(data, dict) else None
    except Exception:
        return None


def get_server_stats(timeout: float = 2.0) -> dict | None:
    if config is None or not is_loopback_url(config.server_url):
        return None
    try:
        resp = requests.get(
            get_server_endpoint_url(config.server_url, "stats"), timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def resolve_log_level(args) -> str:
    return resolve_log_level_pure(
        log_level=getattr(args, "log_level", None),
        quiet=bool(getattr(args, "quiet", False)),
        verbose=int(getattr(args, "verbose", 0) or 0),
    )


def cli_log(message: str, level: str = "info"):

    global _telemetry_log_buffer
    if config is not None:
        if getattr(config, "quiet", False) and level not in {"warning", "error"}:
            return
        if level == "debug" and not getattr(config, "verbose", 0):
            return
    if config and not config.minimal:
        _telemetry_log_buffer.append((message, level))
        return
    print(message)


def flush_client_telemetry_block(*, include_ops_cheat: bool = False) -> None:
    global _telemetry_log_buffer
    if not config or config.minimal:
        return
    to_print: list[tuple[str, str]] = list(_telemetry_log_buffer)
    if include_ops_cheat and not getattr(config, "quiet", False):
        to_print.append(
            (
                f"PTT: {config.hotkey.upper()} to transmit; "
                f"{config.mode_hotkey.upper()} PTT↔VOX; "
                f"{config.recovery_hotkey.upper()} to replay; "
                f"{config.retry_hotkey.upper()} to re-transmit.",
                "info",
            )
        )
        to_print.append(("Ctrl+C signs off and stops Voxium.", "info"))
    if not to_print:
        return
    print_agent_telemetry_panel(console, to_print)
    _telemetry_log_buffer.clear()


def start_local_server():

    global managed_server_process, server_log_handle

    log_path = Path(config.server_log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    server_log_handle = open(log_path, "a", encoding="utf-8")

    lcfg = LocalServerLaunchConfig(
        server_url=config.server_url,
        server_timeout=int(config.server_timeout),
        metrics_sample_interval=float(config.metrics_sample_interval),
        model=config.model,
        server_device=config.server_device,
        server_compute=config.server_compute,
        server_vad=bool(config.server_vad),
        server_gpu_metrics=bool(config.server_gpu_metrics),
        llama_cpp_url=str(getattr(config, "llama_cpp_url", "http://127.0.0.1:11435")),
        polish_default_model=str(getattr(config, "polish_model", "") or "")
        or POLISH_DEFAULT_MODEL,
        polish_timeout=float(getattr(config, "polish_timeout", 25.0) or 25.0),
        polish_enabled_by_default=bool(getattr(config, "polish", True)),
        polish_keep_alive=str(getattr(config, "polish_keep_alive", "-1") or "-1"),
        polish_warmup_on_start=bool(getattr(config, "polish_warmup_on_start", True)),
        polish_max_concurrent=int(getattr(config, "polish_max_concurrent", 2) or 2),
    )
    cmd = [
        sys.executable,
        *argv_after_interpreter(
            lcfg,
            log_level=resolve_log_level(config),
            default_device=DEFAULT_SERVER_DEVICE,
            default_compute=DEFAULT_SERVER_COMPUTE,
        ),
    ]

    kwargs = {
        "stdout": server_log_handle,
        "stderr": subprocess.STDOUT,
        "cwd": str(repo_root()),
    }
    if SYSTEM == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    managed_server_process = subprocess.Popen(cmd, **kwargs)
    model_detail = config.model or "server default"
    cli_log(
        f"Bringing local /transcribe online: model={model_detail}, url={config.server_url}"
    )
    cli_log(f"Server log: {log_path}")
    cli_log(f"Server command: {subprocess.list2cmdline(cmd)}", "debug")


def ensure_local_server():

    if not is_loopback_url(config.server_url):
        print(
            "Voxium: only local loopback for the transcribe server (no off-world URL)."
        )
        print(f"Use a loopback URL such as: {DEFAULT_SERVER_URL}")
        pause_console_before_exit()
        sys.exit(1)

    info = get_server_health(timeout=1.5)
    if info:
        cli_log(f"Stack on station: {describe_server(info)}")
        return

    start_local_server()

    deadline = time.time() + config.server_start_timeout
    last_error_at = 0.0
    while time.time() < deadline:
        info = get_server_health(timeout=2.0)
        if info:
            cli_log(f"Server ready (copy, good read): {describe_server(info)}")
            return
        if managed_server_process and managed_server_process.poll() is not None:
            print("Local server ended during startup — no green board yet.")
            print(f"Check {config.server_log_file} for details.")
            pause_console_before_exit()
            sys.exit(1)
        if time.time() - last_error_at > 10:
            cli_log("Waiting for the stack to load the model (stand by)...")
            last_error_at = time.time()
        time.sleep(0.5)

    print("Timed out waiting for the local server to come on station.")
    print(f"Check {config.server_log_file} for details.")
    pause_console_before_exit()
    sys.exit(1)


def ensure_llama_cpp_for_polish(
    *,
    force: bool = False,
    for_chatter: bool = False,
    polish_slash: tuple[str, SlashLineResult] | None = None,
    freeze_for_external_output: Any = None,
) -> None:
    """Ensure the shared polish/UX-chatter llama.cpp runtime is available."""
    global managed_llama_cpp, _llama_cpp_polish_ready_checked
    if not config:
        return
    if not getattr(config, "polish", True) and not for_chatter:
        return
    if _llama_cpp_polish_ready_checked and not force:
        return
    base_url = str(getattr(config, "llama_cpp_url", "http://127.0.0.1:11435"))
    if force and managed_llama_cpp is not None:
        try:
            stop_managed_llama_cpp(managed_llama_cpp)
        finally:
            managed_llama_cpp = None
    if not getattr(config, "llama_cpp_auto_start", True):
        ok, _reason = llama_cpp_reachable(base_url, timeout=1.0)
        _llama_cpp_polish_ready_checked = ok
        if not ok:
            feature = (
                "UX chatter"
                if for_chatter and not getattr(config, "polish", True)
                else "Re-encode"
            )
            cli_log(
                f"{feature} needs the shared polish llama.cpp runtime; auto-start is disabled, so an existing local llama-server must answer /health.",
                "warning",
            )
        return
    requested_model = str(getattr(config, "polish_model", "") or POLISH_DEFAULT_MODEL)
    use_rich = bool(
        config and not getattr(config, "minimal", False) and not for_chatter
    )

    def _run_after_model(
        resolved: Any,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> list[tuple[str, str]]:
        global managed_llama_cpp, _llama_cpp_polish_ready_checked
        cli_cmd = str(getattr(config, "llama_cpp_cmd", "") or "").strip() or None

        def _emit(msg: str, level: str) -> None:
            if progress is not None:
                progress(msg)
            else:
                cli_log(msg, level)

        gpu_layers_raw = (
            str(getattr(config, "llama_cpp_gpu_layers", "auto") or "auto")
            .strip()
            .lower()
        )
        gpu_layers: int | str | None
        if gpu_layers_raw in {"", "auto"}:
            gpu_layers = 999
        else:
            gpu_layers = str(getattr(config, "llama_cpp_gpu_layers", "") or "").strip()
        try:
            sleep_idle_seconds = parse_sleep_idle_seconds(
                getattr(config, "polish_keep_alive", "-1"),
                default=-1,
            )
        except ValueError as exc:
            _emit(str(exc), "warning")
            sleep_idle_seconds = -1

        if os.name == "nt" and not llama_server_cli_path(cli_cmd, base_env=os.environ):
            try:
                ensure_windows_llama_cpp_runtime(
                    base_env=dict(os.environ),
                    progress=progress or (lambda m: cli_log(m, "info")),
                )
            except RuntimeError as exc:
                if progress is not None:
                    raise
                _emit(str(exc), "error")
                _llama_cpp_polish_ready_checked = False
                return []

        managed, entries = ensure_llama_cpp_daemon(
            base_url=base_url,
            cmd_path=cli_cmd,
            model_path=resolved.path,
            model_alias=resolved.name,
            log_path=logs_dir() / "llama_cpp.log",
            startup_timeout=min(
                30.0, max(3.0, float(getattr(config, "polish_timeout", 25.0) or 25.0))
            ),
            parallel=max(1, int(getattr(config, "polish_max_concurrent", 2) or 2)),
            ctx_size=int(getattr(config, "llama_cpp_ctx_size", 0) or 0),
            gpu_layers=gpu_layers,
            sleep_idle_seconds=sleep_idle_seconds,
        )
        if managed is not None:
            managed_llama_cpp = managed
        ok, _reason = llama_cpp_reachable(base_url, timeout=1.0)
        _llama_cpp_polish_ready_checked = ok
        for msg, level in entries:
            _emit(msg, level)
        return list(entries)

    if use_rich:
        if freeze_for_external_output is not None:
            try:
                freeze_for_external_output()
            except Exception:
                pass

        def _update_live_with_hf(
            msg: str,
            live: Live,
            line: str | None,
            out: SlashLineResult | None,
        ) -> None:
            m = (msg or "…").strip().replace("\n", " ")
            if line is not None and out is not None:
                live.update(
                    build_polish_slash_ensure_downlink_panel(
                        console,
                        command_line=line,
                        result_text=out.text,
                        result_rich=out.result_rich,
                        hf_status_line=m,
                    )
                )
            else:
                live.update(
                    build_polish_ensure_stack_downlink_panel(
                        console,
                        headline="Local re-encode stack (llama.cpp + trusted GGUF).",
                        hf_status_line=m,
                    )
                )

        sl_line = polish_slash[0] if polish_slash else None
        sl_out = polish_slash[1] if polish_slash else None
        if polish_slash is not None and sl_out is not None:
            start = build_polish_slash_ensure_downlink_panel(
                console,
                command_line=sl_line or "",
                result_text=sl_out.text,
                result_rich=sl_out.result_rich,
                hf_status_line="Resolving local re-encoder (Hugging Face)…",
            )
        else:
            start = build_polish_ensure_stack_downlink_panel(
                console,
                headline="Local re-encode stack (llama.cpp + trusted GGUF).",
                hf_status_line="Resolving local re-encoder (Hugging Face)…",
            )
        entries_after: list[tuple[str, str]] = []
        try:
            with Live(
                start,
                console=console,
                refresh_per_second=10,
                transient=True,
            ) as live:

                def progress_sink(msg: str) -> None:
                    _update_live_with_hf(msg, live, sl_line, sl_out)

                resolved = ensure_polish_model_downloaded(
                    model_name=requested_model,
                    progress=progress_sink,
                )
                progress_sink(
                    "GGUF on disk. Spooling local llama-server (loopback) — one moment, copy."
                )
                entries_after = _run_after_model(resolved, progress=progress_sink)
                has_warn = (not _llama_cpp_polish_ready_checked) or any(
                    lev in ("warning", "error") for _, lev in entries_after
                )
                if has_warn:
                    progress_sink(
                        "Re-encode stack: re-encoder ready; llama.cpp still needs attention (downlink below)."
                    )
                else:
                    progress_sink("Re-encode stack: on station, copy.")
        except RuntimeError as exc:
            _llama_cpp_polish_ready_checked = False
            print_agent_telemetry_panel(
                console,
                [
                    (
                        "Re-encode local stack setup did not complete (re-encoder, Windows runtime, or llama.cpp), copy.",
                        "error",
                    ),
                    (str(exc)[:2000], "error"),
                ],
                downlink_subtitle="re-encode",
            )
            return

        has_polish_warn = (not _llama_cpp_polish_ready_checked) or any(
            lev in ("warning", "error") for _, lev in entries_after
        )
        warn_lines: list[tuple[str, str]] = (
            list(entries_after)
            if entries_after
            else [
                (
                    "llama.cpp did not report ready for re-encode after the local stack step.",
                    "warning",
                )
            ]
        )
        if polish_slash is not None and sl_out is not None and sl_line is not None:
            print_slash_command_downlink(
                console, sl_line, sl_out.text, result_rich=sl_out.result_rich
            )
            if has_polish_warn:
                print_agent_telemetry_panel(
                    console,
                    warn_lines,
                    downlink_subtitle="re-encode",
                )
        elif has_polish_warn:
            print_agent_telemetry_panel(
                console,
                warn_lines,
                downlink_subtitle="re-encode",
            )
        else:
            print_agent_telemetry_panel(
                console,
                [("Re-encoder and llama-server: on station, copy.", "info")],
                downlink_subtitle="re-encode",
            )
        return

    try:
        resolved = ensure_polish_model_downloaded(
            model_name=requested_model,
            progress=wrap_hf_download_progress(lambda msg: cli_log(msg, "info")),
        )
    except RuntimeError as exc:
        cli_log(str(exc), "warning")
        _llama_cpp_polish_ready_checked = False
        return
    _run_after_model(resolved)


def ensure_llama_cpp_for_ux_chatter() -> None:
    """
    Bind UX chatter to the same selected polish model and llama.cpp runtime.

    UX chatter can be on while the re-encode pass is off, but the model lane is still
    ``polish_model`` and the runtime is still ``--llama-cpp-url``.
    """
    global _llama_cpp_ux_ready_checked
    if not config or not getattr(config, "ux_chatter", False):
        return
    from voxium.ux_chatter import is_ux_chatter_wanted

    fc = getattr(config, "file_config", None) or {}
    if not is_ux_chatter_wanted(
        cli_enabled=bool(getattr(config, "ux_chatter", False)),
        file_config=fc,
    ):
        return
    if _llama_cpp_ux_ready_checked:
        return
    _llama_cpp_ux_ready_checked = True
    from voxium.ux_chatter import set_resolved_ux_chatter_runtime

    base_url = str(getattr(config, "llama_cpp_url", "http://127.0.0.1:11435") or "")
    base_url = base_url.strip() or "http://127.0.0.1:11435"
    requested_model = str(getattr(config, "polish_model", "") or POLISH_DEFAULT_MODEL)
    model_alias = (
        DEFAULT_TRUSTED_POLISH_MODEL_ID
        if requested_model == POLISH_DEFAULT_MODEL
        else requested_model
    )
    set_resolved_ux_chatter_runtime(base_url, model_alias)
    ensure_llama_cpp_for_polish(for_chatter=True)

    ok, _reason = llama_cpp_reachable(base_url, timeout=1.0)
    if not ok:
        _llama_cpp_ux_ready_checked = False
        cli_log(
            "UX chatter shares the polish model lane, but the shared llama.cpp runtime is not on station.",
            "warning",
        )
        return
    loaded = llama_cpp_loaded_model(base_url, timeout=1.0)
    set_resolved_ux_chatter_runtime(base_url, loaded or model_alias)
    cli_log(
        f"UX chatter sharing polish model lane: {loaded or model_alias} on {base_url}.",
        "info",
    )


def apply_slash_runtime_changes(
    out: SlashLineResult,
    *,
    freeze_for_external_output: Any = None,
    polish_slash: tuple[str, SlashLineResult] | None = None,
) -> None:
    global _llama_cpp_polish_ready_checked, _llama_cpp_ux_ready_checked
    if config is None:
        return
    if out.selected_model is not None:
        config.model = out.selected_model
        ensure_model_on_loopback_server(
            config.server_url,
            console,
            out.selected_model,
            freeze_for_external_output=freeze_for_external_output,
        )

    should_refresh_polish = False
    refresh_force = False
    if out.polish_model is not None:
        config.polish_model = out.polish_model
        _llama_cpp_polish_ready_checked = False
        _llama_cpp_ux_ready_checked = False
        if bool(getattr(config, "polish", True)):
            should_refresh_polish = True
            refresh_force = True
    if out.polish_enabled is not None:
        config.polish = out.polish_enabled
        if out.polish_enabled:
            _llama_cpp_polish_ready_checked = False
            should_refresh_polish = True
    if out.hotkeys:
        persist_hotkey_config(out.hotkeys)
        fc = getattr(config, "file_config", None)
        if not isinstance(fc, dict):
            fc = {}
            config.file_config = fc
        hk = fc.get("hotkeys")
        if not isinstance(hk, dict):
            hk = {}
            fc["hotkeys"] = hk
        for action, key_name in out.hotkeys.items():
            normalized = normalize_hotkey_name(key_name)
            hk[action] = normalized
            if action == "record":
                config.hotkey = normalized
                if ptt_status_box is not None and not getattr(config, "minimal", False):
                    ptt_status_box.set_ptt_hotkey_hint(normalized.upper())
            elif action == "recovery":
                config.recovery_hotkey = normalized
    if should_refresh_polish:
        ensure_llama_cpp_for_polish(
            force=refresh_force,
            polish_slash=polish_slash,
            freeze_for_external_output=freeze_for_external_output,
        )
        if bool(getattr(config, "ux_chatter", False)):
            ensure_llama_cpp_for_ux_chatter()
    elif out.polish_model is not None and bool(getattr(config, "ux_chatter", False)):
        ensure_llama_cpp_for_ux_chatter()
    elif polish_slash is not None:
        pl, po = polish_slash
        print_slash_command_downlink(console, pl, po.text, result_rich=po.result_rich)
    if should_refresh_polish:
        flush_client_telemetry_block()


def beep(freq: float, duration: float, volume: float = 0.12):

    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
    wave = (volume * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    try:
        sd.play(wave, SAMPLE_RATE)
    except Exception:
        pass


def beep_start():
    beep(880, 0.08)


def beep_stop():
    beep(440, 0.12)


def beep_error():
    beep(220, 0.2)


def beep_success():
    beep(660, 0.08)


def beep_recording_reminder():
    """
    Two rapid blips (not the 880 Hz PTT start tone) so the still-on-air cue is distinct
    from push-to-talk. See :data:`RECORDING_REMINDER_INTERVAL_S`.
    """
    vol = 0.11
    blip_s = 0.042
    gap_s = 0.03
    # Above PTT start (880 Hz) / stop (440 Hz) so the ear does not map this to the PTT edge.
    f1, f2 = 1180.0, 1320.0
    n1 = int(SAMPLE_RATE * blip_s)
    n_gap = int(SAMPLE_RATE * gap_s)
    t1 = np.linspace(0, blip_s, n1, False)
    t2 = np.linspace(0, blip_s, n1, False)
    w1 = (vol * np.sin(2 * np.pi * f1 * t1)).astype(np.float32)
    gap = np.zeros(n_gap, dtype=np.float32)
    w2 = (vol * np.sin(2 * np.pi * f2 * t2)).astype(np.float32)
    wave = np.concatenate([w1, gap, w2])
    try:
        sd.play(wave, SAMPLE_RATE)
    except Exception:
        pass


def _end_recording_hud_line() -> None:
    if is_client_shutting_down() or (config and config.minimal):
        return
    if ptt_status_box is not None and (config and not config.minimal):
        return
    try:
        sys.stdout.write("\n")
        sys.stdout.flush()
    except OSError:
        pass


def _recording_tail_samples(max_samples: int = 48_000) -> np.ndarray:
    """Recent mono capture (float32) for the live PTT waveform — last *max_samples* from RAM."""
    with recording_audio_lock:
        if not audio_chunks:
            return np.array([], dtype=np.float32)
        # Only concat tail chunks to keep the HUD path cheap on long PTTs.
        parts = audio_chunks[-96:]
    if not parts:
        return np.array([], dtype=np.float32)
    cat = np.concatenate(parts, dtype=np.float32)
    if cat.size > max_samples:
        return cat[-max_samples:].copy()
    return cat


def _write_recording_hud(content: str | RenderableType) -> None:
    if is_client_shutting_down() or not config:
        return
    if config.minimal:
        if not isinstance(content, str):
            return
        show_status(STATUS_PTT_ACTIVE, content)
    elif ptt_status_box is not None:
        ptt_status_box.update_recording_hud(content)
    else:
        if not isinstance(content, str):
            return
        try:
            sys.stdout.write(f"\r\033[2K{STATUS_PTT_ACTIVE}  {content}")
            sys.stdout.flush()
        except OSError:
            pass


def _next_recording_reminder_in_s() -> float | None:
    t0 = recording_started_at
    if t0 is None:
        return None
    now = time.perf_counter()
    elapsed = now - t0
    if elapsed < 0:
        return RECORDING_REMINDER_INTERVAL_S
    n_done = int(elapsed // RECORDING_REMINDER_INTERVAL_S)
    next_at = t0 + (n_done + 1) * RECORDING_REMINDER_INTERVAL_S
    return max(0.0, next_at - now)


def _recording_monitor_loop() -> None:
    t0 = recording_started_at
    if t0 is None:
        return
    next_beep = t0 + RECORDING_REMINDER_INTERVAL_S
    while not recording_monitor_event.is_set():
        with state_lock:
            st = state
        if st != State.RECORDING:
            break
        now = time.perf_counter()
        if now >= next_beep:
            beep_recording_reminder()
            next_beep = now + RECORDING_REMINDER_INTERVAL_S
        with recording_audio_lock:
            sc = recording_sample_count
            ssq = recording_sum_sq
            pk = recording_peak_abs
            nc = len(audio_chunks)
        rem = _next_recording_reminder_in_s()
        if config and config.minimal:
            d = format_recording_hud_minimal(sc, ssq, pk, nc, SAMPLE_RATE, rem)
        elif ptt_status_box is not None and config and not config.minimal:
            tail = _recording_tail_samples()
            inner_w = max(16, voxium_panel_width(console) - 4)
            d = build_recording_hud_rich(
                sc,
                ssq,
                pk,
                nc,
                SAMPLE_RATE,
                rem,
                tail,
                panel_inner_width=inner_w,
            )
        else:
            d = format_recording_hud(sc, ssq, pk, nc, SAMPLE_RATE, rem)
        _write_recording_hud(d)
        if recording_monitor_event.wait(RECORDING_HUD_INTERVAL_S):
            break


def _get_input_mode() -> str:
    with input_mode_lock:
        return "vox" if input_mode == "vox" else "ptt"


def _set_input_mode(mode: str) -> None:
    global input_mode
    with input_mode_lock:
        input_mode = "vox" if (mode or "").lower() == "vox" else "ptt"
    m = "vox" if input_mode == "vox" else "ptt"
    if ptt_status_box is not None and config is not None and not config.minimal:
        ptt_status_box.set_input_mode_for_footer(m)


def _vox_ring_append(mono: np.ndarray) -> None:
    global vox_ring
    with vox_hud_lock:
        vox_ring = mono if not vox_ring.size else np.concatenate([vox_ring, mono])
        if vox_ring.size > VOX_HUD_RING_MAX:
            vox_ring = vox_ring[-VOX_HUD_RING_MAX:].copy()


def vox_audio_callback(indata, _frames, _time_info, status) -> None:
    global vox_chunker, state
    if status and str(status):
        pass
    ch = indata.copy().ravel()
    if ch.size:
        _vox_ring_append(ch.astype(np.float32, copy=False))
    chc = vox_chunker
    if chc is None or ch.size == 0:
        return
    for utt in chc.feed(ch):
        if not has_speech(utt, SAMPLE_RATE, threshold=0.012, segment_ms=50):
            continue
        with state_lock:
            st = state
        if st == State.IDLE:
            with state_lock:
                state = State.TRANSCRIBING
            paste_target = get_active_window()
            threading.Thread(
                target=transcribe_and_paste,
                args=(utt,),
                kwargs={"source": "vox", "paste_target": paste_target},
                daemon=True,
            ).start()
        else:
            with vox_pending_lock:
                vox_pending_audio.append((utt, get_active_window()))


def _vox_monitor_loop() -> None:
    while not vox_monitor_event.is_set() and vox_stream is not None:
        with vox_hud_lock:
            r = vox_ring
            t = r[-min(48_000, r.size) :] if r.size else r
        tail = t.copy() if t.size else np.empty(0, dtype=np.float32)
        sc = int(tail.size) if tail.size else 0
        ssq = float(np.sum(tail * tail)) if tail.size else 0.0
        pk = float(np.max(np.abs(tail))) if tail.size else 0.0
        st_lbl = vox_chunker.state_label() if vox_chunker else "idle"
        if config and config.minimal:
            d: str | RenderableType = (
                f"VOX  {st_lbl}  ·  pk{pk:5.2f}  {sc // max(1, SAMPLE_RATE):4.1f}s"
            )
        elif ptt_status_box is not None and config and not config.minimal and tail.size:
            inner_w = max(16, voxium_panel_width(console) - 4)
            d = build_recording_hud_rich(
                sc,
                ssq,
                pk,
                0,
                SAMPLE_RATE,
                None,
                tail,
                panel_inner_width=inner_w,
            )
        else:
            d = f"VOX  {st_lbl}  ·  {sc} smpl  ·  pk {pk:.3f}"
        _write_recording_hud(d)
        if vox_monitor_event.wait(RECORDING_HUD_INTERVAL_S):
            break


def _end_vox_hud_line() -> None:
    if ptt_status_box is not None:
        ptt_status_box.update_recording_hud(" ")


def _stop_vox_listening() -> None:
    """Close VOX capture stream, chunker, HUD, clear queue (e.g. mode change or exit)."""
    global vox_stream, vox_chunker, vox_monitor_thread, vox_ring
    vox_monitor_event.set()
    t = vox_monitor_thread
    if t and t.is_alive():
        t.join(timeout=RECORDING_HUD_THREAD_JOIN_S)
    vox_monitor_thread = None
    if vox_stream:
        try:
            vox_stream.stop()
        except Exception:
            pass
        try:
            vox_stream.close()
        except Exception:
            pass
    vox_stream = None
    vox_chunker = None
    vox_ring = np.empty(0, dtype=np.float32)
    with vox_pending_lock:
        vox_pending_audio.clear()
    _end_vox_hud_line()
    beep_stop()


def _start_vox_listening() -> bool:
    global vox_stream, vox_chunker, vox_monitor_thread, vox_ring, stream, target_window
    if stream is not None:
        cli_log(
            "PTT still holds the capture device. Release the key, then try VOX again.",
            "warning",
        )
        beep_error()
        return False
    _stop_morse_audio()
    vox_ring = np.empty(0, dtype=np.float32)
    vox_chunker = UtteranceChunker(SAMPLE_RATE)
    vox_stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=vox_audio_callback
    )
    vox_stream.start()
    vox_monitor_event.clear()
    vox_monitor_thread = threading.Thread(
        target=_vox_monitor_loop, daemon=True, name="VoxiumVoxHUD"
    )
    vox_monitor_thread.start()
    mhk = (config.mode_hotkey if config else DEFAULT_HOTKEYS["mode"]).upper()  # type: ignore[union-attr]
    rkh = (config.hotkey if config else DEFAULT_HOTKEYS["record"]).upper()  # type: ignore[union-attr]
    beep(660.0, 0.09, 0.1)
    de = f"Open mic  ·  {mhk} → PTT  ·  {rkh} idle in VOX  ·  gating, copy."
    if ptt_status_box is not None and not (config and config.minimal):
        ptt_status_box.set_status(
            STATUS_VOX_OPEN,
            de,
            recording_hud=" ",
        )
    else:
        show_status(STATUS_VOX_OPEN, de)
    # Like PTT's target at start of a take — default paste target when VOX is armed.
    target_window = get_active_window()
    return True


def _resolved_window_title() -> str:
    custom = (os.environ.get("VOXIUM_WINDOW_TITLE") or "").strip()
    return custom or VOXIUM_WINDOW_TITLE


def set_terminal_title() -> None:
    """Set tab/window title: env VOXIUM_WINDOW_TITLE or :data:`VOXIUM_WINDOW_TITLE` (no PTT state in title).

    Windows: ``SetConsoleTitleW`` (reliable in Windows Terminal / conhost). Other platforms: xterm
    OSC 0 on the first available TTY among stdout/stderr. Raw ``os.write(2, …)`` is avoided so the
    sequence is not sent when stderr is a pipe or a file.
    """
    title = _resolved_window_title()
    if sys.platform == "win32":
        try:
            import ctypes

            w = ctypes.windll.kernel32.GetConsoleWindow
            s = ctypes.windll.kernel32.SetConsoleTitleW
            if w and w() and s and title:
                s(title)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    _emit_osc0_window_title(title)


def _emit_osc0_window_title(title: str) -> None:
    if not title:
        return
    data = f"\x1b]0;{title}\x07".encode("utf-8", "replace")
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        if stream is None:
            continue
        try:
            is_tty = stream.isatty()
        except (OSError, ValueError, AttributeError):
            continue
        if not is_tty:
            continue
        buf = getattr(stream, "buffer", None)
        if buf is None:
            continue
        try:
            buf.write(data)
            buf.flush()
        except (OSError, TypeError, ValueError):
            continue
        return


def _standby_telemetry_context() -> dict:
    """
    Live values for the green-strip standby line: stream rate/channels, last on-wire / key-when
    different from ``last_audio_capture_info``, and after a successful decode, last pass RTF, last
    take peak/RMS, and model name (see :mod:`voxium.standby_telemetry`). Device name is not included
    (``/mic`` for full capture path); keeps the on-station line short.
    """
    m = last_transcription_metrics
    d: dict = {}

    cap = last_audio_capture_info
    if isinstance(cap, dict):
        st = cap.get("stream") if isinstance(cap.get("stream"), dict) else {}
        if st.get("samplerate") is not None:
            try:
                d["sample_rate_hz"] = int(float(st["samplerate"]))
            except (TypeError, ValueError):
                pass
        if st.get("channels") is not None:
            try:
                d["channels"] = int(st["channels"])
            except (TypeError, ValueError):
                pass
        fmt = cap.get("format") if isinstance(cap.get("format"), dict) else {}
        if "sample_rate_hz" not in d and fmt.get("sample_rate_hz") is not None:
            try:
                d["sample_rate_hz"] = int(fmt["sample_rate_hz"])
            except (TypeError, ValueError):
                pass
        if "channels" not in d and fmt.get("channels") is not None:
            try:
                d["channels"] = int(fmt["channels"])
            except (TypeError, ValueError):
                pass
        rec = cap.get("recording") if isinstance(cap.get("recording"), dict) else {}
        if rec.get("wall_seconds") is not None:
            try:
                d["last_ptt_wall_s"] = float(rec["wall_seconds"])
            except (TypeError, ValueError):
                pass
        if rec.get("capture_seconds") is not None:
            try:
                d["last_ptt_audio_s"] = float(rec["capture_seconds"])
            except (TypeError, ValueError):
                pass
        if rec.get("peak_abs") is not None:
            try:
                d["last_capture_peak"] = float(rec["peak_abs"])
            except (TypeError, ValueError):
                pass
        if rec.get("rms_dbfs") is not None:
            try:
                d["last_capture_rms_dbfs"] = float(rec["rms_dbfs"])
            except (TypeError, ValueError):
                pass

    if "sample_rate_hz" not in d:
        d["sample_rate_hz"] = SAMPLE_RATE
    if "channels" not in d:
        d["channels"] = 1

    if isinstance(m, dict) and m.get("transcription_seconds") is not None:
        d["has_last_decode"] = True
        if m.get("realtime_factor") is not None:
            d["last_realtime_factor"] = m.get("realtime_factor")
        if m.get("audio_seconds") is not None:
            d["last_audio_seconds"] = m.get("audio_seconds")
        mod = m.get("model")
        if isinstance(mod, dict) and mod.get("name"):
            d["last_model_name"] = str(mod.get("name"))[:32]

    last_text = _latest_transcript_text()
    if last_text:
        d["last_transcript_text"] = last_text
    if morse_audio_controller is not None:
        d["morse_audio_playing"] = morse_audio_controller.is_playing()

    if config is not None and getattr(config, "ux_chatter", False):
        from voxium.ux_chatter import get_ux_chatter_wit

        w = (get_ux_chatter_wit() or "").strip()
        if w:
            d["ux_chatter_wit"] = w
    return d


def show_status(status: str, detail: str = ""):

    if is_client_shutting_down():
        return
    if config is None:
        return
    if not config.minimal and ptt_status_box is not None:
        ptt_status_box.set_status(
            status,
            detail,
            recording_hud="" if status_uses_recording_hud_line(status) else None,
        )
        return
    if not config.minimal and ptt_status_box is None:
        console.print(
            build_status_box_panel(
                status, detail, box_width=voxium_panel_width(console)
            )
        )
        return

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\n" * 8)
    sys.stdout.write(f"{'─' * 40}\n")
    sys.stdout.write(f"{status:^40}\n")
    if detail:

        detail = detail[:36] + "..." if len(detail) > 36 else detail
        sys.stdout.write(f"{detail:^40}\n")
    sys.stdout.write(f"{'─' * 40}\n")
    sys.stdout.flush()


def _arm_status_after_log_scrollback() -> None:
    """After the blue PTT&VOX log :class:`Panel` (post-freeze), show one fresh green status — not a re-render of the take chain.

    :func:`freeze_before_external_output` commits the prior live session (on-station, on-air, copy, …) to
    the scrollback. Rebuilding that same ``session_steps`` here would stack a second identical green
    Voxium box and put the log out of order. Instead, reset to a single **ready** line: VOX open
    (when VOX is armed) or on-station for PTT — same as the end of a normal take.
    """
    if is_client_shutting_down() or not config or config.minimal:
        return
    if _get_input_mode() == "vox" and vox_stream is not None:
        mhk = (config.mode_hotkey if config else DEFAULT_HOTKEYS["mode"]).upper()  # type: ignore[union-attr]
        vde = f"Open mic  ·  {mhk} → PTT  ·  utterance gating, copy."
        if ptt_status_box is not None:
            ptt_status_box.set_status(STATUS_VOX_OPEN, vde, recording_hud=" ")
        else:
            show_status(STATUS_VOX_OPEN, vde)
    else:
        show_status(STATUS_VOX_ON_STATION, "Standing by.")


def _print_shutdown_farewell() -> None:
    """
    One line on exit (Ctrl+C path). When ``--ux-chatter`` is on and the UX stack answers in time, the
    line is model-written; else :data:`_VOXIUM_SHUTDOWN_DEFAULT`. The line is shown in a violet
    Downlink box (e.g. ``Voxium: 73 / 10-7``) unless ``--minimal``. Call this **before**
    :func:`cleanup_client_runtime` so the managed UX ``llama-server`` is still on loopback.
    """
    if config is not None and getattr(config, "quiet", False):
        return
    line = _VOXIUM_SHUTDOWN_DEFAULT
    if config is not None and getattr(config, "ux_chatter", False):
        from voxium.ux_chatter import fetch_ux_shutdown_line, is_ux_chatter_wanted

        fc = getattr(config, "file_config", None) or {}
        if is_ux_chatter_wanted(cli_enabled=True, file_config=fc):
            out = fetch_ux_shutdown_line(fc, cli_enabled=True)
            if out:
                line = out
    if config is not None and getattr(config, "minimal", False):
        print(f"\n{line}")
        return
    with _ux_chatter_downlink_lock:
        print_agent_telemetry_panel(
            console,
            [(line, "info")],
            downlink_subtitle="sign-off",
        )


def _ptt_log_panel_subtitle_line(transcribed: str) -> str:
    """Dim footer for the blue transcription panel; dynamic when ``--ux-chatter`` responds."""
    t = (transcribed or "").strip()
    if not t:
        return _PTT_LOG_PANEL_SUBTITLE_DEFAULT
    if not config or not getattr(config, "ux_chatter", False):
        return _PTT_LOG_PANEL_SUBTITLE_DEFAULT
    from voxium.ux_chatter import fetch_ux_log_subtitle, is_ux_chatter_wanted

    fc = getattr(config, "file_config", None) or {}
    if not is_ux_chatter_wanted(cli_enabled=True, file_config=fc):
        return _PTT_LOG_PANEL_SUBTITLE_DEFAULT
    out = fetch_ux_log_subtitle(fc, t, cli_enabled=True)
    s = (out or "").strip()
    return s if s else _PTT_LOG_PANEL_SUBTITLE_DEFAULT


def log_transcription_summary(text: str, metrics: dict | None):

    if is_client_shutting_down():
        return
    if config and config.minimal:
        return

    if ptt_status_box is not None and config and not config.minimal:
        ptt_status_box.freeze_before_external_output()

    w_box = voxium_panel_width(console)
    # Panel border 2 + horizontal padding 1+1 — extra width for metrics columns on small TTYs.
    inner_w = max(4, w_box - 4)
    raw = (text or "").strip()
    transcript = Text(raw or "(empty)", style="#f8fafc")
    metrics_block = build_ptt_log_metrics_layout(
        metrics,
        available_width=inner_w,
    )
    content = Group(
        Text("Transcribed text", style="bold #7dd3fc"),
        Panel(transcript, border_style="#334155", padding=(0, 1), width=inner_w),
        Text("Inference metrics", style="bold #a7f3d0"),
        metrics_block,
    )
    with _ux_chatter_downlink_lock:
        sub_plain = _ptt_log_panel_subtitle_line(raw)
        console.print()
        console.print(
            Panel(
                content,
                title="[bold #38bdf8]Voxium[/bold #38bdf8]",
                title_align="left",
                subtitle=f"[dim]{escape(sub_plain)}[/dim]",
                subtitle_align="left",
                border_style="#38bdf8",
                padding=(1, 1),
                width=w_box,
            )
        )
    if config and not config.minimal:
        _arm_status_after_log_scrollback()


def get_active_window():

    try:
        if SYSTEM == "Linux":
            return subprocess.check_output(
                ["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL
            ).strip()
        elif SYSTEM == "Windows":
            import ctypes

            return ctypes.windll.user32.GetForegroundWindow()
        elif SYSTEM == "Darwin":
            script = 'tell application "System Events" to get name of first process whose frontmost is true'
            result = subprocess.check_output(
                ["osascript", "-e", script], stderr=subprocess.DEVNULL
            )
            return result.strip()
    except Exception:
        return None
    return None


def focus_window(window_id):

    if not window_id:
        return
    try:
        if SYSTEM == "Linux":
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", window_id],
                stderr=subprocess.DEVNULL,
            )
        elif SYSTEM == "Windows":
            import ctypes

            ctypes.windll.user32.SetForegroundWindow(window_id)
        elif SYSTEM == "Darwin":

            script = f'tell application "{window_id.decode()}" to activate'
            subprocess.run(["osascript", "-e", script], stderr=subprocess.DEVNULL)
    except Exception:
        pass


def is_terminal_window(window_id) -> bool:

    try:
        if SYSTEM == "Linux":
            wm_class = (
                subprocess.check_output(
                    ["xprop", "-id", window_id, "WM_CLASS"], stderr=subprocess.DEVNULL
                )
                .decode()
                .lower()
            )
            return any(t in wm_class for t in TERMINALS.get("Linux", []))

        elif SYSTEM == "Windows":
            import ctypes

            buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(window_id, buffer, 256)
            title = buffer.value.lower()
            class_buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(window_id, class_buffer, 256)
            class_name = class_buffer.value
            return any(
                t.lower() in title or t.lower() in class_name.lower()
                for t in TERMINALS.get("Windows", [])
            )

        elif SYSTEM == "Darwin":

            app_name = (
                window_id.decode() if isinstance(window_id, bytes) else str(window_id)
            )
            return any(
                t.lower() in app_name.lower() for t in TERMINALS.get("Darwin", [])
            )
    except Exception:
        pass
    return False


def query_default_audio_input() -> tuple[dict | None, dict | None, str | None]:

    try:
        device = sd.query_devices(kind="input")
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"

    host_api = None
    try:
        host_api_index = device.get("hostapi")
        if host_api_index is not None:
            host_api = sd.query_hostapis(host_api_index)
    except Exception:
        host_api = None

    return device, host_api, None


def describe_audio_capture_source() -> str:

    device, host_api, error = query_default_audio_input()
    if error:
        return f"unavailable ({error})"
    name = device.get("name") if device else None
    host_api_name = host_api.get("name") if host_api else None
    default_rate = (
        round_audio_float(device.get("default_samplerate"), 0) if device else None
    )
    details = [name or "default input"]
    if host_api_name:
        details.append(f"via {host_api_name}")
    if default_rate:
        details.append(f"default={default_rate:.0f} Hz")
    return ", ".join(details)


def build_audio_capture_info(stream_obj: sd.InputStream | None = None) -> dict:

    device, host_api, error = query_default_audio_input()
    backend = {
        "library": "python-sounddevice",
        "api": "PortAudio",
        "sounddevice_version": getattr(sd, "__version__", None),
    }
    try:
        portaudio_version = sd.get_portaudio_version()
        if isinstance(portaudio_version, tuple) and len(portaudio_version) >= 2:
            backend["portaudio_version"] = portaudio_version[0]
            backend["portaudio_version_text"] = portaudio_version[1]
    except Exception:
        pass

    device_info = {}
    if device:
        device_info = {
            "index": device.get("index"),
            "name": device.get("name"),
            "max_input_channels": device.get("max_input_channels"),
            "default_samplerate_hz": round_audio_float(
                device.get("default_samplerate")
            ),
            "default_low_input_latency_seconds": round_audio_float(
                device.get("default_low_input_latency")
            ),
            "default_high_input_latency_seconds": round_audio_float(
                device.get("default_high_input_latency")
            ),
        }

    host_api_info = {}
    if host_api:
        host_api_info = {
            "index": device.get("hostapi") if device else None,
            "name": host_api.get("name"),
            "default_input_device": host_api.get("default_input_device"),
        }

    stream_info = {}
    if stream_obj:
        for attr in ("samplerate", "channels", "dtype", "latency", "blocksize"):
            try:
                value = getattr(stream_obj, attr)
            except Exception:
                continue
            key = "latency_seconds" if attr == "latency" else attr
            stream_info[key] = json_safe_audio_value(value)

    capture_info = {
        "backend": backend,
        "device": device_info,
        "host_api": host_api_info,
        "format": {
            "sample_rate_hz": SAMPLE_RATE,
            "channels": 1,
            "dtype": "float32",
        },
        "stream": stream_info,
    }
    if error:
        capture_info["error"] = error
    return capture_info


def finalize_audio_capture_info(
    captured_frames: int,
    chunks: int,
    wall_seconds: float | None,
    *,
    peak_abs: float | None = None,
    rms_dbfs: float | None = None,
) -> dict:
    base = current_audio_capture_info or build_audio_capture_info()
    return enrich_capture_with_recording(
        base,
        captured_frames,
        chunks,
        wall_seconds,
        list(audio_capture_statuses),
        SAMPLE_RATE,
        peak_abs=peak_abs,
        rms_dbfs=rms_dbfs,
    )


def audio_callback(indata, _frames, _time_info, status):
    global audio_chunks, audio_capture_statuses, recording_sum_sq, recording_sample_count, recording_peak_abs
    if status:
        status_text = str(status)
        if status_text and status_text not in audio_capture_statuses:
            audio_capture_statuses.append(status_text)
            del audio_capture_statuses[:-5]
    chunk = indata.copy()
    t = float(np.sum(chunk * chunk))
    p = float(np.max(np.abs(chunk)))
    with recording_audio_lock:
        audio_chunks.append(chunk)
        recording_sum_sq += t
        recording_sample_count += int(chunk.size)
        if p > recording_peak_abs:
            recording_peak_abs = p


def start_recording():

    global stream, audio_chunks, target_window, current_audio_capture_info
    global audio_capture_statuses, recording_started_at
    global recording_sum_sq, recording_sample_count, recording_peak_abs, recording_monitor_thread
    _stop_morse_audio()
    with recording_audio_lock:
        audio_chunks = []
    recording_sum_sq = 0.0
    recording_sample_count = 0
    recording_peak_abs = 0.0
    audio_capture_statuses = []
    target_window = get_active_window()
    recording_started_at = time.perf_counter()
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback
    )
    stream.start()
    current_audio_capture_info = build_audio_capture_info(stream)
    recording_monitor_event.clear()
    recording_monitor_thread = threading.Thread(
        target=_recording_monitor_loop, daemon=True, name="VoxiumRecordingHUD"
    )
    recording_monitor_thread.start()
    beep_start()
    rec_hk = (config.hotkey if config else DEFAULT_HOTKEYS["record"]).upper()
    set_terminal_title()
    show_status(
        STATUS_PTT_ACTIVE,
        (
            f"{rec_hk} drops carrier · live meter · "
            f"still-air reminder: 2 blips every {RECORDING_REMINDER_INTERVAL_S:.0f}s (not PTT)"
        ),
    )


def stop_recording() -> np.ndarray:

    global stream, last_audio_capture_info, recording_started_at, recording_monitor_thread
    stop_time = time.perf_counter()
    # Wake the HUD thread first so it is not stuck in wait(RECORDING_HUD_INTERVAL_S) during stream teardown.
    recording_monitor_event.set()
    if stream:
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        stream = None
    tmon = recording_monitor_thread
    if tmon and tmon.is_alive():
        tmon.join(timeout=RECORDING_HUD_THREAD_JOIN_S)
    recording_monitor_thread = None
    _end_recording_hud_line()
    beep_stop()
    set_terminal_title()
    show_status(
        STATUS_EDGE_INFERENCE,
        _status_detail_for_edge_inference(rexmit=False),
    )

    with recording_audio_lock:
        captured_frames = recording_sample_count
        n_chunks = len(audio_chunks)
        to_concat = list(audio_chunks) if audio_chunks else []
        ssq = recording_sum_sq
        sc = recording_sample_count
        pk = recording_peak_abs

    wall_seconds = (
        stop_time - recording_started_at if recording_started_at is not None else None
    )
    peak_arg: float | None = None
    rms_arg: float | None = None
    if sc and captured_frames:
        rms = float(np.sqrt(max(0.0, ssq / max(1, sc))))
        peak_arg = float(pk)
        rms_arg = rms_to_dbfs(rms)
    last_audio_capture_info = finalize_audio_capture_info(
        captured_frames,
        n_chunks,
        wall_seconds,
        peak_abs=peak_arg,
        rms_dbfs=rms_arg,
    )

    if not to_concat:
        return np.array([], dtype=np.float32)
    return np.concatenate(to_concat).flatten()


def is_client_shutting_down() -> bool:

    return client_shutdown_event.is_set()


def cleanup_client_runtime():

    global stream, state, recording_monitor_thread, ptt_status_box, managed_llama_cpp, morse_audio_controller, _llama_cpp_polish_ready_checked, _llama_cpp_ux_ready_checked
    _stop_morse_audio()
    morse_audio_controller = None
    _stop_vox_listening()
    _set_input_mode("ptt")
    client_shutdown_event.set()
    _ptt_key_tracker.reset()
    recording_monitor_event.set()
    tmon = recording_monitor_thread
    if tmon and tmon.is_alive():
        try:
            tmon.join(timeout=1.0)
        except Exception:
            pass
    _end_recording_hud_line()
    try:
        if stream:
            stream.stop()
            stream.close()
            stream = None
    except Exception:
        stream = None
    with state_lock:
        state = State.IDLE
    try:
        set_terminal_title()
    except Exception:
        pass
    if ptt_status_box is not None:
        try:
            ptt_status_box.close()
        finally:
            ptt_status_box = None
    if managed_llama_cpp is not None:
        try:
            stop_managed_llama_cpp(managed_llama_cpp)
        finally:
            managed_llama_cpp = None
    _llama_cpp_polish_ready_checked = False
    _llama_cpp_ux_ready_checked = False


def maybe_polish_transcript(raw_text: str) -> str:
    """If polish is enabled, POST the same host's ``/polish``; merge metrics into STT. On any failure, return *raw_text* (paste still proceeds)."""
    global last_transcription_metrics
    if not config or not getattr(config, "polish", True):
        return raw_text
    t = (raw_text or "").strip()
    if not t:
        return raw_text
    ensure_llama_cpp_for_polish()
    url = get_server_endpoint_url(config.server_url, "polish")
    body: dict[str, Any] = {"text": t, "backend": "llama.cpp"}
    pm = getattr(config, "polish_model", None)
    attempted_model = str(pm or POLISH_DEFAULT_MODEL)
    try:
        attempted_model = validate_polish_model_tag(attempted_model)
    except ValueError:
        pass

    def _merge_polish_metrics(
        *,
        polish: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> None:
        global last_transcription_metrics
        base: dict[str, Any]
        if isinstance(last_transcription_metrics, dict):
            base = dict(last_transcription_metrics)
        else:
            base = {}
        if isinstance(extra_metrics, dict):
            base.update(extra_metrics)
        if isinstance(polish, dict):
            base["polish"] = polish
        last_transcription_metrics = base

    def _record_polish_fallback(error: str) -> None:
        pol = {
            "enabled": True,
            "attempted": True,
            "applied": False,
            "model": attempted_model,
            "backend": "llama.cpp",
            "seconds": None,
            "tokens_in": None,
            "tokens_out": None,
            "error": error[:300],
        }
        _merge_polish_metrics(
            polish=pol,
            extra_metrics={
                "polish": {
                    "model": attempted_model,
                    "backend": "llama.cpp",
                    "seconds": None,
                    "error": pol["error"],
                    "applied": False,
                }
            },
        )

    body["model"] = attempted_model
    try:
        pto = float(getattr(config, "polish_timeout", 25.0) or 25.0)
    except (TypeError, ValueError):
        pto = 25.0
    try:
        resp = requests.post(url, json=body, timeout=pto)
    except Exception as exc:
        _record_polish_fallback(f"{type(exc).__name__}: {exc}")
        if not config.minimal:
            msg = str(exc)[:300]
            print_agent_telemetry_panel(
                console,
                [
                    (
                        f"Re-encode: not applied ({type(exc).__name__}: {msg}). Pasting STT as-is, copy.",
                        "warning",
                    )
                ],
                downlink_subtitle="re-encode",
            )
        return raw_text
    if resp.status_code != 200:
        detail = ""
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("error") or "").strip()
        if not detail:
            detail = f"HTTP {resp.status_code}"
        else:
            detail = f"HTTP {resp.status_code}: {detail}"
        _record_polish_fallback(detail)
        if not config.minimal:
            print_agent_telemetry_panel(
                console,
                [
                    (
                        f"Re-encode: {detail} — pasting STT as-is, copy.",
                        "warning",
                    )
                ],
                downlink_subtitle="re-encode",
            )
        return raw_text
    try:
        data = resp.json()
    except Exception:
        _record_polish_fallback("Invalid JSON from /polish")
        return raw_text
    if not isinstance(data, dict):
        _record_polish_fallback("Invalid JSON payload from /polish")
        return raw_text
    out = (data.get("text") or "").strip() or raw_text
    extra = data.get("metrics")
    pl = data.get("polish")
    _merge_polish_metrics(
        polish=pl if isinstance(pl, dict) else None,
        extra_metrics=extra if isinstance(extra, dict) else None,
    )
    if not config.minimal:
        pol = pl if isinstance(pl, dict) else {}
        try:
            sec = float(pol.get("seconds")) if pol.get("seconds") is not None else None
        except (TypeError, ValueError):
            sec = None
        app = pol.get("applied", True)
        try:
            app = bool(app)
        except Exception:
            app = True
        model = str(pol.get("model") or attempted_model)
        tail = f"{sec:.2f}s" if isinstance(sec, float) else "n/a"
        tok = format_polish_usage_suffix(pol)
        print_agent_telemetry_panel(
            console,
            [
                (
                    f"Re-encode: {model} · {tail} · applied={app}{tok} (local second pass, copy).",
                    "info",
                )
            ],
            downlink_subtitle="re-encode",
        )
    return out


def transcribe_server(wav_buffer: io.BytesIO, capture_info: dict | None = None) -> str:

    global last_transcription_metrics
    last_transcription_metrics = None
    wav_buffer.seek(0)

    if not is_loopback_url(config.server_url):
        raise ValueError(
            "Remote transcription URLs are not supported; use http://127.0.0.1:8002/transcribe"
        )

    files = {"file": ("audio.wav", wav_buffer, "audio/wav")}
    data = {}
    if config.language:
        data["language"] = config.language
    if config.model:
        data["model"] = config.model
    if capture_info:
        data["capture_metadata"] = json.dumps(capture_info)

    try:
        resp = requests.post(config.server_url, files=files, data=data, timeout=240)
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(http_error_detail_text(e)) from e

    try:
        result = resp.json()
        metrics = result.get("metrics") if isinstance(result, dict) else None
        if isinstance(metrics, dict) and capture_info:
            metrics.setdefault("capture", capture_info)
        elif capture_info:
            metrics = {"capture": capture_info}
        last_transcription_metrics = metrics
        return result.get("text", "").strip()
    except Exception:
        last_transcription_metrics = {"capture": capture_info} if capture_info else None
        return resp.text.strip()


def transcribe(audio: np.ndarray) -> str:

    global last_transcription_metrics
    last_transcription_metrics = None

    if len(audio) < SAMPLE_RATE * 0.5:
        return ""

    if not has_speech(audio, SAMPLE_RATE):
        return ""

    audio_int16 = (audio * 32767).astype(np.int16)
    wav_buffer = io.BytesIO()
    wavfile.write(wav_buffer, SAMPLE_RATE, audio_int16)

    h = get_transcript_history()
    if h is not None:
        h.save_pending_audio(wav_buffer)

    wav_buffer.seek(0)

    _stt_t0 = time.perf_counter()
    _stt_err: str | None = None
    _stt_ok = False
    try:
        text = transcribe_server(wav_buffer, last_audio_capture_info)
        _stt_ok = True
    except Exception as exc:
        _stt_err = str(exc)[:200]
        raise
    finally:
        _stt_wall = time.perf_counter() - _stt_t0
        _stt_metrics = (
            last_transcription_metrics
            if isinstance(last_transcription_metrics, dict)
            else None
        )
        _stt_model_name = ""
        if isinstance(_stt_metrics, dict):
            mblk = _stt_metrics.get("model")
            if isinstance(mblk, dict):
                _stt_model_name = str(mblk.get("name") or "")
        if not _stt_model_name:
            _stt_model_name = str(getattr(config, "model", "") or "")
        polish_profile.record_stt(
            model=_stt_model_name,
            client_wall_seconds=_stt_wall,
            metrics=_stt_metrics,
            ok=_stt_ok,
            error=_stt_err,
        )

    if last_transcription_metrics is None:
        last_transcription_metrics = {}
    if text and config and getattr(config, "polish", True):
        text = maybe_polish_transcript(text)

    h2 = get_transcript_history()
    if h2 is not None and text:
        h2.clear_pending_audio()

    return text


def paste_text(text: str):

    global _last_paste_time
    if is_client_shutting_down():
        return

    now = time.time() * 1000
    if now - _last_paste_time < PASTE_DEBOUNCE_MS:
        if DEBUG_PASTE:
            print(
                f"[DEBUG] paste_text BLOCKED by debounce (delta={now - _last_paste_time:.0f}ms)"
            )
        return
    _last_paste_time = now

    if DEBUG_PASTE:
        import traceback

        print(f"[DEBUG] paste_text called at {now:.0f}ms")
        print(f"[DEBUG] text length: {len(text)}, preview: {text[:50]!r}")
        print(f"[DEBUG] call stack:\n{''.join(traceback.format_stack()[-4:-1])}")

    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        old_clipboard = None

    pyperclip.copy(text)
    time.sleep(0.05)

    focus_window(target_window)
    time.sleep(0.05)

    is_terminal = is_terminal_window(target_window) if target_window else False

    if SYSTEM == "Linux":
        key = "ctrl+shift+v" if is_terminal else "ctrl+v"
        if DEBUG_PASTE:
            print(f"[DEBUG] xdotool sending: {key} (is_terminal={is_terminal})")

        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "--delay", "50", key],
            stderr=subprocess.DEVNULL,
        )
        if DEBUG_PASTE:
            print("[DEBUG] xdotool completed")

    elif SYSTEM == "Windows":
        import pyautogui

        if is_terminal:

            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.hotkey("ctrl", "v")

    elif SYSTEM == "Darwin":
        import pyautogui

        pyautogui.hotkey("command", "v", interval=0.05)

    if old_clipboard:

        def restore():

            delay = min(3.0, max(1.0, 1.0 + len(text) * 0.0001))
            time.sleep(delay)
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass

        threading.Thread(target=restore, daemon=True).start()


def transcribe_and_paste(
    audio: np.ndarray, *, source: str = "ptt", paste_target: Any | None = None
):
    """
    *paste_target* — foreground window to focus before paste (PTT sets this via
    :func:`start_recording` on the main thread; VOX passes it per utterance from
    :func:`get_active_window` in the audio callback or queue).
    """
    global state, target_window
    if paste_target is not None:
        target_window = paste_target
    # Skip the post-take sleep when we already re-armed on an error path (fast VOX/PTT).
    skip_final_pacing = False
    rearm_after_final_pacing = False
    try:
        text = transcribe(audio)
        metrics = last_transcription_metrics
        if metrics is not None:
            _record_persistent_stats(metrics, source=source)
        if is_client_shutting_down():
            return
        if text and not is_hallucination(text):
            set_spectrum_from_mono_float(audio, SAMPLE_RATE)
            # Record before paste so /history and replay work even if clipboard or focus/paste fails.
            h = get_transcript_history()
            if h is not None:
                h.add(text, source=source)
            paste_text(" " + text)
            beep_success()
            set_terminal_title()
            copy_readback = take_readback()
            if config and getattr(config, "ux_chatter", False):
                from voxium.ux_chatter import (
                    is_ux_chatter_wanted,
                    sync_ux_chatter_for_transcript,
                )

                fc = getattr(config, "file_config", None) or {}
                if is_ux_chatter_wanted(
                    cli_enabled=bool(getattr(config, "ux_chatter", False)),
                    file_config=fc,
                ):
                    uxf = sync_ux_chatter_for_transcript(
                        text,
                        fc,
                        bool(getattr(config, "ux_chatter", False)),
                        on_complete=_ux_chatter_on_complete,
                    )
                    copy_readback = _ux_resolve_copy_wit(text, uxf)
            show_status(STATUS_VOX_COPY, copy_readback)
            log_transcription_summary(text, metrics)
        else:
            beep_error()
            set_terminal_title()
            h_clear = get_transcript_history()
            if h_clear is not None:
                h_clear.clear_pending_audio()
            if config and config.minimal:
                print("Voxium: no speech detected.", file=sys.stderr)
            else:
                show_status(
                    STATUS_NO_AUDIO,
                    "No speech detected in that take — standing by, copy.",
                )
                rearm_after_final_pacing = True
            skip_final_pacing = False
    except Exception as e:
        if is_client_shutting_down():
            return
        beep_error()
        set_terminal_title()
        if config and config.minimal:
            print(f"Voxium: transcription failed: {e!s}"[:500], file=sys.stderr)
        else:
            cli_log(f"Transcription failed: {str(e)[:200]}", "error")
            _arm_status_after_log_scrollback()
        skip_final_pacing = True
    finally:
        with state_lock:
            state = State.IDLE
        if is_client_shutting_down():
            return
        nxt: np.ndarray | None = None
        nxt_paste: Any = None
        with vox_pending_lock:
            if vox_pending_audio:
                nxt, nxt_paste = vox_pending_audio.popleft()
        if nxt is not None and _get_input_mode() == "vox" and vox_stream is not None:
            with state_lock:
                state = State.TRANSCRIBING
            threading.Thread(
                target=transcribe_and_paste,
                args=(nxt,),
                kwargs={"source": "vox", "paste_target": nxt_paste},
                daemon=True,
            ).start()
            return
        if not skip_final_pacing:
            time.sleep(1.5)
        if is_client_shutting_down():
            return
        set_terminal_title()
        # Successful takes: :func:`_arm_status_after_log_scrollback` in :func:`log_transcription_summary`
        # re-armed after the blue log; no-audio takes use the same short pause so the indicator is visible.
        if rearm_after_final_pacing:
            _arm_status_after_log_scrollback()


def get_hotkey(key_name: str):

    key_map = {key: getattr(keyboard.Key, key) for key in HOTKEY_ORDER}
    return key_map[normalize_hotkey_name(key_name)]


def _active_hotkey(action: str, fallback) -> object:
    if config is None:
        return fallback
    if action == "record":
        return get_hotkey(getattr(config, "hotkey", DEFAULT_HOTKEYS["record"]))
    if action == "recovery":
        return get_hotkey(
            getattr(config, "recovery_hotkey", DEFAULT_HOTKEYS["recovery"])
        )
    if action == "retry":
        return get_hotkey(getattr(config, "retry_hotkey", DEFAULT_HOTKEYS["retry"]))
    if action == "mode":
        return get_hotkey(getattr(config, "mode_hotkey", DEFAULT_HOTKEYS["mode"]))
    return fallback


def _current_hotkeys_for_slash() -> dict[str, str]:
    if config is None:
        return dict(DEFAULT_HOTKEYS)
    return {
        "record": normalize_hotkey_name(
            getattr(config, "hotkey", DEFAULT_HOTKEYS["record"])
        ),
        "recovery": normalize_hotkey_name(
            getattr(config, "recovery_hotkey", DEFAULT_HOTKEYS["recovery"])
        ),
        "retry": normalize_hotkey_name(
            getattr(config, "retry_hotkey", DEFAULT_HOTKEYS["retry"])
        ),
        "mode": normalize_hotkey_name(
            getattr(config, "mode_hotkey", DEFAULT_HOTKEYS["mode"])
        ),
    }


def _load_persistent_stats_for_slash() -> dict[str, Any]:
    return load_stats(config_stats_path())


def _record_persistent_stats(metrics: dict | None, *, source: str) -> None:
    try:
        record_stats(metrics, source=source, path=config_stats_path())
    except Exception as exc:
        cli_log(f"Stats counter update failed: {str(exc)[:160]}", "warning")


def _latest_transcript_text() -> str:
    h = get_transcript_history()
    if h is None:
        return ""
    text = h.text_by_display_index(1)
    return str(text or "").strip()


def _set_morse_audio_state(playing: bool) -> None:
    if ptt_status_box is not None and config and not config.minimal:
        ptt_status_box.set_morse_audio_state(playing)


def _ensure_morse_audio_controller() -> MorseAudioController:
    global morse_audio_controller
    if morse_audio_controller is None:
        morse_audio_controller = MorseAudioController(
            on_state_change=_set_morse_audio_state
        )
    return morse_audio_controller


def _stop_morse_audio() -> None:
    if morse_audio_controller is not None:
        morse_audio_controller.stop()


def _toggle_morse_audio_for_last_transcript() -> bool:
    controller = _ensure_morse_audio_controller()
    if controller.is_playing():
        controller.stop()
        return True
    text = _latest_transcript_text()
    if not text:
        beep_error()
        _set_morse_audio_state(False)
        return True
    if not controller.play_text(text):
        beep_error()
        _set_morse_audio_state(False)
    return True


def _finish_ptt_recording() -> None:
    global state
    with state_lock:
        if state != State.RECORDING:
            return
        state = State.TRANSCRIBING
    audio = stop_recording()
    threading.Thread(target=transcribe_and_paste, args=(audio,), daemon=True).start()


def create_record_hotkey_handlers(hotkey):

    def on_press(key):
        global state, _last_hotkey_time
        if key != _active_hotkey("record", hotkey):
            return
        # PTT record key is only active in push-to-talk mode; ignore in VOX (open mic) so F9 does
        # not grab the mic or start a PTT take while VOX holds the capture device.
        if _get_input_mode() != "ptt":
            return

        now = time.time() * 1000
        with state_lock:
            st = state
        action, _last_hotkey_time = handle_ptt_press(
            _ptt_key_tracker,
            now_ms=now,
            can_start=(st == State.IDLE),
            can_stop=(st == State.RECORDING),
            last_hotkey_time_ms=_last_hotkey_time,
            start_debounce_ms=HOTKEY_DEBOUNCE_PTT_START_MS,
            stop_debounce_ms=HOTKEY_DEBOUNCE_PTT_STOP_MS,
        )
        if action == PTT_ACTION_START:
            with state_lock:
                if state == State.IDLE:
                    state = State.RECORDING
                    start_recording()
        elif action == PTT_ACTION_STOP:
            _finish_ptt_recording()

    def on_release(key):
        if key != _active_hotkey("record", hotkey):
            return
        # Ignore release in VOX or while not actively recording.
        if _get_input_mode() != "ptt":
            _ptt_key_tracker.reset()
            return

        now = time.time() * 1000
        with state_lock:
            st = state
        action = handle_ptt_release(
            _ptt_key_tracker,
            now_ms=now,
            is_recording=(st == State.RECORDING),
        )
        if action == PTT_ACTION_STOP:
            _finish_ptt_recording()

    return on_press, on_release


def create_recovery_handler(recovery_key):

    def on_press(key):
        global target_window
        if key != _active_hotkey("recovery", recovery_key):
            return

        with state_lock:
            if state != State.IDLE:
                return

        h = get_transcript_history()
        if h is None:
            beep_error()
            return

        rep = h.next_replay_paste()
        if not rep:
            beep_error()
            show_status("❌ NO HISTORY", "Nothing to replay")
            return
        last_text, k, n = rep
        target_window = get_active_window()
        paste_text(" " + last_text)
        beep_success()
        set_terminal_title()
        show_status(STATUS_VOX_LAST_COPY, f"Replay {k}/{n} · {last_text[:50]}")

    return on_press


def create_retry_handler(retry_key):

    def on_press(key):
        global last_transcription_metrics, target_window
        if key != _active_hotkey("retry", retry_key):
            return

        with state_lock:
            if state != State.IDLE:
                return

        h = get_transcript_history()
        if h is None:
            beep_error()
            return

        pending = h.get_pending_audio()
        if not pending:
            beep_error()
            show_status("❌ NO PENDING", "Nothing to re-transmit")
            return

        target_window = get_active_window()

        set_terminal_title()
        show_status(
            STATUS_EDGE_INFERENCE_REXMIT,
            _status_detail_for_edge_inference(rexmit=True),
        )

        try:
            wav_buffer = io.BytesIO(pending)
            text = transcribe_server(wav_buffer, last_audio_capture_info)
            if last_transcription_metrics is None:
                last_transcription_metrics = {}
            if text and config and getattr(config, "polish", True):
                text = maybe_polish_transcript(text)
            metrics = last_transcription_metrics
            if metrics is not None:
                _record_persistent_stats(metrics, source="retry")

            if text and not is_hallucination(text):
                try:
                    raw = io.BytesIO(pending)
                    r_sr, r_data = wavfile.read(raw)
                    if r_data.ndim > 1:
                        r_data = r_data.mean(axis=1)
                    if np.issubdtype(r_data.dtype, np.integer):
                        arr = (r_data.astype(np.float32) / 32768.0).ravel()
                    else:
                        arr = r_data.astype(np.float32).ravel()
                    set_spectrum_from_mono_float(arr, int(r_sr))
                except Exception:
                    pass
                h2 = get_transcript_history()
                if h2 is not None:
                    h2.add(text)
                paste_text(" " + text)
                h3 = get_transcript_history()
                if h3 is not None:
                    h3.clear_pending_audio()
                beep_success()
                set_terminal_title()
                copy_rex = take_readback_rexmit()
                if config and getattr(config, "ux_chatter", False):
                    from voxium.ux_chatter import (
                        is_ux_chatter_wanted,
                        sync_ux_chatter_for_transcript,
                    )

                    _fc = getattr(config, "file_config", None) or {}
                    if is_ux_chatter_wanted(
                        cli_enabled=bool(getattr(config, "ux_chatter", False)),
                        file_config=_fc,
                    ):
                        uxf = sync_ux_chatter_for_transcript(
                            text,
                            _fc,
                            bool(getattr(config, "ux_chatter", False)),
                            on_complete=_ux_chatter_on_complete,
                        )
                        w = _ux_resolve_copy_wit(text, uxf)
                        copy_rex = f"{w} (re-transmit)"
                show_status(STATUS_VOX_COPY_REXMIT, copy_rex)
                log_transcription_summary(text, metrics)
            else:
                beep_error()
                show_status("❌ NO SPEECH", "Nothing detected")
                h4 = get_transcript_history()
                if h4 is not None:
                    h4.clear_pending_audio()
        except Exception as e:
            beep_error()
            set_terminal_title()
            show_status("❌ RE-TRANSMIT FAILED", str(e)[:200])

    return on_press


def create_mode_toggle_handler(mode_key, mode_hotkey_label: str):

    def on_press(key: object) -> None:
        global _last_hotkey_time
        if key != _active_hotkey("mode", mode_key):
            return
        now = time.time() * 1000
        if now - _last_hotkey_time < HOTKEY_DEBOUNCE_PTT_START_MS:
            return
        _last_hotkey_time = now
        with state_lock:
            st = state
        if st in (State.RECORDING, State.TRANSCRIBING, State.COMMAND_INPUT):
            return
        ptt_l = (config.hotkey if config else "f9").upper()
        if _get_input_mode() == "ptt":
            _set_input_mode("vox")
            if not _start_vox_listening():
                _set_input_mode("ptt")
                if config and not config.minimal:
                    print_agent_telemetry_panel(
                        console,
                        [
                            (
                                "VOX capture could not start (e.g. PTT still on); staying on PTT.",
                                "warning",
                            )
                        ],
                        downlink_subtitle="input mode",
                    )
                else:
                    print(
                        "Voxium: VOX not started — staying on PTT.",
                        file=sys.stderr,
                    )
                return
            if config and not config.minimal:
                print_input_mode_downlink(
                    console,
                    mode="vox",
                    mode_hotkey_label=mode_hotkey_label,
                    ptt_hotkey_label=ptt_l,
                )
                if ptt_status_box is not None:
                    ptt_status_box.restore_live_after_scrollback_output()
            else:
                print(
                    "Voxium: input mode VOX (open mic, utterance gating).",
                    file=sys.stderr,
                )
        else:
            _stop_vox_listening()
            _set_input_mode("ptt")
            # Rebuild the green strip + slash footer *before* printing the violet downlink. If
            # `console.print` runs first, the nested transient footer Live can be left off-screen
            # until the next full status refresh (e.g. PTT on F9). VOX→PTT order matches VOX entry:
            # `set_status` in `_start_vox_listening` runs before `print_input_mode_downlink`.
            show_status(
                STATUS_VOX_ON_STATION,
                f"PTT: {(config.hotkey if config else 'f9').upper()} to transmit, copy.",
            )
            if config and not config.minimal:
                print_input_mode_downlink(
                    console,
                    mode="ptt",
                    mode_hotkey_label=mode_hotkey_label,
                    ptt_hotkey_label=ptt_l,
                )
                if ptt_status_box is not None:
                    ptt_status_box.restore_live_after_scrollback_output()
            else:
                print("Voxium: PTT (push-to-talk) — VOX on demand.", file=sys.stderr)

    return on_press


def _peer_pid_exists_on_this_os(pid: int) -> bool:
    """Best-effort: is ``pid`` a live process on *this* OS (WSL vs Windows share one lock file)."""
    if SYSTEM == "Windows":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def _print_instance_lock_denied(lock_file: Path, pid_hint: str | None) -> None:
    """Operator-facing copy when the repo-local single-instance guard trips."""
    pid_hint = (pid_hint or "").strip() or None
    if pid_hint:
        print(
            f"Another PTT session is already on the air (PID {pid_hint})",
            file=sys.stderr,
        )
    else:
        print(
            "Voxium is already running — one operator at a time (single instance).",
            file=sys.stderr,
        )
    print(
        "Close the other Voxium window, or stop its Python process (including leftover "
        "tests, profilers, or automated runs).",
        file=sys.stderr,
    )
    if pid_hint is not None and pid_hint.isdigit():
        pid_i = int(pid_hint)
        if SYSTEM == "Windows":
            print(f"  Try:  Stop-Process -Id {pid_i} -Force", file=sys.stderr)
            if not _peer_pid_exists_on_this_os(pid_i):
                print(
                    "  That PID is not a Windows process right now — logs/voxium.lock "
                    "may have been written from WSL while this clone lives on a shared "
                    "drive. Use Task Manager to end the Python process that is actually "
                    "running Voxium on Windows.",
                    file=sys.stderr,
                )
                print(
                    "  The single-instance guard is a Windows mutex (VoxiumSingleInstance); "
                    "deleting voxium.lock alone does not release it if another python.exe "
                    "still holds that mutex.",
                    file=sys.stderr,
                )
        else:
            print(f"  Try:  kill {pid_i}", file=sys.stderr)
            if not _peer_pid_exists_on_this_os(pid_i):
                print(
                    "  That PID is not running here — the lock file line may be stale, "
                    "or another machine shares this working copy.",
                    file=sys.stderr,
                )
    elif SYSTEM == "Windows":
        print(
            "  No PID is recorded in voxium.lock (missing, empty, or unreadable); "
            "another Windows process still holds the VoxiumSingleInstance mutex. "
            "List candidates with:\n"
            "    Get-Process python* | Select-Object Id, Path\n"
            "  End the one using this clone's .venv\\Scripts\\python.exe, or use Task Manager.",
            file=sys.stderr,
        )
    print(f"  Repo lock file: {lock_file}", file=sys.stderr)


class _WindowsLock:
    def __init__(self, handle):
        self._handle = handle

    def close(self):
        import ctypes

        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


def acquire_instance_lock():

    lock_file = instance_lock_path()

    if SYSTEM == "Windows":
        import ctypes

        handle = ctypes.windll.kernel32.CreateMutexW(None, True, "VoxiumSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            pid: str | None = None
            try:
                with open(lock_file, "r") as f:
                    pid = f.read().strip()
            except Exception:
                pid = None
            _print_instance_lock_denied(lock_file, pid)
            ctypes.windll.kernel32.CloseHandle(handle)
            pause_console_before_exit()
            sys.exit(1)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        return _WindowsLock(handle)
    else:
        import fcntl

        lock_fd = open(lock_file, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
            return lock_fd
        except BlockingIOError:
            try:
                lock_fd.close()
            except Exception:
                pass
            pid_other: str | None = None
            try:
                with open(lock_file, "r") as f:
                    pid_other = f.read().strip()
            except Exception:
                pid_other = None
            _print_instance_lock_denied(lock_file, pid_other)
            pause_console_before_exit()
            sys.exit(1)


def run_server_command(args) -> int:

    if not is_loopback_host(args.host):
        print("Voxium: managed server may only key to a loopback address (ground net).")
        print("Use localhost, 127.0.0.1, or ::1.")
        return 2

    from voxium import whisper_server

    host = normalize_loopback_host(args.host)
    server_argv = [
        "--model",
        args.model,
        "--device",
        args.device,
        "--compute",
        args.compute,
        "--host",
        host,
        "--port",
        str(args.port),
        "--timeout",
        str(args.timeout),
        "--metrics-sample-interval",
        str(args.metrics_sample_interval),
        "--log-level",
        resolve_log_level(args),
    ]
    if not args.vad:
        server_argv.append("--no-vad")
    if not args.gpu_metrics:
        server_argv.append("--no-gpu-metrics")
    server_argv.extend(
        [
            "--llama-cpp-url",
            str(args.llama_cpp_url),
            "--polish-default-model",
            str(args.polish_default_model),
            "--polish-timeout",
            str(args.polish_timeout),
        ]
    )
    if args.polish_enabled_by_default:
        server_argv.append("--polish-enabled-by-default")
    else:
        server_argv.append("--no-polish-enabled-by-default")
    server_argv.extend(
        [
            "--polish-keep-alive",
            str(args.polish_keep_alive),
        ]
    )
    if args.polish_warmup_on_start:
        server_argv.append("--polish-warmup-on-start")
    else:
        server_argv.append("--no-polish-warmup-on-start")
    server_argv.extend(["--polish-max-concurrent", str(args.polish_max_concurrent)])

    whisper_server.main(server_argv)
    return 0


def print_server_response(title: str, data: dict, raw_json: bool):

    body = json.dumps(data, indent=2, sort_keys=True)
    if raw_json:
        print(body)
        return
    console.print(
        Panel(
            body,
            title=title,
            title_align="left",
            border_style="cyan",
            width=voxium_panel_width(console),
        )
    )


def run_server_query(args, endpoint: str) -> int:

    if not is_loopback_url(args.server_url):
        print(
            "Voxium: only HTTP loopback for server queries (keep the link on the ground)."
        )
        print(f"Use a URL such as: {DEFAULT_SERVER_URL}")
        return 2

    url = get_server_endpoint_url(args.server_url, endpoint)
    try:
        resp = requests.get(url, timeout=args.timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        if not getattr(args, "quiet", False):
            print(f"Could not read {url}: {exc}")
        return 1

    print_server_response(f"Downlink /{endpoint}", data, args.json)
    return 0


def _transcribe_models_payload(*, installed_only: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name in sorted(TRUSTED_MODELS):
        metadata = TRUSTED_MODELS[name]
        installed = is_trusted_model_on_disk(name)
        if installed_only and not installed:
            continue
        rows.append(
            {
                "id": name,
                "repo": metadata["repo"],
                "vram": metadata["vram"],
                "description": metadata["description"],
                "installed": installed,
                "default": name == DEFAULT_MODEL_NAME,
            }
        )
    return {
        "lane": "transcribe",
        "default": DEFAULT_MODEL_NAME,
        "trusted_namespace": "Systran",
        "installed_count": sum(1 for row in rows if row["installed"]),
        "models": rows,
    }


def _polish_models_payload(
    *,
    installed_only: bool = False,
    provisioned: dict[str, Any] | None = None,
) -> dict[str, Any]:
    installed_by_name = {model.name: model for model in list_local_polish_models()}
    trusted_rows: list[dict[str, Any]] = []
    for model in list_available_polish_models():
        local = installed_by_name.get(model.model_id)
        if installed_only and local is None:
            continue
        trusted_rows.append(
            {
                "id": model.model_id,
                "repo_id": model.repo_id,
                "filename": model.filename,
                "description": model.description,
                "size": model.size_text,
                "backend": model.backend,
                "installed": local is not None,
                "default": model.model_id == DEFAULT_TRUSTED_POLISH_MODEL_ID,
                "path": str(local.path) if local is not None else None,
                "size_bytes": local.size_bytes if local is not None else None,
            }
        )
    custom_rows = [
        {
            "id": model.name,
            "path": str(model.path),
            "size": model.size_gib_text,
            "size_bytes": model.size_bytes,
            "description": model.description or "Local custom GGUF",
        }
        for model in list_local_polish_models()
        if not model.is_trusted
    ]
    payload = {
        "lane": "polish",
        "backend": "llama.cpp",
        "default": POLISH_DEFAULT_MODEL,
        "registry_default": DEFAULT_TRUSTED_POLISH_MODEL_ID,
        "models_dir": str(polish_models_dir().resolve()),
        "runtime_dir": str(llama_cpp_dir().resolve()),
        "runtime_cmd": llama_server_cli_path(),
        "trusted_models": trusted_rows,
        "custom_local_models": custom_rows,
    }
    if provisioned is not None:
        payload["provisioned"] = provisioned
    return payload


def _print_transcribe_models_table(
    payload: dict[str, Any], *, installed_only: bool
) -> None:
    title = (
        "Transcribe models installed under models/ (Systran faster-whisper)"
        if installed_only
        else "Transcribe models (Systran faster-whisper allow-list)"
    )
    table = Table(title=title)
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Installed", justify="center")
    table.add_column("VRAM", justify="right")
    table.add_column("Repository", style="green")
    table.add_column("Notes")
    rows = payload["models"]
    if not rows:
        table.add_row("—", "—", "—", "—", "No transcribe models matched this view")
    for row in rows:
        label = f"{row['id']} (default)" if row["default"] else row["id"]
        table.add_row(
            label,
            "yes" if row["installed"] else "no",
            row["vram"],
            row["repo"],
            row["description"],
        )
    console.print(table)


def _print_polish_models_table(
    payload: dict[str, Any], *, installed_only: bool
) -> None:
    title = (
        "Polish + UX chatter models installed under models/polish (trusted IDs + local GGUF)"
        if installed_only
        else "Polish + UX chatter models (trusted IDs for llama.cpp plus installed local GGUF)"
    )
    table = Table(title=title)
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Installed", justify="center")
    table.add_column("Approx size", justify="right")
    table.add_column("Source", style="green")
    table.add_column("Notes")
    rows = payload["trusted_models"]
    if not rows:
        table.add_row(
            "—", "—", "—", "—", "No trusted polish/chatter models matched this view"
        )
    for row in rows:
        label = f"{row['id']} (registry default)" if row["default"] else row["id"]
        table.add_row(
            label,
            "yes" if row["installed"] else "no",
            row["size"],
            row["repo_id"],
            row["description"],
        )
    console.print(table)

    custom_rows = payload.get("custom_local_models") or []
    if custom_rows:
        local_table = Table(title="Installed custom local GGUF polish/chatter models")
        local_table.add_column("Selector", style="cyan", no_wrap=True)
        local_table.add_column("Approx size", justify="right")
        local_table.add_column("Path")
        for row in custom_rows:
            local_table.add_row(row["id"], row["size"], row["path"])
        console.print(local_table)

    runtime_note = payload.get("runtime_cmd") or "(not found)"
    console.print(
        f"llama-server runtime: {runtime_note}\n"
        f"registry default: {payload['registry_default']}\n"
        f"models dir: {payload['models_dir']}\n"
        f"runtime dir: {payload['runtime_dir']}\n"
        "Select the shared polish/chatter model in-session with `/models polish use <id>` "
        "or at launch with `voxium run --polish-model <id>`."
    )


def run_models_command(args) -> int:
    provisioned_payload: dict[str, Any] | None = None
    lane = (getattr(args, "lane", None) or "").strip().lower()
    action = (getattr(args, "action", None) or "").strip().lower()
    model_id = (getattr(args, "model_id", None) or "").strip()

    if lane and lane not in {"transcribe", "polish"}:
        print(
            "Use `voxium models`, `voxium models transcribe ...`, or `voxium models polish ...`."
        )
        return 2

    if lane == "polish" and action == "pull":
        pull_code, provisioned_payload = _pull_polish_models_to_repo_cache(
            json_mode=bool(args.json),
            model_id=model_id or POLISH_DEFAULT_MODEL,
        )
        if args.json and pull_code != 0 and provisioned_payload is not None:
            print(json.dumps(provisioned_payload, indent=2))
        if pull_code != 0:
            return pull_code
        action = "list"

    if not lane:
        payload = {
            "transcribe": _transcribe_models_payload(installed_only=False),
            "polish": _polish_models_payload(
                installed_only=False,
                provisioned=provisioned_payload,
            ),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        _print_transcribe_models_table(payload["transcribe"], installed_only=False)
        _print_polish_models_table(payload["polish"], installed_only=False)
        console.print(
            "Use `voxium models transcribe installed` or `voxium models polish installed` "
            "for downloaded-only views."
        )
        return 0

    if lane == "transcribe":
        if action not in {"", "list", "installed"}:
            print(
                "Use `voxium models transcribe list` or `voxium models transcribe installed`.\n"
                "Select for a run with `voxium run --model <id>` or in-session with `/models transcribe use <id>`."
            )
            return 2
        installed_only = action == "installed"
        payload = _transcribe_models_payload(installed_only=installed_only)
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        _print_transcribe_models_table(payload, installed_only=installed_only)
        console.print(
            "Select for a run with `voxium run --model <id>` or in-session with `/models transcribe use <id>`."
        )
        return 0

    if action not in {"", "list", "installed"}:
        print(
            "Use `voxium models polish list`, `voxium models polish installed`, or `voxium models polish pull <id>`.\n"
            "Select in-session with `/models polish use <id>` or at launch with `voxium run --polish-model <id>`."
        )
        return 2
    installed_only = action == "installed"
    payload = _polish_models_payload(
        installed_only=installed_only,
        provisioned=provisioned_payload,
    )
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    _print_polish_models_table(payload, installed_only=installed_only)
    return 0


def _pull_polish_models_to_repo_cache(
    *, json_mode: bool = False, model_id: str = POLISH_DEFAULT_MODEL
) -> tuple[int, dict[str, Any] | None]:
    ensure_runtime_dirs()
    progress = None if json_mode else print
    try:
        assets = ensure_default_polish_assets(
            model_name=model_id,
            progress=progress,
        )
    except RuntimeError as exc:
        payload = {
            "ok": False,
            "backend": "llama.cpp",
            "runtime_dir": str(llama_cpp_dir().resolve()),
            "models_dir": str(polish_models_dir().resolve()),
            "requested_model": model_id,
            "error": str(exc),
        }
        if not json_mode:
            print(f"Voxium could not provision the local re-encode stack: {exc}")
        return 1, payload

    payload = {
        "ok": True,
        "backend": "llama.cpp",
        "runtime_dir": (
            str(assets.runtime_dir.resolve())
            if assets.runtime_dir is not None
            else str(llama_cpp_dir().resolve())
        ),
        "runtime_exe": (
            str(assets.runtime_exe.resolve())
            if assets.runtime_exe is not None
            else None
        ),
        "requested_model": model_id,
        "runtime_variant": assets.runtime_variant,
        "runtime_tag": assets.runtime_tag,
        "model_path": str(assets.model_path.resolve()),
        "model_repo_id": assets.model_repo_id,
        "model_filename": assets.model_filename,
    }
    if not json_mode:
        print("Re-encode provisioning ready.")
        if assets.runtime_exe is not None:
            print(f"  runtime: {assets.runtime_exe}")
        else:
            print(f"  runtime dir: {llama_cpp_dir().resolve()}")
        print(f"  model:   {assets.model_path}")
    return 0, payload


def run_client(args, _raw_argv: list[str]) -> int:

    global config, history, ptt_status_box, _llama_cpp_polish_ready_checked, _llama_cpp_ux_ready_checked
    client_shutdown_event.clear()

    ensure_runtime_dirs()
    from voxium.ux_chatter import clear_ux_chatter_wit

    clear_ux_chatter_wit()
    args.file_config = _merge_run_file_config(args)
    config = args
    _llama_cpp_polish_ready_checked = False
    _llama_cpp_ux_ready_checked = False
    _telemetry_log_buffer.clear()
    ptt_status_box = (
        PttSessionStatusBox(console, standby_context=_standby_telemetry_context)
        if not config.minimal
        else None
    )
    hotkeys_adjusted = apply_runtime_hotkey_safety(config) or getattr(
        config,
        "_config_hotkeys_adjusted",
        False,
    )

    lock_fd = acquire_instance_lock()
    atexit.register(lambda: lock_fd.close())
    atexit.register(lambda: server_log_handle.close() if server_log_handle else None)

    _pending_b = int(config.history_pending_mib) * 1024 * 1024
    history = SessionTranscriptHistory(
        max_entries=config.history_limit,
        max_total_chars=config.history_max_chars,
        max_pending_bytes=_pending_b,
    )

    if not config.quiet and not config.minimal:
        from voxium.startup_banner import get_startup_hostname, show_startup_banner
        from voxium.ux_chatter import (
            fetch_ux_rig_subtitle,
            fetch_ux_startup_tagline,
            is_ux_chatter_wanted,
        )

        _ux_fc = getattr(config, "file_config", None) or {}
        _ux_tag: str | None = None
        _ux_rig: str | None = None
        _host = get_startup_hostname()
        if is_ux_chatter_wanted(
            cli_enabled=bool(getattr(config, "ux_chatter", False)),
            file_config=_ux_fc,
        ):
            ensure_llama_cpp_for_ux_chatter()
            _ux_tag = fetch_ux_startup_tagline(_ux_fc, cli_enabled=True)
            _ux_rig = fetch_ux_rig_subtitle(_ux_fc, _host, cli_enabled=True)
        show_startup_banner(console, tagline=_ux_tag, rig_subtitle=_ux_rig)
    cli_log(f"System: {SYSTEM}")
    if hotkeys_adjusted:
        cli_log(
            "Adjusted unsupported or duplicate hotkeys; active hotkeys are "
            f"record={config.hotkey.upper()} (PTT), "
            f"mode={config.mode_hotkey.upper()} (PTT ↔ VOX), "
            f"recovery={config.recovery_hotkey.upper()} (cycle replay transcripts), "
            f"retry={config.retry_hotkey.upper()} (re-transmit).",
            "warning",
        )

    check_dependencies()
    cli_log(f"Audio input: {describe_audio_capture_source()}")
    ensure_llama_cpp_for_polish()
    ensure_local_server()
    # Prewarm the session STT model on the local stack (default ``small.en`` unless
    # ``--model`` / ``transcription.model`` says otherwise). Reusing a server that was
    # started with a different argv (e.g. ``tiny``) does not reload the process; /ensure-model
    # loads and first-inference warms the session model so the first PTT is ready.
    if is_loopback_url(config.server_url) and getattr(config, "model", None):
        ensure_model_on_loopback_server(
            config.server_url,
            console,
            validate_model_name(str(config.model)),
            quiet_success=True,
        )
    if getattr(config, "ux_chatter", False) and not config.minimal:
        cli_log(
            "UX chatter: on — dynamic lines and violet Downlink share the selected polish GGUF "
            f"on {getattr(config, 'llama_cpp_url', 'http://127.0.0.1:11435')}. "
            "Use `/models polish list` and `/models polish use <id>` to inspect or change that shared model, copy.",
            "info",
        )
    flush_client_telemetry_block(include_ops_cheat=True)

    hotkey = get_hotkey(config.hotkey)
    recovery_key = get_hotkey(config.recovery_hotkey)
    retry_key = get_hotkey(config.retry_hotkey)
    mode_key = get_hotkey(config.mode_hotkey)
    set_terminal_title()

    if ptt_status_box is not None and not config.minimal:
        ptt_status_box.set_ptt_hotkey_hint(config.hotkey.upper())
        ptt_status_box.set_mode_hotkey_hint(config.mode_hotkey.upper())
        ptt_status_box.set_input_mode_for_footer("ptt")

    if config.minimal:
        show_status(
            STATUS_VOX_ON_STATION,
            (
                f"PTT: {config.hotkey.upper()} · "
                f"{config.mode_hotkey.upper()} PTT↔VOX · {config.recovery_hotkey.upper()} replay · "
                f"{config.retry_hotkey.upper()} re-xmit (VOX in)"
            ),
        )
    elif not config.quiet:
        show_status(STATUS_VOX_ON_STATION, "Standing by.")
    if not config.quiet:
        cli_log(
            f"📻 Input mode: PTT (default) — {config.mode_hotkey.upper()} → VOX (open mic, utterance gating).",
            "info",
        )

    record_press_handler, record_release_handler = create_record_hotkey_handlers(hotkey)
    recovery_handler = create_recovery_handler(recovery_key)
    retry_handler = create_retry_handler(retry_key)
    mode_handler = create_mode_toggle_handler(mode_key, config.mode_hotkey.upper())

    def _slash_refresh_footer(command_active: bool) -> None:
        if ptt_status_box is None:
            return
        ptt_status_box.set_command_line(
            _slash_buffer,
            command_active,
            hints=format_slash_command_hints(_slash_buffer) if command_active else "",
        )

    def pynput_typed_char(key: object) -> str | None:
        if key == keyboard.Key.space:
            return " "
        if hasattr(key, "char") and key.char and len(key.char) == 1:
            c = key.char
            if c.isprintable():
                return c
        return None

    def try_handle_slash_input(key: object) -> bool:
        """True when the key was consumed (slash line / cancel / submit)."""
        global state, _slash_buffer, _last_hotkey_time, _slash_tab_cycle
        if config.minimal or ptt_status_box is None or config.quiet:
            return False

        slash_global = bool(
            getattr(config, "slash_global", False)
            or os.environ.get("VOXIUM_SLASH_GLOBAL", "").lower() in ("1", "true", "yes")
        )

        with state_lock:
            st = state

        if st == State.COMMAND_INPUT:
            if key == hotkey:
                now = time.time() * 1000
                if now - _last_hotkey_time < HOTKEY_DEBOUNCE_MS:
                    return True
                _last_hotkey_time = now
                with state_lock:
                    state = State.IDLE
                _slash_buffer = ""
                _slash_tab_cycle = 0
                ptt_status_box.set_command_line("", False)
                set_terminal_title()
                return True
            if key == keyboard.Key.tab:
                out = apply_slash_tab(_slash_buffer, tab_cycle=_slash_tab_cycle)
                _slash_buffer = out.new_buffer
                _slash_tab_cycle = out.tab_cycle
                _slash_refresh_footer(True)
                return True
            if key == keyboard.Key.enter:
                line = _slash_buffer
                _slash_buffer = ""
                _slash_tab_cycle = 0
                with state_lock:
                    state = State.IDLE
                ptt_status_box.set_command_line("", False)
                needs = slash_data_needs(line)
                sk: dict = {}
                if needs.server_gpu:
                    sk["gpu"] = get_server_gpu_metrics()
                if needs.server_health:
                    if bool(getattr(config, "polish", True)):
                        ensure_llama_cpp_for_polish(force=True)
                    sk["server_health"] = get_server_health()
                if needs.server_stats:
                    sk["server_stats"] = get_server_stats()
                    sk["persistent_stats"] = _load_persistent_stats_for_slash()
                if needs.mic_capture:
                    sk["mic_info"] = build_audio_capture_info()
                out = run_slash_line(
                    line,
                    session_model=getattr(config, "model", None),
                    polish_enabled=bool(getattr(config, "polish", True)),
                    polish_model=getattr(config, "polish_model", None),
                    current_hotkeys=_current_hotkeys_for_slash(),
                    transcript_history=get_transcript_history(),
                    file_config=getattr(config, "file_config", None) or {},
                    **sk,
                )
                combined_polish = bool(
                    not config.minimal
                    and (out.polish_enabled is not None or out.polish_model is not None)
                    and (
                        out.polish_enabled is True
                        or (
                            out.polish_model is not None
                            and bool(getattr(config, "polish", True))
                        )
                    )
                )
                if not combined_polish:
                    print_slash_command_downlink(
                        console, line, out.text, result_rich=out.result_rich
                    )

                def _freeze_for_ensure() -> None:
                    if ptt_status_box is not None and config and not config.minimal:
                        ptt_status_box.freeze_before_external_output()

                apply_slash_runtime_changes(
                    out,
                    freeze_for_external_output=_freeze_for_ensure,
                    polish_slash=(line, out) if combined_polish else None,
                )
                if ptt_status_box is not None and config and not config.minimal:
                    ptt_status_box.restore_live_after_scrollback_output()
                set_terminal_title()
                return True
            if key == keyboard.Key.backspace:
                _slash_tab_cycle = 0
                if _slash_buffer:
                    _slash_buffer = _slash_buffer[:-1]
                with state_lock:
                    if not _slash_buffer:
                        state = State.IDLE
                    command_active = state == State.COMMAND_INPUT
                if command_active:
                    _slash_refresh_footer(True)
                else:
                    ptt_status_box.set_command_line("", False)
                return True
            ch = pynput_typed_char(key)
            if ch and len(_slash_buffer) < _SLASH_LINE_MAX:
                _slash_tab_cycle = 0
                _slash_buffer += ch
                _slash_refresh_footer(True)
            return True

        if st == State.IDLE:
            ch0 = pynput_typed_char(key)
            if ch0 == "/":
                if not slash_global and not is_our_terminal_focused():
                    return False
                with state_lock:
                    if state != State.IDLE:
                        return False
                    state = State.COMMAND_INPUT
                _slash_buffer = "/"
                _slash_tab_cycle = 0
                _slash_refresh_footer(True)
                return True

        return False

    def try_handle_morse_audio_toggle(key: object) -> bool:
        """True when terminal-focused ``M`` toggled CW playback for the last transcript."""
        ch = pynput_typed_char(key)
        if ch not in ("m", "M"):
            return False
        with state_lock:
            st = state
        if st != State.IDLE:
            return False
        if not is_our_terminal_focused():
            return False
        return _toggle_morse_audio_for_last_transcript()

    def combined_handler(key: object) -> None:
        if try_handle_slash_input(key):
            return
        if try_handle_morse_audio_toggle(key):
            return
        mode_handler(key)
        record_press_handler(key)
        recovery_handler(key)
        retry_handler(key)

    def combined_release_handler(key: object) -> None:
        record_release_handler(key)

    # Ctrl+C: never stop pynput or run cleanup *inside* the signal handler. On Windows that can
    # deadlock (handler runs in the main thread while the listener thread holds locks). Only set a
    # flag; the main thread exits the wait loop, the Listener context manager stops, then we cleanup.
    import signal

    shutdown_requested = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigbreak: object = None
    if platform.system() == "Windows" and hasattr(signal, "SIGBREAK"):
        try:
            previous_sigbreak = signal.getsignal(signal.SIGBREAK)  # type: ignore[attr-defined]
        except (OSError, ValueError):
            pass

    def request_interrupt(*_a: object) -> None:
        shutdown_requested.set()
        client_shutdown_event.set()

    try:
        signal.signal(signal.SIGINT, request_interrupt)
    except (OSError, ValueError):
        pass
    sigbreak_hooked = False
    if platform.system() == "Windows" and hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, request_interrupt)  # type: ignore[attr-defined]
            sigbreak_hooked = True
        except (OSError, ValueError):
            pass

    try:
        with keyboard.Listener(
            on_press=combined_handler,
            on_release=combined_release_handler,
        ) as listener:
            while listener.is_alive() and not shutdown_requested.is_set():
                listener.join(0.2)
    except KeyboardInterrupt:
        request_interrupt()
    finally:
        try:
            signal.signal(signal.SIGINT, previous_sigint)
        except (OSError, ValueError):
            pass
        if sigbreak_hooked and hasattr(signal, "SIGBREAK"):
            try:
                to_restore = (
                    previous_sigbreak
                    if previous_sigbreak is not None
                    else signal.SIG_DFL
                )
                signal.signal(signal.SIGBREAK, to_restore)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                pass

    # Freeze the green PTT strip before the violet sign-off. Otherwise :meth:`PttSessionStatusBox.close`
    # would call :meth:`rich.live.Live.stop` after the downlink, which re-prints a final green “Standing
    # by” panel in the scrollback after the user already saw the shutdown line (Ctrl+C / SIGINT path).
    if (
        shutdown_requested.is_set()
        and ptt_status_box is not None
        and config
        and not config.minimal
    ):
        ptt_status_box.freeze_before_external_output()
    # Farewell: :func:`cleanup_client_runtime` tears down the managed UX llama-server, and
    # :func:`fetch_ux_shutdown_line` needs that stack on loopback for a model-written sign-off.
    if shutdown_requested.is_set() and not config.quiet:
        _print_shutdown_farewell()
    cleanup_client_runtime()
    return 0


def main(argv: list[str] | None = None):
    try:
        raw_argv = list(sys.argv[1:] if argv is None else argv)
        args = parse_args(raw_argv)

        if args.command == "server":
            return run_server_command(args)
        if args.command == "health":
            return run_server_query(args, "health")
        if args.command == "stats":
            return run_server_query(args, "stats")
        if args.command == "models":
            return run_models_command(args)
        return run_client(args, raw_argv)
    except KeyboardInterrupt:
        if ptt_status_box is not None and config is not None and not config.minimal:
            ptt_status_box.freeze_before_external_output()
        _print_shutdown_farewell()
        cleanup_client_runtime()
        return 130


if __name__ == "__main__":
    sys.exit(main())
