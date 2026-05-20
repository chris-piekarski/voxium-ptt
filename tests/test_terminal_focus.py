"""Unit tests for best-effort terminal focus detection (slash/PTT guard)."""

from __future__ import annotations

import types

import pytest

import voxium.terminal_focus as tf


@pytest.fixture(autouse=True)
def _reset_focus_cache() -> None:
    tf._cached_value = None
    tf._cached_at = 0.0
    yield
    tf._cached_value = None
    tf._cached_at = 0.0


def test_is_our_true_when_impl_raises(monkeypatch) -> None:
    def _bang() -> bool:
        raise RuntimeError("no")

    monkeypatch.setattr(tf, "_is_our_terminal_focused_impl", _bang)
    assert tf.is_our_terminal_focused() is True


def test_darwin_path_returns_true(monkeypatch) -> None:
    monkeypatch.setattr(tf.platform, "system", lambda: "Darwin")
    assert tf._is_our_terminal_focused_impl() is True


def test_wsl_bypasses_linux_path(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_is_wsl", lambda: True)
    assert tf._linux_x11_window_matches_active() is True


def test_linux_no_display_skips_x11_probe(monkeypatch) -> None:
    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert tf._linux_x11_window_matches_active() is True


def test_linux_xdotool_happy_path(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "DISPLAY", ":0")
    monkeypatch.setitem(os.environ, "WINDOWID", "99")
    monkeypatch.setattr(tf, "shutil", types.SimpleNamespace(which=lambda x: "/b/x"))
    r = types.SimpleNamespace(returncode=0, stdout="99\n", stderr="")
    monkeypatch.setattr(tf.subprocess, "run", lambda *a, **k: r)
    assert tf._linux_x11_window_matches_active() is True
    r2 = types.SimpleNamespace(returncode=0, stdout="1\n", stderr="")
    monkeypatch.setattr(tf.subprocess, "run", lambda *a, **k: r2)
    assert tf._linux_x11_window_matches_active() is False


def test_voxium_window_title_tokens_includes_env(monkeypatch) -> None:
    import os

    monkeypatch.setitem(os.environ, "VOXIUM_WINDOW_TITLE", "myterm")
    try:
        assert "myterm" in tf._voxium_window_title_tokens()
    finally:
        os.environ.pop("VOXIUM_WINDOW_TITLE", None)


def test_focus_caches_result_within_ttl(monkeypatch) -> None:
    calls: list[int] = []

    def _impl() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(tf, "_is_our_terminal_focused_impl", _impl)
    assert tf.is_our_terminal_focused() is True
    assert len(calls) == 1
    assert tf.is_our_terminal_focused() is True
    assert len(calls) == 1


def test_is_wsl_detects_microsoft_kernel_release(monkeypatch) -> None:
    u = types.SimpleNamespace(release="5.15.0-microsoft-standard-WSL2")
    monkeypatch.setattr(tf.platform, "uname", lambda: u)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    assert tf._is_wsl() is True


def test_linux_wayland_without_windowid_is_permissive(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("WINDOWID", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert tf._linux_x11_window_matches_active() is True


def test_xdotool_subprocess_error_is_permissive(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "DISPLAY", ":0")
    monkeypatch.setitem(os.environ, "WINDOWID", "1")
    monkeypatch.setattr(tf, "shutil", types.SimpleNamespace(which=lambda x: "/x"))
    monkeypatch.setattr(
        tf.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")),
    )
    assert tf._linux_x11_window_matches_active() is True


def test_force_terminal_focus_check_reruns_impl(monkeypatch) -> None:
    calls: list[int] = []

    def _impl() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(tf, "_is_our_terminal_focused_impl", _impl)
    one = tf.is_our_terminal_focused()
    two = tf.force_terminal_focus_check()
    assert one and two
    assert len(calls) == 2


def test_dispatcher_windows_branch(monkeypatch) -> None:
    monkeypatch.setattr(tf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tf, "_windows_foreground_looks_like_ours", lambda: True)
    assert tf._is_our_terminal_focused_impl() is True


def test_dispatcher_linux_branch(monkeypatch) -> None:
    monkeypatch.setattr(tf.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tf, "_linux_x11_window_matches_active", lambda: False)
    assert tf._is_our_terminal_focused_impl() is False


def test_dispatcher_unknown_system_returns_true(monkeypatch) -> None:
    monkeypatch.setattr(tf.platform, "system", lambda: "Plan9")
    assert tf._is_our_terminal_focused_impl() is True


def test_linux_invalid_windowid_is_permissive(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "DISPLAY", ":0")
    monkeypatch.setitem(os.environ, "WINDOWID", "not-a-number")
    assert tf._linux_x11_window_matches_active() is True


def test_linux_no_xdotool_is_permissive(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "DISPLAY", ":0")
    monkeypatch.setitem(os.environ, "WINDOWID", "42")
    monkeypatch.setattr(tf, "shutil", types.SimpleNamespace(which=lambda x: None))
    assert tf._linux_x11_window_matches_active() is True


def test_xdotool_nonzero_returncode_is_permissive(monkeypatch) -> None:
    import os

    monkeypatch.setattr(tf, "_is_wsl", lambda: False)
    monkeypatch.setitem(os.environ, "DISPLAY", ":0")
    monkeypatch.setitem(os.environ, "WINDOWID", "1")
    monkeypatch.setattr(tf, "shutil", types.SimpleNamespace(which=lambda x: "/x"))
    r = types.SimpleNamespace(returncode=1, stdout="", stderr="oops")
    monkeypatch.setattr(tf.subprocess, "run", lambda *a, **k: r)
    assert tf._linux_x11_window_matches_active() is True


def test_is_wsl_detects_wsl_interop_env(monkeypatch) -> None:
    import os

    u = types.SimpleNamespace(release="5.15.0-linux-generic")
    monkeypatch.setattr(tf.platform, "uname", lambda: u)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setitem(os.environ, "WSL_INTEROP", "/run/wsl/123_interop")
    assert tf._is_wsl() is True
