"""Tests for input_mode_set handler and VOX dead-man timer (PR 11).

Patches _start_vox_listening / _stop_vox_listening so no sound device or
whisper server is needed.  Uses a short deadline (2 s) instead of the
product default of 120 s.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mcp.server")

import voxium.agent_dispatch as _disp
import voxium.agent_server as _mod
from voxium.agent_dispatch import (
    _HANDLER_MAP,
    AgentDispatch,
    ClientState,
    FakeSessionPort,
)

# ---------------------------------------------------------------------------
# Fixture: reset shared agent state before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_vox_state():
    """Reset VOX agent state so tests don't leak across each other."""
    from voxium.agent_dispatch import TokenBucket

    _mod._vox_auto_disarmed = False
    _mod._vox_disarming = False
    _mod._vox_max_seconds = 120
    _mod._vox_cap_invalid = False
    if _mod._vox_deadman_timer is not None:
        _mod._vox_deadman_timer.cancel()
        _mod._vox_deadman_timer = None
    _mod._shutting_down = False
    _mod._live_session = None
    # Reset rate-limit buckets (create fresh — capacity can be clobbered)
    _disp.AgentDispatch._mutable_bucket = TokenBucket(capacity=2, refill_per_second=2.0)
    _disp.AgentDispatch._read_bucket = TokenBucket(capacity=20, refill_per_second=20.0)
    yield
    # Teardown: cancel any leftover timer
    if _mod._vox_deadman_timer is not None:
        _mod._vox_deadman_timer.cancel()
        _mod._vox_deadman_timer = None


# ---------------------------------------------------------------------------
# Helpers: build dispatch with input:vox scope
# ---------------------------------------------------------------------------


def _make_dispatch(
    *,
    state: ClientState = ClientState.IDLE,
    input_mode: str = "ptt",
    granted: set[str] | None = None,
):
    fp = FakeSessionPort(state=state, input_mode=input_mode)
    _HANDLER_MAP.update(_mod._build_read_handler_map())
    return AgentDispatch(fp, granted_scopes=granted or {"input:vox"})


# ---------------------------------------------------------------------------
# Basic arm / disarm
# ---------------------------------------------------------------------------


def test_arm_vox_success():
    """Arming VOX when IDLE + PTT succeeds and starts the dead-man timer."""
    with patch.object(_mod, "_vox_max_seconds", 2):
        dispatch = _make_dispatch()

        with (
            patch("voxium.app._start_vox_listening", return_value=True),
            patch("voxium.app._stop_vox_listening"),
            patch("voxium.app._set_input_mode"),
            patch("voxium.app.show_status"),
            patch("voxium.app.ptt_status_box"),
        ):
            import voxium.app as _app

            _app.config = MagicMock()
            _app.config.minimal = True

            result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
            assert result["ok"] is True
            assert result["data"]["input_mode"] == "vox"
            # Timer should have been started
            assert _mod._vox_deadman_timer is not None


