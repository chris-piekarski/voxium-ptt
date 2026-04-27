"""Tests for voxium.cli_argv."""

from voxium.cli_argv import normalize_cli_args


def test_normalize_empty_uses_run():
    assert normalize_cli_args([]) == ["run"]


def test_normalize_preserves_help_and_version():
    assert normalize_cli_args(["-h"]) == ["-h"]
    assert normalize_cli_args(["--version"]) == ["--version"]


def test_normalize_preserves_known_subcommands():
    for cmd in ("run", "server", "health"):
        assert normalize_cli_args([cmd]) == [cmd]


def test_normalize_injects_run_before_flags():
    assert normalize_cli_args(["--device", "cpu"]) == ["run", "--device", "cpu"]


def test_normalize_passes_through_unknown_verb():
    # Not a subcommand: kept as legacy-style bare token (covered by final return).
    assert normalize_cli_args(["config"]) == ["config"]
