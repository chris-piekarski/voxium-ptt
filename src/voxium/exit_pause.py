"""Keep the Windows console open on fatal startup errors (bare ``python -m voxium`` / double-click)."""

from __future__ import annotations

import os
import sys

try:
    import msvcrt as _msvcrt  # pylint: disable=import-error
except ImportError:
    _msvcrt = None  # type: ignore[assignment]


def pause_console_before_exit() -> None:
    """Block briefly so operators can read stderr before the console closes.

    No-op on non-Windows. Skipped when ``VOXIUM_NO_PAUSE`` is set (automation matches
    ``scripts/windows/Voxium.cmd``).
    """
    if sys.platform != "win32":
        return
    flag = (os.environ.get("VOXIUM_NO_PAUSE") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return
    prompt = "\nPress any key to close this window…"
    if _msvcrt is not None:
        try:
            print(prompt, file=sys.stderr, flush=True)
            _msvcrt.getch()
            return
        except Exception:
            pass
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            print(prompt.replace("any key", "Enter"), file=sys.stderr, flush=True)
            sys.stdin.readline()
    except (EOFError, OSError):
        pass


__all__ = ["pause_console_before_exit"]