def test_arm_vox_false_start_leaves_mode_ptt():
    """When _start_vox_listening returns False, mode is reverted to ptt and
    the error includes details.reason = 'portaudio'."""
    dispatch = _make_dispatch()

    with (
        patch("voxium.app._start_vox_listening", return_value=False),
        patch("voxium.app._set_input_mode") as mock_set_mode,
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        import voxium.app as _app

        _app.config = MagicMock()
        _app.config.minimal = True
        _app.stream = None

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert not result["ok"]
        assert result["error"]["code"] == "microphone_unavailable"
        assert result["error"]["details"]["reason"] == "portaudio"
        # _set_input_mode was called with "vox" then reverted to "ptt"
        calls = [c[0][0] for c in mock_set_mode.call_args_list]
        assert "vox" in calls
        assert calls[-1] == "ptt"
        # Timer should NOT have been started
        assert _mod._vox_deadman_timer is None


def test_arm_vox_blocked_by_active_ptt_stream():
    """If app.stream is not None, arm is refused with details.reason
    'ptt_stream_held' and _start_vox_listening is never called."""
    dispatch = _make_dispatch()

    with (
        patch("voxium.app._start_vox_listening") as mock_start,
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        import voxium.app as _app

        _app.config = MagicMock()
        _app.config.minimal = True
        _app.stream = MagicMock()  # simulate an active PTT stream

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert not result["ok"]
        assert result["error"]["code"] == "microphone_unavailable"
        assert result["error"]["details"]["reason"] == "ptt_stream_held"
        mock_start.assert_not_called()


def test_disarm_vox():
    """Disarming VOX stops listening and sets mode to ptt."""
    import voxium.app as _app

    dispatch = _make_dispatch(input_mode="vox")

    with (
        patch.object(_app, "input_mode", "vox"),
        patch("voxium.app._stop_vox_listening") as mock_stop,
        patch("voxium.app._set_input_mode") as mock_set_mode,
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "ptt"})
        assert result["ok"] is True
        assert result["data"]["input_mode"] == "ptt"
        mock_stop.assert_called_once()
        mock_set_mode.assert_called_with("ptt")


# ---------------------------------------------------------------------------
# Config reader: out-of-range VOX cap must reject arm (not fall back to 120)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_cap", [0, 3601, 1.5, "120"])
def test_vox_cap_rejects_out_of_range_config(bad_cap):
    """A configured but invalid vox_max_seconds must reject VOX arm.

    The value is passed through the same reader start_agent_listener uses
    (``_resolve_vox_cap``), not assigned to ``_vox_max_seconds`` after the
    filter, so it proves the 120 s fallback does not silently open the mic.
    """
    import voxium.app as _app

    _mod._resolve_vox_cap({"vox_max_seconds": bad_cap})
    assert _mod._vox_cap_invalid is True

    dispatch = _make_dispatch()

    with (
        patch("voxium.app._set_input_mode") as mock_set,
        patch("voxium.app._start_vox_listening") as mock_start,
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True
        _app.stream = None

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_arguments"
        mock_set.assert_not_called()
        mock_start.assert_not_called()


def test_vox_cap_absent_uses_default_120():
    """Absent vox_max_seconds keeps the 120 s default and still allows arm."""
    import voxium.app as _app

    _mod._resolve_vox_cap({})  # key absent
    assert _mod._vox_cap_invalid is False
    assert _mod._vox_max_seconds == 120

    dispatch = _make_dispatch()

    with (
        patch("voxium.app._start_vox_listening", return_value=True),
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
        patch.object(_app, "stream", None),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert result["ok"] is True
        assert result["data"]["input_mode"] == "vox"


# ---------------------------------------------------------------------------
# Idempotency: already vox, already ptt
# ---------------------------------------------------------------------------


def test_idempotent_vox_while_already_vox():
    """Setting vox while already vox (IDLE) succeeds without reopening stream."""
    import voxium.app as _app

    dispatch = _make_dispatch(input_mode="vox")

    with (
        patch.object(_app, "input_mode", "vox"),
        patch("voxium.app._start_vox_listening") as mock_start,
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert result["ok"] is True
        assert result["data"]["input_mode"] == "vox"
        # Must NOT call _start_vox_listening
        mock_start.assert_not_called()


def test_idempotent_ptt_while_already_ptt():
    """Setting ptt while already ptt (IDLE) succeeds without side effects."""
    dispatch = _make_dispatch()

    with (
        patch("voxium.app._start_vox_listening"),
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        import voxium.app as _app

        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "ptt"})
        assert result["ok"] is True
        assert result["data"]["input_mode"] == "ptt"


# ---------------------------------------------------------------------------
# Busy state: RECORDING rejects input_mode_set even for current mode
# ---------------------------------------------------------------------------


def test_recording_refuses_arm():
    """RECORDING state refuses VOX arm with session_busy."""
    dispatch = _make_dispatch(state=ClientState.RECORDING)

    result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"
    assert result["error"]["details"]["state"] == "recording"


def test_recording_refuses_same_mode():
    """RECORDING state refuses even the SAME mode with session_busy."""
    dispatch = _make_dispatch(
        state=ClientState.RECORDING,
        input_mode="vox",
    )

    result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"
    assert result["error"]["details"]["state"] == "recording"


# ---------------------------------------------------------------------------
# Dead-man timer: does not disarm while RECORDING
# ---------------------------------------------------------------------------


def test_deadman_retries_during_recording():
    """The timer must not disarm while state is RECORDING; it sleeps and
    retries. It disarms only after state leaves RECORDING (here to IDLE).
    """
    import voxium.app as _app

    _mod._vox_max_seconds = 0.3  # short deadline for tests

    # Create a state_lock that's always available
    real_lock = threading.Lock()

    # Record the state observed each time the disarm path runs. Disarming
    # while state is still RECORDING is the bug this test must catch.
    _call_states: list = []

    def _record_stop(*_args, **_kwargs):
        with real_lock:
            _call_states.append(_app.state)

    with (
        patch.object(_app, "input_mode", "vox"),
        patch.object(_app, "state_lock", real_lock),
        patch.object(_app, "state", _app.State.RECORDING),
        patch("voxium.app._stop_vox_listening", side_effect=_record_stop) as mock_stop,
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch(
            "voxium.app.config",
            new_callable=lambda: MagicMock(minimal=True),
        ),
        patch("voxium.app.ptt_status_box"),
    ):
        # Start the dead-man timer (normally started by the arm handler)
        _mod._start_vox_deadman()
        assert _mod._vox_deadman_timer is not None

        # The timer fires after 0.3 s, sees RECORDING, sleeps 200 ms and
        # retries. After a short delay, switch state to IDLE so it can disarm.
        def _switch_to_idle_after_delay():
            time.sleep(0.8)  # let the timer retry a few times during RECORDING
            with real_lock:
                _app.state = _app.State.IDLE

        switcher = threading.Thread(target=_switch_to_idle_after_delay, daemon=True)
        switcher.start()

        # Wait for the timer to run (give it some buffer)
        _mod._vox_deadman_timer.join(timeout=2.0)

        switcher.join(timeout=1.0)
        assert _mod._vox_auto_disarmed is True
        mock_stop.assert_called()
        # Must never have disarmed while state was still RECORDING.
        assert _app.State.RECORDING not in _call_states


# ---------------------------------------------------------------------------
# Dead-man timer: cancels when mode is already ptt
# ---------------------------------------------------------------------------


def test_deadman_cancels_when_already_ptt():
    """If mode is already ptt when the timer fires, it disarms no-ops."""
    import voxium.app as _app

    _mod._vox_max_seconds = 0.3

    with patch.object(_app, "input_mode", "ptt"):
        _mod._start_vox_deadman()
        assert _mod._vox_deadman_timer is not None
        # Wait for the timer to fire
        _mod._vox_deadman_timer.join(timeout=1.0)
        assert _mod._vox_deadman_timer.is_alive() is False
        # No disarm should have happened
        assert _mod._vox_auto_disarmed is False


# ---------------------------------------------------------------------------
# Dead-man timer: TRANSCRIBING disarms immediately
# ---------------------------------------------------------------------------


def test_deadman_disarms_during_transcribing():
    """Timer fires during TRANSCRIBING: disarms without waiting."""
    import voxium.app as _app

    _mod._vox_max_seconds = 0.3

    real_lock = threading.Lock()

    with (
        patch.object(_app, "input_mode", "vox"),
        patch.object(_app, "state_lock", real_lock),
        patch.object(_app, "state", _app.State.TRANSCRIBING),
        patch("voxium.app._stop_vox_listening") as mock_stop,
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch(
            "voxium.app.config",
            new_callable=lambda: MagicMock(minimal=True),
        ),
        patch("voxium.app.ptt_status_box"),
    ):
        _mod._start_vox_deadman()
        _mod._vox_deadman_timer.join(timeout=1.0)
        mock_stop.assert_called_once()
        assert _mod._vox_auto_disarmed is True


# ---------------------------------------------------------------------------
# vox_auto_disarmed cleared on next arm
# ---------------------------------------------------------------------------


def test_auto_disarmed_cleared_on_arm():
    """After timer sets vox_auto_disarmed=True, a new arm clears it."""
    import voxium.app as _app

    _mod._vox_auto_disarmed = True  # simulating prior timer disarm
    dispatch = _make_dispatch()

    with (
        patch.object(_mod, "_vox_max_seconds", 2),
        patch.object(_app, "input_mode", "ptt"),
        patch("voxium.app._start_vox_listening", return_value=True),
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
        patch.object(_app, "stream", None),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True

        dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert _mod._vox_auto_disarmed is False


# ---------------------------------------------------------------------------
# calling vox while already vox does NOT reset the timer
# ---------------------------------------------------------------------------


def test_vox_while_vox_does_not_reset_timer():
    """Arm again while already VOX must not reset the deadline."""
    import voxium.app as _app

    _mod._vox_max_seconds = 2

    dispatch = _make_dispatch(input_mode="vox")

    # Pre-arm a timer (simulating a prior successful arm)
    _mod._start_vox_deadman()
    first_timer = _mod._vox_deadman_timer
    assert first_timer is not None

    with (
        patch.object(_app, "input_mode", "vox"),
        patch("voxium.app._start_vox_listening") as mock_start,
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
        assert result["ok"] is True
        mock_start.assert_not_called()
        # Timer is NOT restarted — same handle should survive
        assert _mod._vox_deadman_timer is first_timer


# ---------------------------------------------------------------------------
# Rate-limit: disarm while mode is vox is exempt
# ---------------------------------------------------------------------------


def test_disarm_exempt_from_rate_limit():
    """input_mode_set to ptt while vox is exempt from mutation bucket."""
    _disp.AgentDispatch._mutable_bucket = _disp.TokenBucket(
        capacity=0, refill_per_second=0
    )
    fp = FakeSessionPort(input_mode="vox")
    _HANDLER_MAP.update(_mod._build_read_handler_map())
    dispatch = AgentDispatch(fp, granted_scopes={"input:vox"})

    with (
        patch("voxium.app._stop_vox_listening"),
        patch("voxium.app._set_input_mode"),
        patch("voxium.app.show_status"),
        patch("voxium.app.ptt_status_box"),
    ):
        import voxium.app as _app

        _app.config = MagicMock()
        _app.config.minimal = True

        result = dispatch.dispatch("input_mode_set", {"mode": "ptt"})
        # disarm should NOT be rate-limited
        assert result.get("error", {}).get("code") != "rate_limited"
