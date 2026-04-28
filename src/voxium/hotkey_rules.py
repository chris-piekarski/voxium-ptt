"""Hotkey selection and normalization (pure)."""

from __future__ import annotations

from typing import Any

from voxium.constants import (
    DEFAULT_HOTKEYS,
    HOTKEY_ORDER,
    SUPPORTED_HOTKEYS,
)


def canonical_hotkeys_dict(hotkeys: dict | None) -> dict[str, Any]:
    """
    Map YAML/CLI key spellings to ``record`` / ``recovery`` / ``retry`` / ``mode`` (lowercase).
    Ignores unknown keys. Without this, ``Record: f9`` is missed and we mis-report “adjusted”
    or drop the operator’s real bindings.
    """
    if not isinstance(hotkeys, dict) or not hotkeys:
        return {}
    out: dict[str, Any] = {}
    for k, v in hotkeys.items():
        if not isinstance(k, str):
            k = str(k)
        k2 = k.lower().strip()
        if k2 in ("record", "recovery", "retry", "mode"):
            out[k2] = v
    return out


def normalize_hotkey_name(value) -> str:
    """
    F-key token for F1..F12. Accepts ``f6``, ``F6``, or bare ``6`` / ``6.0`` from YAML/JSON/CLI so we
    do not compare ``\"7\"`` to ``f7`` and spuriously warn that hotkeys were adjusted.
    """
    if value is None:
        return ""
    # bool is a subclass of int; reject so True does not become f1
    if type(value) is bool:
        return str(value).lower()
    if isinstance(value, (int, float)) and 1 <= int(value) <= 12:
        return f"f{int(value)}"
    s = str(value or "").lower().strip()
    if s.isdigit():
        n = int(s)
        if 1 <= n <= 12:
            return f"f{n}"
    return s


def choose_hotkey(action: str, candidate, used: set[str]) -> str:
    normalized = normalize_hotkey_name(candidate)
    if normalized in SUPPORTED_HOTKEYS and normalized not in used:
        return normalized

    preferred = [
        DEFAULT_HOTKEYS[action],
        DEFAULT_HOTKEYS["record"],
        DEFAULT_HOTKEYS["recovery"],
        DEFAULT_HOTKEYS["retry"],
        DEFAULT_HOTKEYS.get("mode", "f7"),
    ]
    for key_name in preferred + list(HOTKEY_ORDER):
        if key_name not in used:
            return key_name

    return DEFAULT_HOTKEYS[action]


def sanitize_hotkey_config(hotkeys: dict | None) -> dict[str, str]:
    source = canonical_hotkeys_dict(hotkeys if isinstance(hotkeys, dict) else None)
    clean: dict[str, str] = {}
    used: set[str] = set()
    for action in ("record", "recovery", "retry", "mode"):
        key_name = choose_hotkey(action, source.get(action), used)
        clean[action] = key_name
        used.add(key_name)
    return clean


def hotkey_config_changed(source: dict | None, clean: dict[str, str]) -> bool:
    """
    True if any **binding the operator set** in the file (canonical keys) normalizes
    to something other than ``clean[action]`` — i.e. the sanitizer had to change or
    re-order keys. We do **not** compare “missing” keys against defaults, so a partial
    ``config.yaml`` does not spuriously look “adjusted” for keys you never set.
    """
    if not isinstance(source, dict) or not source:
        return False
    by = canonical_hotkeys_dict(source)
    for action in ("record", "recovery", "retry", "mode"):
        if action not in by:
            continue
        if normalize_hotkey_name(by[action]) != clean[action]:
            return True
    return False
