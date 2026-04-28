"""Tests for voxium.hotkey_rules."""

from voxium.constants import DEFAULT_HOTKEYS
from voxium.hotkey_rules import (
    canonical_hotkeys_dict,
    choose_hotkey,
    hotkey_config_changed,
    normalize_hotkey_name,
    sanitize_hotkey_config,
)


def test_normalize_hotkey_name():
    assert normalize_hotkey_name("  F8 ") == "f8"
    assert normalize_hotkey_name(None) == ""
    assert normalize_hotkey_name(6) == "f6"
    assert normalize_hotkey_name(12) == "f12"
    assert normalize_hotkey_name("7") == "f7"
    assert normalize_hotkey_name("f9") == "f9"
    assert normalize_hotkey_name(True) == "true"
    assert normalize_hotkey_name("13") == "13"


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
    assert set(clean.keys()) == {"record", "recovery", "retry", "mode"}


def test_sanitize_ignores_invalid_dict():
    clean = sanitize_hotkey_config(None)  # type: ignore[arg-type]
    assert clean["record"] == DEFAULT_HOTKEYS["record"]


def test_hotkey_config_changed_false_for_empty():
    assert hotkey_config_changed(None, sanitize_hotkey_config({})) is False
    assert (
        hotkey_config_changed(
            {}, {"record": "f1", "recovery": "f2", "retry": "f3", "mode": "f4"}
        )
        is False
    )


def test_hotkey_config_changed_file_only_has_some_keys_skips_unset() -> None:
    """`continue` in hotkey_config_changed: actions not present in the file are not compared."""
    clean = sanitize_hotkey_config({})
    # File only sets ``mode`` and it matches the sanitizer; ``record``/``recovery``/``retry`` are absent.
    assert hotkey_config_changed({"mode": 7}, clean) is False
    assert hotkey_config_changed({"record": 9}, clean) is False


def test_hotkey_config_changed_true_when_mismatch():
    clean = sanitize_hotkey_config({})
    source = {**clean, "record": "f12"}
    assert hotkey_config_changed(source, clean) is True


def test_hotkey_config_changed_false_when_all_match_sanitized():
    clean = sanitize_hotkey_config({})
    source = {
        action: clean[action] for action in ("record", "recovery", "retry", "mode")
    }
    assert hotkey_config_changed(source, clean) is False


def test_numeric_yaml_style_f_keys_match_sanitize():
    """YAML/JSON can load F-key numbers as ints; we must not spuriously flag 'adjusted' hotkeys."""
    source = {"record": 9, "recovery": 8, "retry": 7, "mode": 6}
    clean = sanitize_hotkey_config(source)
    assert clean == {
        "record": "f9",
        "recovery": "f8",
        "retry": "f7",
        "mode": "f6",
    }
    assert hotkey_config_changed(source, clean) is False
    assert {k: normalize_hotkey_name(v) for k, v in source.items()} == clean


def test_canonical_hotkeys_non_str_key_stringifies() -> None:
    class _Key:
        def __str__(self) -> str:
            return "record"

    out = canonical_hotkeys_dict({_Key(): "f11"})
    assert out == {"record": "f11"}


def test_canonical_hotkey_yaml_keys() -> None:
    out = canonical_hotkeys_dict({"Record": "f9", "MODE": 6, "retransmit": 1})
    assert out == {"record": "f9", "mode": 6}
    # Sanitizer and “adjusted?” check use the same canonical map.
    assert sanitize_hotkey_config(
        {"Record": "f9", "Recovery": 8, "Retry": 7, "Mode": 6}
    ) == {"record": "f9", "recovery": "f8", "retry": "f7", "mode": "f6"}
    assert not hotkey_config_changed(
        {"Record": 9, "Recovery": 8, "Retry": 7, "Mode": 6},
        {
            "record": "f9",
            "recovery": "f8",
            "retry": "f7",
            "mode": "f6",
        },
    )
