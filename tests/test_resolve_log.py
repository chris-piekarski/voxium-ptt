"""Tests for voxium.resolve_log."""

from voxium.resolve_log import resolve_log_level


def test_priority_explicit_log_level():
    assert resolve_log_level(log_level="DEBUG", quiet=True) == "DEBUG"


def test_quiet_wins_over_verbose():
    assert resolve_log_level(quiet=True, verbose=1) == "ERROR"


def test_verbose_debug():
    assert resolve_log_level(verbose=2) == "DEBUG"


def test_default_info():
    assert resolve_log_level() == "INFO"
