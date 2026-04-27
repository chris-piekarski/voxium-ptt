"""Hotkey selection and normalization (pure)."""

from __future__ import annotations

from voxium.constants import (
    DEFAULT_HOTKEYS,
    HOTKEY_ORDER,
    SUPPORTED_HOTKEYS,
)


def normalize_hotkey_name(value) -> str:
    return str(value or "").lower().strip()


def choose_hotkey(action: str, candidate, used: set[str]) -> str:
    normalized = normalize_hotkey_name(candidate)
    if normalized in SUPPORTED_HOTKEYS and normalized not in used:
        return normalized

    preferred = [
        DEFAULT_HOTKEYS[action],
        DEFAULT_HOTKEYS["record"],
        DEFAULT_HOTKEYS["recovery"],
        DEFAULT_HOTKEYS["retry"],
    ]
    for key_name in preferred + list(HOTKEY_ORDER):
        if key_name not in used:
            return key_name

    return DEFAULT_HOTKEYS[action]


def sanitize_hotkey_config(hotkeys: dict | None) -> dict[str, str]:
    source = hotkeys if isinstance(hotkeys, dict) else {}
    clean: dict[str, str] = {}
    used: set[str] = set()
    for action in ("record", "recovery", "retry"):
        key_name = choose_hotkey(action, source.get(action), used)
        clean[action] = key_name
        used.add(key_name)
    return clean


def hotkey_config_changed(source: dict | None, clean: dict[str, str]) -> bool:
    if not isinstance(source, dict) or not source:
        return False
    for action in ("record", "recovery", "retry"):
        requested = normalize_hotkey_name(source.get(action) or DEFAULT_HOTKEYS[action])
        if requested != clean[action]:
            return True
    return False
