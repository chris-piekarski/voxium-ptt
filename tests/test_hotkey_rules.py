"""Tests for voxium.hotkey_rules."""

from voxium.constants import DEFAULT_HOTKEYS
from voxium.hotkey_rules import (
    choose_hotkey,
    hotkey_config_changed,
    normalize_hotkey_name,
    sanitize_hotkey_config,
)


def test_normalize_hotkey_name():
    assert normalize_hotkey_name("  F8 ") == "f8"
    assert normalize_hotkey_name(None) == ""


def test_choose_hotkey_uses_valid_candidate():
    used: set[str] = set()
    k = choose_hotkey("record", "f1", used)
    assert k == "f1"


def test_choose_hotkey_fills_preferred_first():
    used: set[str] = set()
    k = choose_hotkey("record", "not_a_key", used)
    # Preferred default for "record" is the first in preferred that is unused.
    assert k == DEFAULT_HOTKEYS["record"]


def test_choose_hotkey_last_resort_default_when_all_used():
    used: set[str] = {f"f{i}" for i in range(1, 13)}
    k = choose_hotkey("record", "nope", used)
    # No free key in the scan; return DEFAULT_HOTKEYS[action] (may still be in used)
    assert k == DEFAULT_HOTKEYS["record"]


def test_sanitize_hotkey_config_fills_all_actions():
    clean = sanitize_hotkey_config({})
    assert set(clean.keys()) == {"record", "recovery", "retry"}


def test_sanitize_ignores_invalid_dict():
    clean = sanitize_hotkey_config(None)  # type: ignore[arg-type]
    assert clean["record"] == DEFAULT_HOTKEYS["record"]


def test_hotkey_config_changed_false_for_empty():
    assert hotkey_config_changed(None, sanitize_hotkey_config({})) is False
    assert hotkey_config_changed({}, {"record": "f1", "recovery": "f2", "retry": "f3"}) is False


def test_hotkey_config_changed_true_when_mismatch():
    clean = sanitize_hotkey_config({})
    source = {**clean, "record": "f12"}
    assert hotkey_config_changed(source, clean) is True
