#!/usr/bin/env python3

import argparse
import atexit
import io
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pyperclip
import requests
import sounddevice as sd
from pynput import keyboard
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from scipy.io import wavfile
import yaml

from voxium.capture_enrich import enrich_capture_with_recording
from voxium.console_status import (
    PttSessionStatusBox,
    build_status_box_panel,
    print_agent_telemetry_panel,
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
    DEFAULT_SERVER_URL,
    DEFAULT_HOTKEYS,
    HOTKEY_ORDER,
    LOG_LEVELS,
    SAMPLE_RATE,
)
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
)
from voxium.metrics_table import build_ptt_log_metrics_layout
from voxium.metrics_text import describe_server
from voxium.model_arg import trusted_model_arg
from voxium.model_registry import (
    DEFAULT_MODEL_NAME,
    TRUSTED_MODEL_HELP,
    TRUSTED_MODELS,
    validate_model_name,
)
from voxium.paths import default_server_log_path, ensure_runtime_dirs, instance_lock_path, repo_root
from voxium.resolve_log import resolve_log_level as resolve_log_level_pure
from voxium.slash_complete import apply_slash_tab, format_slash_command_hints
from voxium.slash_commands import run_slash_line, slash_data_needs
from voxium.session_history import SessionTranscriptHistory
from voxium.speech_guards import has_speech, is_hallucination
from voxium.standby_fft import set_spectrum_from_mono_float

SYSTEM = platform.system()

                             
TERMINALS = {
    "Linux": [
        "gnome-terminal", "xterm", "konsole", "alacritty", "kitty",
        "terminator", "tilix", "xfce4-terminal", "urxvt", "st",
        "sakura", "guake", "tilda", "hyper", "wezterm"
    ],
    "Windows": [
        "WindowsTerminal", "cmd.exe", "powershell", "pwsh",
        "ConEmu", "mintty", "Hyper", "Terminus"
    ],
    "Darwin": [
        "Terminal", "iTerm", "iTerm2", "Hyper", "kitty",
        "alacritty", "wezterm"
    ]
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
ptt_status_box: PttSessionStatusBox | None = None
_telemetry_log_buffer: list[tuple[str, str]] = []
managed_server_process: subprocess.Popen | None = None
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

# Green status strip — PTT/VOX & local stack (brand: docs/brand.md)
STATUS_VOX_ON_STATION = "◉ PTT/VOX · ON STATION"
STATUS_VOX_COPY = "🖥️ PTT/VOX · COPY"
STATUS_VOX_COPY_REXMIT = "🖥️ PTT/VOX · COPY (RE-XMIT)"
STATUS_PTT_ACTIVE = "📻 PTT ACTIVE"
STATUS_EDGE_INFERENCE = "🤖 EDGE INFERENCE"
STATUS_EDGE_INFERENCE_REXMIT = "🤖 EDGE INFERENCE (RE-XMIT)"
STATUS_VOX_LAST_COPY = "↩️ PTT/VOX · LAST COPY"
# One short window title: radio = PTT, robot = local inference (tab bar read; override: env VOXIUM_WINDOW_TITLE).
VOXIUM_WINDOW_TITLE = "Voxium 📻🤖"

DEBUG_PASTE = False

CONFIG_PATH = Path.home() / ".config" / "voxium" / "config.yaml"


def load_config_file() -> dict:

    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        if not raw:
            return {}
        from voxium.config import VoxiumUserConfig

        return VoxiumUserConfig.model_validate(raw).model_dump()
    except Exception:
        return {}

                          
def add_output_options(
    parser: argparse.ArgumentParser,
    *,
    include_verbose: bool = True,
    include_log_level: bool = True,
):

    if include_verbose:
        parser.add_argument(
            "-v", "--verbose",
            action="count",
            default=0,
            help="Increase console detail. Repeat for more detail."
        )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress nonessential console output."
    )
    if include_log_level:
        parser.add_argument(
            "--log-level",
            choices=LOG_LEVELS,
            default=None,
            help="Server log level. Overrides --verbose/--quiet for server logs."
        )

def apply_runtime_hotkey_safety(args) -> bool:

    requested = {
        "record": args.hotkey,
        "recovery": args.recovery_hotkey,
        "retry": args.retry_hotkey,
    }
    clean = sanitize_hotkey_config(requested)
    args.hotkey = clean["record"]
    args.recovery_hotkey = clean["recovery"]
    args.retry_hotkey = clean["retry"]
    return {name: normalize_hotkey_name(value) for name, value in requested.items()} != clean

