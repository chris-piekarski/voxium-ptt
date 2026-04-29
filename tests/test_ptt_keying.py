"""Tests for record-key press/hold semantics."""

from __future__ import annotations

from voxium.ptt_keying import (
    PTT_ACTION_START,
    PTT_ACTION_STOP,
    PTT_HOLD_TO_TALK_THRESHOLD_MS,
    PttKeyTracker,
    handle_ptt_press,
    handle_ptt_release,
)


def test_tap_to_start_does_not_stop_on_quick_release() -> None:
    tracker = PttKeyTracker()
    action, last = handle_ptt_press(
        tracker,
        now_ms=1000.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert action == PTT_ACTION_START
    assert last == 1000.0
    assert (
        handle_ptt_release(
            tracker,
            now_ms=1000.0 + PTT_HOLD_TO_TALK_THRESHOLD_MS - 1.0,
            is_recording=True,
        )
        is None
    )


def test_hold_to_talk_stops_on_release_after_threshold() -> None:
    tracker = PttKeyTracker()
    action, _last = handle_ptt_press(
        tracker,
        now_ms=2000.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert action == PTT_ACTION_START
    assert (
        handle_ptt_release(
            tracker,
            now_ms=2000.0 + PTT_HOLD_TO_TALK_THRESHOLD_MS,
            is_recording=True,
        )
        == PTT_ACTION_STOP
    )


def test_repeat_press_while_key_is_held_is_ignored() -> None:
    tracker = PttKeyTracker()
    action1, last1 = handle_ptt_press(
        tracker,
        now_ms=1000.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    action2, last2 = handle_ptt_press(
        tracker,
        now_ms=1100.0,
        can_start=False,
        can_stop=True,
        last_hotkey_time_ms=last1,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert action1 == PTT_ACTION_START
    assert action2 is None
    assert last2 == last1


def test_reset_clears_tracker() -> None:
    tracker = PttKeyTracker()
    tracker.is_down = True
    tracker.started_press_at_ms = 1.0
    tracker.reset()
    assert not tracker.is_down
    assert tracker.started_press_at_ms is None


def test_press_ignored_when_neither_start_nor_stop_available() -> None:
    tracker = PttKeyTracker()
    act, _ = handle_ptt_press(
        tracker,
        now_ms=1.0,
        can_start=False,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=0.0,
        stop_debounce_ms=0.0,
    )
    assert act is None
    assert tracker.is_down
    assert tracker.started_press_at_ms is None


def test_debounce_suppresses_start() -> None:
    tracker = PttKeyTracker()
    a1, last = handle_ptt_press(
        tracker,
        now_ms=1000.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert a1 == PTT_ACTION_START
    handle_ptt_release(tracker, now_ms=1005.0, is_recording=False)
    a2, _ = handle_ptt_press(
        tracker,
        now_ms=1100.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=last,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert a2 is None


def test_release_when_key_not_down() -> None:
    tracker = PttKeyTracker()
    assert handle_ptt_release(tracker, now_ms=0.0, is_recording=True) is None


def test_release_not_recording() -> None:
    tracker = PttKeyTracker()
    handle_ptt_press(
        tracker,
        now_ms=0.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=0.0,
        stop_debounce_ms=0.0,
    )
    out = handle_ptt_release(tracker, now_ms=0.0, is_recording=False)
    assert out is None


def test_second_press_while_recording_stops_immediately() -> None:
    tracker = PttKeyTracker()
    _action1, last = handle_ptt_press(
        tracker,
        now_ms=1000.0,
        can_start=True,
        can_stop=False,
        last_hotkey_time_ms=0.0,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    handle_ptt_release(
        tracker,
        now_ms=1005.0,
        is_recording=True,
    )
    action2, _last2 = handle_ptt_press(
        tracker,
        now_ms=1300.0,
        can_start=False,
        can_stop=True,
        last_hotkey_time_ms=last,
        start_debounce_ms=220.0,
        stop_debounce_ms=0.0,
    )
    assert action2 == PTT_ACTION_STOP
