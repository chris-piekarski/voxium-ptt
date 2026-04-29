"""Pure helpers for the record hotkey: tap-to-toggle plus hold-to-talk release stop."""

from __future__ import annotations

from dataclasses import dataclass

PTT_HOLD_TO_TALK_THRESHOLD_MS = 350.0

PTT_ACTION_START = "start"
PTT_ACTION_STOP = "stop"


@dataclass
class PttKeyTracker:
    """Track one physical record-key press so auto-repeat does not retrigger PTT transitions."""

    is_down: bool = False
    started_press_at_ms: float | None = None

    def reset(self) -> None:
        self.is_down = False
        self.started_press_at_ms = None


def handle_ptt_press(
    tracker: PttKeyTracker,
    *,
    now_ms: float,
    can_start: bool,
    can_stop: bool,
    last_hotkey_time_ms: float,
    start_debounce_ms: float,
    stop_debounce_ms: float,
) -> tuple[str | None, float]:
    """
    Decide what a record-key press should do.

    Returns ``(action, updated_last_hotkey_time_ms)`` where action is:
    - ``"start"`` for tap/hold start,
    - ``"stop"`` for a second press while recording,
    - ``None`` when ignored (for example: key auto-repeat while already held).
    """

    if tracker.is_down:
        return None, last_hotkey_time_ms

    tracker.is_down = True

    if not can_start and not can_stop:
        tracker.started_press_at_ms = None
        return None, last_hotkey_time_ms

    debounce = stop_debounce_ms if can_stop else start_debounce_ms
    if now_ms - last_hotkey_time_ms < debounce:
        tracker.started_press_at_ms = None
        return None, last_hotkey_time_ms

    if can_start:
        tracker.started_press_at_ms = now_ms
        return PTT_ACTION_START, now_ms

    tracker.started_press_at_ms = None
    return PTT_ACTION_STOP, now_ms


def handle_ptt_release(
    tracker: PttKeyTracker,
    *,
    now_ms: float,
    is_recording: bool,
    hold_threshold_ms: float = PTT_HOLD_TO_TALK_THRESHOLD_MS,
) -> str | None:
    """
    Stop on release only when the same press started recording and the key was held long enough.

    Short taps still behave like toggle start: release does nothing and the next press stops.
    """

    if not tracker.is_down:
        return None

    tracker.is_down = False
    started_at_ms = tracker.started_press_at_ms
    tracker.started_press_at_ms = None

    if not is_recording or started_at_ms is None:
        return None

    if now_ms - started_at_ms >= hold_threshold_ms:
        return PTT_ACTION_STOP

    return None
