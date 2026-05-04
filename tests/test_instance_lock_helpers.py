"""Sanity checks for single-instance helper logic in ``voxium.app``."""

from __future__ import annotations

import os

import voxium.app as app_mod


def test_peer_pid_exists_for_current_process() -> None:
    assert app_mod._peer_pid_exists_on_this_os(os.getpid())


def test_peer_pid_not_exists_for_unlikely_pid() -> None:
    assert not app_mod._peer_pid_exists_on_this_os(9_999_999)