def add_run_options(parser: argparse.ArgumentParser, file_config: dict):

    configured_hotkeys = file_config.get("hotkeys", {})
    hotkeys = sanitize_hotkey_config(configured_hotkeys)
    parser.set_defaults(_config_hotkeys_adjusted=hotkey_config_changed(configured_hotkeys, hotkeys))
    trans = file_config.get("transcription", {})
    ui = file_config.get("ui", {})
    hist = file_config.get("history", {})
    server = file_config.get("server", {})

    server_url_default = trans.get("server_url") or DEFAULT_SERVER_URL
    if not is_loopback_url(server_url_default):
        server_url_default = DEFAULT_SERVER_URL

    server_group = parser.add_argument_group(
        "Voxium server",
        "Local transcription server (faster-whisper) that Voxium starts or reuses."
    )
    server_group.add_argument(
        "--server-url",
        default=server_url_default,
        help="HTTP loopback transcription URL"
    )
    server_group.add_argument(
        "--server-start-timeout",
        type=int,
        default=trans.get("server_start_timeout", DEFAULT_SERVER_START_TIMEOUT),
        help="Seconds to wait for server startup/model load"
    )
    server_group.add_argument(
        "--server-log-file",
        default=server.get("log_file", str(default_server_log_path())),
        help="Managed server log path"
    )
    server_group.add_argument(
        "--server-device",
        choices=("auto", "cuda", "cpu"),
        default=server.get("device") or DEFAULT_SERVER_DEVICE,
        help="Device for the local server (default: cuda — local GPU; use cpu if CUDA is unavailable)"
    )
    server_group.add_argument(
        "--server-compute",
        choices=("auto", "float16", "int8"),
        default=server.get("compute") or DEFAULT_SERVER_COMPUTE,
        help="Compute type for the local server (default: float16 for GPU)"
    )
    server_group.add_argument(
        "--server-timeout",
        type=int,
        default=server.get("timeout", DEFAULT_SERVER_TIMEOUT),
        help="Per-request server timeout in seconds"
    )
    server_group.add_argument(
        "--server-vad",
        action=argparse.BooleanOptionalAction,
        default=server.get("vad_enabled", True),
        help="Enable/disable server VAD filtering when Voxium starts the server"
    )
    server_group.add_argument(
        "--server-gpu-metrics",
        action=argparse.BooleanOptionalAction,
        default=server.get("gpu_metrics_enabled", True),
        help="Enable/disable per-request GPU metrics when Voxium starts the server"
    )
    server_group.add_argument(
        "--metrics-sample-interval",
        type=float,
        default=server.get("metrics_sample_interval", DEFAULT_METRICS_SAMPLE_INTERVAL),
        help="GPU metrics sampling interval in seconds"
    )

    transcription_group = parser.add_argument_group("Transcription")
    transcription_group.add_argument(
        "--model", "-m",
        type=trusted_model_arg,
        default=trans.get("model"),
        metavar="MODEL",
        help=f"Voxium model (Systran faster-whisper only): {TRUSTED_MODEL_HELP}"
    )
    transcription_group.add_argument(
        "--language", "-l",
        default=trans.get("language"),
        help="Language code for transcription"
    )

    hotkey_group = parser.add_argument_group("Hotkeys and UI")
    hotkey_group.add_argument(
        "--hotkey", "-k",
        default=hotkeys.get("record", "f9"),
        help="Record/stop F1-F12 hotkey. Examples: f8, f10, f12"
    )
    hotkey_group.add_argument(
        "--recovery-hotkey",
        default=hotkeys.get("recovery", "f8"),
        help="Hotkey (F1–F12) to cycle replay: re-paste PTT/VOX transcripts (newest first, wraps)",
    )
    hotkey_group.add_argument(
        "--retry-hotkey",
        default=hotkeys.get("retry", "f7"),
        help="Hotkey (F1–F12) to re-transmit: re-run transcription on the last pending capture",
    )
    hotkey_group.add_argument(
        "--minimal", "-M",
        action="store_true",
        default=ui.get("minimal", False),
        help="Minimal UI - only show status"
    )

    history_group = parser.add_argument_group("History")
    history_group.add_argument(
        "--history-limit",
        type=int,
        default=hist.get("limit", 100),
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
        help="Max MiB for the last capture in RAM (re-transmit / F7). Use 0 to disable storage",
    )

