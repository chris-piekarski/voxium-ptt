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
