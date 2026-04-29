"""Defaults and overrides for the llama.cpp polish runtime (pure, testable)."""

from __future__ import annotations

from typing import Any


def parse_sleep_idle_seconds(value: Any, *, default: int = -1) -> int:
    """
    Convert user-facing keep-alive text into llama.cpp `--sleep-idle-seconds`.

    Supported values:
    - integers / numeric strings (`600`, `0`, `-1`)
    - duration suffixes: `s`, `m`, `h`
    - empty / `None` -> `default`
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    if not text:
        return default
    if text.lstrip("-").isdigit():
        return int(text)
    if text.endswith("s") and text[:-1].lstrip("-").isdigit():
        return int(text[:-1])
    if text.endswith("m") and text[:-1].lstrip("-").isdigit():
        return int(text[:-1]) * 60
    if text.endswith("h") and text[:-1].lstrip("-").isdigit():
        return int(text[:-1]) * 3600
    raise ValueError(
        f"Unsupported polish keep-alive value {value!r}. "
        "Use seconds, -1, or suffixes like 30s, 10m, 1h, copy."
    )
