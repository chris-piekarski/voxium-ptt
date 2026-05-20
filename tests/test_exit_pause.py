"""Tests for ``voxium.exit_pause``."""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

import voxium.exit_pause as exit_pause_mod
from voxium.exit_pause import pause_console_before_exit


def test_pause_no_op_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    pause_console_before_exit()  # should return immediately


def test_pause_skipped_when_voxium_no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("VOXIUM_NO_PAUSE", "1")
    getch = mock.Mock()
    fake_msvcrt = types.SimpleNamespace(getch=getch)
    monkeypatch.setattr(exit_pause_mod, "_msvcrt", fake_msvcrt)
    pause_console_before_exit()
    getch.assert_not_called()


def test_pause_uses_msvcrt_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOXIUM_NO_PAUSE", raising=False)
    getch = mock.Mock(return_value=b"\r")
    fake_msvcrt = types.SimpleNamespace(getch=getch)
    monkeypatch.setattr(exit_pause_mod, "_msvcrt", fake_msvcrt)
    pause_console_before_exit()
    getch.assert_called_once()


def test_pause_falls_back_to_stdin_when_msvcrt_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If msvcrt.getch raises, we drop through to the stdin readline branch."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOXIUM_NO_PAUSE", raising=False)

    getch = mock.Mock(side_effect=Exception("bad"))
    fake_msvcrt = types.SimpleNamespace(getch=getch)
    monkeypatch.setattr(exit_pause_mod, "_msvcrt", fake_msvcrt)

    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = True
    fake_stdin.readline.return_value = "\n"
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    pause_console_before_exit()
    getch.assert_called_once()
    fake_stdin.readline.assert_called_once()


def test_pause_swallows_stdin_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError/EOFError from stdin.readline must not propagate."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOXIUM_NO_PAUSE", raising=False)
    monkeypatch.setattr(exit_pause_mod, "_msvcrt", None)

    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = True
    fake_stdin.readline.side_effect = OSError("piped")
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    pause_console_before_exit()  # must not raise


def test_pause_no_op_when_stdin_not_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """If stdin isn't a TTY, skip the readline prompt entirely."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("VOXIUM_NO_PAUSE", raising=False)
    monkeypatch.setattr(exit_pause_mod, "_msvcrt", None)

    fake_stdin = mock.Mock()
    fake_stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    pause_console_before_exit()
    fake_stdin.readline.assert_not_called()
