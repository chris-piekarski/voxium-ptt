"""
Best-effort detection: is the key focus on *this* terminal (or a window owned by the Voxium process).

Used so ``/`` command input does not activate when the operator is typing in another
application. :func:`pynput` is global, so this check is the guard rail.

* Windows: ``GetConsoleWindow == GetForegroundWindow``, and foreground PID in ``{pid, ppid}``.
* Linux (X11): if ``$WINDOWID`` is set, compare to ``xdotool getactivewindow`` when available.
* macOS: not reliably available without private APIs; returns ``True`` (same as unknown).
* Otherwise: return ``True`` (cannot determine — keep legacy behavior, e.g. headless, Wayland, SSH).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time

_CACHE_TTL_S = 0.12
_cached_value: bool | None = None
_cached_at: float = 0.0


def _is_our_terminal_focused_impl() -> bool:
    system = platform.system()
    if system == "Windows":
        return _windows_foreground_looks_like_ours()
    if system == "Linux":
        return _linux_x11_window_matches_active()
    if system == "Darwin":
        return _macos_frontmost_looks_like_ours()
    return True


def is_our_terminal_focused() -> bool:
    """
    Return whether the OS *probably* has keyboard focus in this terminal or this process.

    If detection is unavailable, returns ``True`` so PTT and slash are not bricked
    in SSH, WSL text-only, etc.
    """
    global _cached_value, _cached_at
    now = time.monotonic()
    if _cached_value is not None and (now - _cached_at) < _CACHE_TTL_S:
        return _cached_value
    try:
        _cached_value = _is_our_terminal_focused_impl()
    except Exception:
        _cached_value = True
    _cached_at = now
    return _cached_value


def force_terminal_focus_check() -> bool:
    """Next :func:`is_our_terminal_focused` call re-runs the probe (e.g. after a long sleep)."""
    global _cached_value, _cached_at
    _cached_value = None
    _cached_at = 0.0
    return is_our_terminal_focused()


def _windows_foreground_looks_like_ours() -> bool:  # pragma: no cover
    import ctypes
    from ctypes import wintypes  # noqa: TCH002

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return True
    try:
        user32 = windll.user32
        kernel32 = windll.kernel32
    except Exception:
        return True

    our_hwnd = kernel32.GetConsoleWindow()
    fg = user32.GetForegroundWindow()
    if our_hwnd and fg == our_hwnd:
        return True

    if not fg:
        return False
    title, class_name = _windows_window_text(fg)
    if _windows_window_text_matches_our_terminal(title, class_name):
        return True

    pid_w = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_w))
    fgp = int(pid_w.value)
    pid = os.getpid()
    try:
        ppid = os.getppid()
    except Exception:
        ppid = -1
    if fgp == pid:
        return True
    if ppid >= 0 and fgp == ppid:
        return True
    return False


def _windows_window_text(hwnd: int) -> tuple[str, str]:  # pragma: no cover
    import ctypes

    title_buf = ctypes.create_unicode_buffer(512)
    class_buf = ctypes.create_unicode_buffer(256)
    try:
        # windll is Windows-only; not in typeshed for Linux CI mypy
        _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        _user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        _user32.GetClassNameW(hwnd, class_buf, len(class_buf))
    except Exception:
        return "", ""
    return title_buf.value, class_buf.value


def _windows_window_text_matches_our_terminal(  # pragma: no cover
    title: str, class_name: str
) -> bool:
    hay = f"{title}\n{class_name}".lower()
    return any(token in hay for token in _voxium_window_title_tokens())


def _voxium_window_title_tokens() -> tuple[str, ...]:
    custom = (os.environ.get("VOXIUM_WINDOW_TITLE") or "").strip().lower()
    tokens: list[str] = []
    if custom:
        tokens.append(custom)
    if "voxium" not in tokens:
        tokens.append("voxium")
    return tuple(t for t in tokens if t)


def _linux_x11_window_matches_active() -> bool:
    if _is_wsl():
        return True
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return True
    # Wayland: no stable cheap focus API in scope; do not break slash in GNOME+WT.
    if "WAYLAND_DISPLAY" in os.environ and not os.environ.get("WINDOWID"):
        return True
    wid = (os.environ.get("WINDOWID") or "").strip()
    if not wid or not wid.isdigit():
        return True
    exe = shutil.which("xdotool")
    if not exe:
        return True
    try:
        r = subprocess.run(
            [exe, "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=0.1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return True
    if r.returncode != 0 or not (r.stdout or "").strip().isdigit():
        return True
    return (r.stdout or "").strip() == wid


def _is_wsl() -> bool:
    rel = (platform.uname().release or "").lower()
    if "microsoft" in rel or "wsl" in rel:
        return True
    return bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))


def _macos_frontmost_looks_like_ours() -> bool:
    # Tying foreground to our PID is unreliable: Terminal / iTerm host the shell+Python, but
    # the frontmost process is often the emulator, not *python* or *zsh*'s PID. Until we
    # adopt a TTY-based read path, do not false-negative slash on macOS.
    return True
