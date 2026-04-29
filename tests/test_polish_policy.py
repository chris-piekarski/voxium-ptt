"""Tests for llama.cpp idle-unload policy helpers."""

from __future__ import annotations

import pytest

from voxium.polish_policy import parse_sleep_idle_seconds


def test_parse_sleep_idle_seconds_accepts_numbers_and_suffixes() -> None:
    assert parse_sleep_idle_seconds(None) == -1
    assert parse_sleep_idle_seconds("") == -1
    assert parse_sleep_idle_seconds("0") == 0
    assert parse_sleep_idle_seconds(-1) == -1
    assert parse_sleep_idle_seconds("30s") == 30
    assert parse_sleep_idle_seconds("10m") == 600
    assert parse_sleep_idle_seconds("1h") == 3600


def test_parse_sleep_idle_seconds_rejects_bad_text() -> None:
    with pytest.raises(ValueError, match="Unsupported polish keep-alive value"):
        parse_sleep_idle_seconds("later")