def add_server_options(parser: argparse.ArgumentParser):

    parser.add_argument(
        "--model", "-m",
        type=trusted_model_arg,
        default=DEFAULT_SERVER_MODEL,
        metavar="MODEL",
        help=f"Voxium model (Systran faster-whisper only): {TRUSTED_MODEL_HELP}"
    )
    parser.add_argument(
        "--device", "-d",
        choices=("auto", "cuda", "cpu"),
        default=DEFAULT_SERVER_DEVICE,
        help="Device: auto, cuda, cpu"
    )
    parser.add_argument(
        "--compute", "-c",
        choices=("auto", "float16", "int8"),
        default=DEFAULT_SERVER_COMPUTE,
        help="Compute type: auto, float16, int8"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP loopback host to bind to"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8002,
        help="Port to bind to"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=DEFAULT_SERVER_TIMEOUT,
        help="Transcription timeout in seconds"
    )
    parser.add_argument(
        "--vad",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable VAD filtering"
    )
    parser.add_argument(
        "--gpu-metrics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable per-request GPU metrics sampling"
    )
    parser.add_argument(
        "--metrics-sample-interval",
        type=float,
        default=DEFAULT_METRICS_SAMPLE_INTERVAL,
        help="GPU metrics sampling interval in seconds"
    )

def add_server_query_options(parser: argparse.ArgumentParser, file_config: dict):

    trans = file_config.get("transcription", {})
    server_url_default = trans.get("server_url") or DEFAULT_SERVER_URL
    if not is_loopback_url(server_url_default):
        server_url_default = DEFAULT_SERVER_URL
    parser.add_argument(
        "--server-url",
        default=server_url_default,
        help="HTTP loopback transcription URL"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="HTTP request timeout in seconds"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of a formatted panel"
    )

