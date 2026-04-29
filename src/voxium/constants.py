"""Shared CLI / audio constants (imported by app, tests, and pure helpers)."""

from voxium.model_registry import DEFAULT_MODEL_NAME
from voxium.polish_model_registry import POLISH_DEFAULT_MODEL

import os

APP_VERSION = "0.0.1"
SAMPLE_RATE = 16_000
DEFAULT_SERVER_URL = "http://localhost:8002/transcribe"
DEFAULT_SERVER_START_TIMEOUT = 180
DEFAULT_SERVER_TIMEOUT = 120
DEFAULT_METRICS_SAMPLE_INTERVAL = 0.25
DEFAULT_SERVER_MODEL = os.getenv("WHISPER_MODEL", DEFAULT_MODEL_NAME)
DEFAULT_SERVER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
DEFAULT_SERVER_COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")
DEFAULT_POLISH_MODEL = os.getenv("VOXIUM_POLISH_MODEL", POLISH_DEFAULT_MODEL)
DEFAULT_HOTKEYS = {"record": "f9", "recovery": "f8", "retry": "f6", "mode": "f7"}


def env_polish_enabled_default() -> bool:
    """
    Re-encode (``/polish``) is on unless ``VOXIUM_POLISH_ENABLED`` is explicitly false-ish
    (``0``, ``false``, ``no``, ``off``). When unset, returns True.
    """
    v = (os.environ.get("VOXIUM_POLISH_ENABLED") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return True


HOTKEY_ORDER = tuple(f"f{i}" for i in range(1, 13))
SUPPORTED_HOTKEYS: frozenset[str] = frozenset(HOTKEY_ORDER)
CLI_COMMANDS: frozenset[str] = frozenset(["run", "server", "health", "stats", "models"])
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
