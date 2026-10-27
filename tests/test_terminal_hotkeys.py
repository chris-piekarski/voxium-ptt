"""Terminal function-key bytes that pynput never sees."""

from voxium.terminal_hotkeys import (
    TerminalHotkeyPump,
    TerminalKeyEvent,
    pop_terminal_key_events,
)


def test_f9_bare_sequence_is_a_tap() -> None:
    events, rest = pop_terminal_key_events(b"\x1b[20~")
    assert rest == b""
    assert events == [TerminalKeyEvent("f9", "press", False)]


def test_f9_kitty_release_is_explicit() -> None:
    events, rest = pop_terminal_key_events(b"\x1b[20;1:3~")
    assert rest == b""
    assert events == [TerminalKeyEvent("f9", "release", True)]


def test_split_sequence_waits_for_the_trailer() -> None:
    first, rest = pop_terminal_key_events(b"noise\x1b[2")
    assert first == []
    assert rest.endswith(b"\x1b[2")
    events, rest = pop_terminal_key_events(rest + b"0~")
    assert rest == b""
    assert events == [TerminalKeyEvent("f9", "press", False)]


def test_other_ptt_keys() -> None:
    events, _rest = pop_terminal_key_events(b"\x1b[17~\x1b[18~\x1b[19~")
    assert [event.name for event in events] == ["f6", "f7", "f8"]


def test_bare_press_releases_so_the_next_tap_can_stop() -> None:
    seen: list[tuple[str, str]] = []
    pump = TerminalHotkeyPump(
        on_press=lambda key: seen.append(("press", key)),
        on_release=lambda key: seen.append(("release", key)),
        key_for_name=lambda name: name,
        clock=lambda: 0.0,
    )
    pump.handle(TerminalKeyEvent("f9", "press", False))
    assert seen == [("press", "f9"), ("release", "f9")]


def test_explicit_press_waits_for_a_real_release() -> None:
    seen: list[str] = []
    pump = TerminalHotkeyPump(
        on_press=lambda key: seen.append("press"),
        on_release=lambda key: seen.append("release"),
        key_for_name=lambda name: name,
        clock=lambda: 0.0,
    )
    pump.handle(TerminalKeyEvent("f9", "press", True))
    pump.handle(TerminalKeyEvent("f9", "release", True))
    assert seen == ["press", "release"]


def test_repeat_burst_does_not_retrigger() -> None:
    seen: list[str] = []
    now = {"t": 10.0}
    pump = TerminalHotkeyPump(
        on_press=lambda key: seen.append("press"),
        on_release=lambda key: seen.append("release"),
        key_for_name=lambda name: name,
        clock=lambda: now["t"],
    )
    pump.handle(TerminalKeyEvent("f9", "press", False))
    now["t"] = 10.2
    pump.handle(TerminalKeyEvent("f9", "press", False))
    now["t"] = 10.5
    pump.handle(TerminalKeyEvent("f9", "press", False))
    now["t"] = 11.1
    pump.handle(TerminalKeyEvent("f9", "press", False))
    assert seen == ["press", "release", "press", "release"]