def build_parser(file_config: dict) -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog="voxium",
        usage="voxium [command] [options]",
        description=(
            "Voxium — PTT (push-to-talk) voice in, text out, over local loopback. "
            "Radio: *VOX* at the mic. Stack: an Apollo-style first flight of *your* hardware+software+model path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  voxium                                  Open the PTT & VOX path (default command)
  voxium --model large-v3 -v              Same as: voxium run --model large-v3 -v
  voxium run --server-device cpu         CPU stack (default server device is cuda)
  voxium stats                            Ground readout: server totals
  voxium models                           List trusted models (Systran)
  voxium health --json                    Downlink: server health as JSON
  voxium server --help                    Foreground server (diagnostics; normal use: voxium run)
        """
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
        help="List Systran allow-listed models (trusted stack)",
        formatter_class=SmartDefaultsFormatter,
    )
    models_parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of a formatted table"
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
    return args

                           
def check_dependencies():

    if SYSTEM == "Linux":
        missing = []
        for cmd in ("xdotool", "xclip"):
            try:
                subprocess.run(["which", cmd], capture_output=True, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                missing.append(cmd)
        if missing:
            print(f"Missing Linux dependencies: {', '.join(missing)}")
            print(f"Install with: sudo apt install {' '.join(missing)}")
            sys.exit(1)

                      
    try:
        devices = sd.query_devices()
        if not any(d['max_input_channels'] > 0 for d in devices):
            print("No mic / input device — PTT path blocked. Check default input in OS sound settings.")
            sys.exit(1)
    except Exception as e:
        print(f"Audio path error: {e}")
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
                f"{config.recovery_hotkey.upper()} to replay last transmission; "
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
    cli_log(f"Bringing local /transcribe online: model={model_detail}, url={config.server_url}")
    cli_log(f"Server log: {log_path}")
    cli_log(f"Server command: {subprocess.list2cmdline(cmd)}", "debug")

def ensure_local_server():

    if not is_loopback_url(config.server_url):
        print("Voxium: only local loopback for the transcribe server (no off-world URL).")
        print(f"Use a loopback URL such as: {DEFAULT_SERVER_URL}")
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
            sys.exit(1)
        if time.time() - last_error_at > 10:
            cli_log("Waiting for the stack to load the model (stand by)...")
            last_error_at = time.time()
        time.sleep(0.5)

    print("Timed out waiting for the local server to come on station.")
    print(f"Check {config.server_log_file} for details.")
    sys.exit(1)

                        
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
    """Same tone as PTT start beep: still on the air (see RECORDING_REMINDER_INTERVAL_S)."""
    beep_start()


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
            build_status_box_panel(status, detail, box_width=voxium_panel_width(console))
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
    transcript = Text(text.strip() or "(empty)", style="#f8fafc")
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
    console.print()
    console.print(Panel(
        content,
        title="[bold #38bdf8]Voxium[/bold #38bdf8]",
        title_align="left",
        subtitle="[dim]PTT & VOX log — local loopback only[/dim]",
        subtitle_align="left",
        border_style="#38bdf8",
        padding=(1, 1),
        width=w_box,
    ))

                                         
def get_active_window():

    try:
        if SYSTEM == "Linux":
            return subprocess.check_output(
                ["xdotool", "getactivewindow"],
                stderr=subprocess.DEVNULL
            ).strip()
        elif SYSTEM == "Windows":
            import ctypes
            return ctypes.windll.user32.GetForegroundWindow()
        elif SYSTEM == "Darwin":
            script = 'tell application "System Events" to get name of first process whose frontmost is true'
            result = subprocess.check_output(["osascript", "-e", script], stderr=subprocess.DEVNULL)
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
                stderr=subprocess.DEVNULL
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
            wm_class = subprocess.check_output(
                ["xprop", "-id", window_id, "WM_CLASS"],
                stderr=subprocess.DEVNULL
            ).decode().lower()
            return any(t in wm_class for t in TERMINALS.get("Linux", []))

        elif SYSTEM == "Windows":
            import ctypes
            buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(window_id, buffer, 256)
            title = buffer.value.lower()
            class_buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(window_id, class_buffer, 256)
            class_name = class_buffer.value
            return any(t.lower() in title or t.lower() in class_name.lower()
                      for t in TERMINALS.get("Windows", []))

        elif SYSTEM == "Darwin":
                                            
            app_name = window_id.decode() if isinstance(window_id, bytes) else str(window_id)
            return any(t.lower() in app_name.lower() for t in TERMINALS.get("Darwin", []))
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
    default_rate = round_audio_float(device.get("default_samplerate"), 0) if device else None
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
            "default_samplerate_hz": round_audio_float(device.get("default_samplerate")),
            "default_low_input_latency_seconds": round_audio_float(device.get("default_low_input_latency")),
            "default_high_input_latency_seconds": round_audio_float(device.get("default_high_input_latency")),
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
    with recording_audio_lock:
        audio_chunks = []
    recording_sum_sq = 0.0
    recording_sample_count = 0
    recording_peak_abs = 0.0
    audio_capture_statuses = []
    target_window = get_active_window()
    recording_started_at = time.perf_counter()
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        callback=audio_callback
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
            f"reminder ping every {RECORDING_REMINDER_INTERVAL_S:.0f}s"
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
        take_edge_inference_detail(),
    )

    with recording_audio_lock:
        captured_frames = recording_sample_count
        n_chunks = len(audio_chunks)
        to_concat = list(audio_chunks) if audio_chunks else []
        ssq = recording_sum_sq
        sc = recording_sample_count
        pk = recording_peak_abs

    wall_seconds = stop_time - recording_started_at if recording_started_at is not None else None
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

    global stream, state, recording_monitor_thread, ptt_status_box
    client_shutdown_event.set()
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

def transcribe_server(wav_buffer: io.BytesIO, capture_info: dict | None = None) -> str:

    global last_transcription_metrics
    last_transcription_metrics = None
    wav_buffer.seek(0)

    if not is_loopback_url(config.server_url):
        raise ValueError("Remote transcription URLs are not supported; use http://localhost:8002/transcribe")

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

    text = transcribe_server(wav_buffer, last_audio_capture_info)

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
            print(f"[DEBUG] paste_text BLOCKED by debounce (delta={now - _last_paste_time:.0f}ms)")
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
                                                                              
                                                 
        subprocess.run(["xdotool", "key", "--clearmodifiers", "--delay", "50", key], stderr=subprocess.DEVNULL)
        if DEBUG_PASTE:
            print("[DEBUG] xdotool completed")

    elif SYSTEM == "Windows":
        import pyautogui
        if is_terminal:
                                                              
            pyautogui.hotkey('ctrl', 'v')
        else:
            pyautogui.hotkey('ctrl', 'v')

    elif SYSTEM == "Darwin":
        import pyautogui
        pyautogui.hotkey('command', 'v', interval=0.05)                                                

                                                                                
    if old_clipboard:
        def restore():
                                                            
            delay = min(3.0, max(1.0, 1.0 + len(text) * 0.0001))
            time.sleep(delay)
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass
        threading.Thread(target=restore, daemon=True).start()

                    
def transcribe_and_paste(audio: np.ndarray):

    global state
    try:
        text = transcribe(audio)
        metrics = last_transcription_metrics
        if is_client_shutting_down():
            return
        if text and not is_hallucination(text):
            set_spectrum_from_mono_float(audio, SAMPLE_RATE)
            # Record before paste so /history and replay work even if clipboard or focus/paste fails.
            h = get_transcript_history()
            if h is not None:
                h.add(text)
            paste_text(" " + text)
            beep_success()
            set_terminal_title()
            show_status(STATUS_VOX_COPY, take_readback())
            log_transcription_summary(text, metrics)
        else:
            beep_error()
            set_terminal_title()
            show_status("❌ NO SPEECH", "Nothing detected")
                                                             
            h_clear = get_transcript_history()
            if h_clear is not None:
                h_clear.clear_pending_audio()
    except Exception as e:
        if is_client_shutting_down():
            return
        beep_error()
        set_terminal_title()
        show_status("❌ FAILED", str(e)[:200])
                                                       
    finally:
        with state_lock:
            state = State.IDLE
        if is_client_shutting_down():
            return
                                       
        time.sleep(1.5)
        if is_client_shutting_down():
            return
        set_terminal_title()
        show_status(STATUS_VOX_ON_STATION, "Standing by.")

def get_hotkey(key_name: str):

    key_map = {
        key: getattr(keyboard.Key, key)
        for key in HOTKEY_ORDER
    }
    return key_map[normalize_hotkey_name(key_name)]

def create_hotkey_handler(hotkey):

    def on_press(key):
        global state, _last_hotkey_time
        if key != hotkey:
            return

        now = time.time() * 1000
        with state_lock:
            st = state
        if st not in (State.IDLE, State.RECORDING):
            return
        debounce = (
            HOTKEY_DEBOUNCE_PTT_STOP_MS
            if st == State.RECORDING
            else HOTKEY_DEBOUNCE_PTT_START_MS
        )
        if now - _last_hotkey_time < debounce:
            return
        _last_hotkey_time = now

        with state_lock:
            if state == State.IDLE:
                state = State.RECORDING
                start_recording()
            elif state == State.RECORDING:
                state = State.TRANSCRIBING
                audio = stop_recording()
                threading.Thread(
                    target=transcribe_and_paste,
                    args=(audio,),
                    daemon=True
                ).start()

    return on_press

def create_recovery_handler(recovery_key):

    def on_press(key):
        global target_window
        if key != recovery_key:
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
        global target_window
        if key != retry_key:
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
            take_edge_inference_rexmit_detail(),
        )

        try:
            wav_buffer = io.BytesIO(pending)
            text = transcribe_server(wav_buffer, last_audio_capture_info)
            metrics = last_transcription_metrics

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
                show_status(STATUS_VOX_COPY_REXMIT, take_readback_rexmit())
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
            try:
                with open(lock_file, 'r') as f:
                    pid = f.read().strip()
                print(f"Another PTT session is already on the air (PID {pid})")
            except Exception:
                print("Voxium is already running — one operator at a time (single instance).")
            ctypes.windll.kernel32.CloseHandle(handle)
            sys.exit(1)
        try:
            with open(lock_file, 'w') as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        return _WindowsLock(handle)
    else:
        import fcntl
        lock_fd = open(lock_file, 'w')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_fd.write(str(os.getpid()))
            lock_fd.flush()
            return lock_fd
        except BlockingIOError:
            try:
                with open(lock_file, 'r') as f:
                    pid = f.read().strip()
                print(f"Another PTT session is already on the air (PID {pid})")
            except Exception:
                print("Voxium is already running — one operator at a time (single instance).")
            sys.exit(1)

def run_server_command(args) -> int:

    if not is_loopback_host(args.host):
        print("Voxium: managed server may only key to a loopback address (ground net).")
        print("Use localhost, 127.0.0.1, or ::1.")
        return 2

    from voxium import whisper_server

    host = normalize_loopback_host(args.host)
    server_argv = [
        "--model", args.model,
        "--device", args.device,
        "--compute", args.compute,
        "--host", host,
        "--port", str(args.port),
        "--timeout", str(args.timeout),
        "--metrics-sample-interval", str(args.metrics_sample_interval),
        "--log-level", resolve_log_level(args),
    ]
    if not args.vad:
        server_argv.append("--no-vad")
    if not args.gpu_metrics:
        server_argv.append("--no-gpu-metrics")

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
        print("Voxium: only HTTP loopback for server queries (keep the link on the ground).")
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

def run_models_command(args) -> int:

    payload = {
        "default": DEFAULT_MODEL_NAME,
        "trusted_namespace": "Systran",
        "models": [
            {"name": name, **metadata}
            for name, metadata in TRUSTED_MODELS.items()
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    table = Table(title="Trusted models (Systran stack — VOX path allow-list)")
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Repository", style="green")
    table.add_column("VRAM", justify="right")
    table.add_column("Notes")
    for name, metadata in TRUSTED_MODELS.items():
        label = f"{name} (default)" if name == DEFAULT_MODEL_NAME else name
        table.add_row(label, metadata["repo"], metadata["vram"], metadata["description"])
    console.print(table)
    return 0

def run_client(args, _raw_argv: list[str]) -> int:

    global config, history, ptt_status_box
    client_shutdown_event.clear()

    ensure_runtime_dirs()
    config = args
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
        from voxium.startup_banner import show_startup_banner

        show_startup_banner(console)
    cli_log(f"System: {SYSTEM}")
    if hotkeys_adjusted:
        cli_log(
            "Adjusted unsupported or duplicate hotkeys; active hotkeys are "
            f"record={config.hotkey.upper()} (PTT), "
            f"recovery={config.recovery_hotkey.upper()} (cycle replay PTT/VOX transcripts), "
            f"retry={config.retry_hotkey.upper()} (re-transmit).",
            "warning",
        )

    check_dependencies()
    cli_log(f"Audio input: {describe_audio_capture_source()}")
    ensure_local_server()
    flush_client_telemetry_block(include_ops_cheat=True)

    hotkey = get_hotkey(config.hotkey)
    recovery_key = get_hotkey(config.recovery_hotkey)
    retry_key = get_hotkey(config.retry_hotkey)
    set_terminal_title()

    if ptt_status_box is not None and not config.minimal:
        ptt_status_box.set_ptt_hotkey_hint(config.hotkey.upper())

    if config.minimal:
        show_status(STATUS_VOX_ON_STATION, f"PTT: {config.hotkey.upper()} to transmit (VOX in)")
    elif not config.quiet:
        show_status(STATUS_VOX_ON_STATION, "Standing by.")

    record_handler = create_hotkey_handler(hotkey)
    recovery_handler = create_recovery_handler(recovery_key)
    retry_handler = create_retry_handler(retry_key)

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
                if needs.mic_capture:
                    sk["mic_info"] = build_audio_capture_info()
                out = run_slash_line(
                    line,
                    session_model=getattr(config, "model", None),
                    transcript_history=get_transcript_history(),
                    **sk,
                )
                print_slash_command_downlink(
                    console, line, out.text, result_rich=out.result_rich
                )
                if out.selected_model is not None:
                    config.model = out.selected_model

                    def _freeze_for_ensure() -> None:
                        if ptt_status_box is not None and config and not config.minimal:
                            ptt_status_box.freeze_before_external_output()

                    ensure_model_on_loopback_server(
                        config.server_url,
                        console,
                        out.selected_model,
                        freeze_for_external_output=_freeze_for_ensure,
                    )
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
                with state_lock:
                    if state != State.IDLE:
                        return False
                    state = State.COMMAND_INPUT
                _slash_buffer = "/"
                _slash_tab_cycle = 0
                _slash_refresh_footer(True)
                return True

        return False

    def combined_handler(key: object) -> None:
        if try_handle_slash_input(key):
            return
        record_handler(key)
        recovery_handler(key)
        retry_handler(key)

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
        with keyboard.Listener(on_press=combined_handler) as listener:
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

    cleanup_client_runtime()
    if shutdown_requested.is_set() and not config.quiet:
        print("\nVoxium: going clear. Stopped.")
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
        cleanup_client_runtime()
        print("\nVoxium: going clear. Stopped.")
        return 130

if __name__ == "__main__":
    sys.exit(main())
