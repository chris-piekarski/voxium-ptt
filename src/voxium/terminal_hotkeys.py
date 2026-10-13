"""Function keys typed into this terminal, for panes that never emit an X11 key.

Herdr delivers F9 as the VT sequence ``ESC [ 20 ~``. pynput only hears X11
key events, so a press in the Voxium pane used to do nothing. This module
reads those bytes from the tty and turns them into press/release callbacks.

A bare sequence has no key-up. The dispatcher treats that as a short tap:
press, then release. Hold-to-talk release still works when the terminal sends
a Kitty event type (``ESC [ 20 ; 1 : 3 ~``).
"""

from __future__ import annotations

import os
import select
import sys
import threading
import time
from dataclasses import dataclass

# Disambiguate escape codes and report press/repeat/release. Pop on the way out
# so the shell that follows is not left in the enhanced keyboard mode.
_KITTY_KEYBOARD_PUSH = b"\x1b[>3u"
_KITTY_KEYBOARD_POP = b"\x1b[<u"

# xterm/vt sequences. F1–F4 also arrive as SS3 (ESC O P …).
_TILDE_KEY_BY_NUMBER = {
    11: "f1",
    12: "f2",
    13: "f3",
    14: "f4",
    15: "f5",
    17: "f6",
    18: "f7",
    19: "f8",
    20: "f9",
    21: "f10",
    23: "f11",
    24: "f12",
}
_SS3_KEY = {b"OP": "f1", b"OQ": "f2", b"OR": "f3", b"OS": "f4"}
_EVENT_KIND = {1: "press", 2: "repeat", 3: "release"}


@dataclass(frozen=True)
class TerminalKeyEvent:
    """One decoded function key. ``explicit`` is set when the sequence carried an event type."""

    name: str
    kind: str
    explicit: bool = False


def pop_terminal_key_events(buf: bytes) -> tuple[list[TerminalKeyEvent], bytes]:
    """Pull complete function-key sequences out of ``buf``. Keep a partial trailer."""
    events: list[TerminalKeyEvent] = []
    index = 0
    limit = len(buf)
    while index < limit:
        if buf[index] != 0x1B:
            index += 1
            continue
        if index + 1 >= limit:
            break
        follower = buf[index + 1]
        if follower == ord("O"):
            if index + 2 >= limit:
                break
            name = _SS3_KEY.get(buf[index + 1 : index + 3])
            if name is None:
                index += 1
                continue
            events.append(TerminalKeyEvent(name, "press"))
            index += 3
            continue
        if follower != ord("["):
            index += 1
            continue
        end = index + 2
        while end < limit and not 0x40 <= buf[end] <= 0x7E:
            end += 1
            if end - index > 32:
                index += 1
                break
        else:
            if end >= limit:
                break
            parsed = _parse_csi_function_key(buf[index + 2 : end], buf[end])
            if parsed is not None:
                events.append(parsed)
            index = end + 1
            continue
    return events, buf[index:]


def _parse_csi_function_key(body: bytes, final: int) -> TerminalKeyEvent | None:
    if final != ord("~"):
        return None
    number_text, _, rest = body.partition(b";")
    if not number_text.isdigit():
        return None
    name = _TILDE_KEY_BY_NUMBER.get(int(number_text))
    if name is None:
        return None
    explicit = b":" in rest
    kind = "press"
    if explicit:
        _mods, _, event_text = rest.partition(b":")
        if not event_text.isdigit():
            return None
        kind = _EVENT_KIND.get(int(event_text), "press")
    return TerminalKeyEvent(name, kind, explicit)


class TerminalHotkeyPump:
    """Read function keys from the controlling tty and forward them."""

    def __init__(
        self,
        on_press,
        on_release,
        *,
        key_for_name=None,
        coalesce_s: float = 0.4,
        clock=None,
    ) -> None:
        self._on_press = on_press
        self._on_release = on_release
        self._key_for_name = key_for_name or _pynput_key
        self._coalesce_s = coalesce_s
        self._clock = clock or time.monotonic
        self._last_press: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._termios_previous: list | None = None

    def handle(self, event: TerminalKeyEvent) -> None:
        """Apply one decoded key. Bare presses also release so the next tap can stop PTT."""
        try:
            key = self._key_for_name(event.name)
        except (AttributeError, KeyError):
            return
        if event.kind == "repeat":
            return
        if event.kind == "release":
            self._on_release(key)
            return
        now = float(self._clock())
        previous = self._last_press.get(event.name)
        self._last_press[event.name] = now
        if previous is not None and now - previous < self._coalesce_s:
            return
        self._on_press(key)
        if not event.explicit:
            self._on_release(key)

    def start(self) -> None:
        if self._thread is not None or os.name != "posix":
            return
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
        import termios
        import tty

        self._fd = fd
        self._termios_previous = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        os.write(fd, _KITTY_KEYBOARD_PUSH)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="voxium-terminal-hotkeys", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None
        fd = self._fd
        previous = self._termios_previous
        if fd is not None and previous is not None:
            import termios

            try:
                os.write(fd, _KITTY_KEYBOARD_POP)
            except OSError:
                pass
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, previous)
            except termios.error:
                pass
        self._fd = None
        self._termios_previous = None

    def _loop(self) -> None:
        fd = self._fd
        if fd is None:
            return
        pending = b""
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                chunk = os.read(fd, 256)
            except OSError:
                return
            if not chunk:
                return
            pending += chunk
            events, pending = pop_terminal_key_events(pending)
            for event in events:
                self.handle(event)


def _pynput_key(name: str):
    from pynput import keyboard

    return getattr(keyboard.Key, name)
