"""Tests for the InferenceHealth tri-state classifier + process-local registry."""

from __future__ import annotations

import threading

import pytest

import voxium.inference_health as ih


@pytest.fixture(autouse=True)
def _reset_registry():
    ih.reset_for_tests()
    yield
    ih.reset_for_tests()


def test_initial_state_is_unknown_without_any_signal() -> None:
    h = ih.get_health("whisper")
    snap = h.snapshot()
    assert snap.state == ih.STATE_UNKNOWN
    assert snap.last_ok_at is None
    assert snap.last_error_at is None
    assert snap.last_error_msg is None
    assert snap.consecutive_failures == 0


def test_record_ok_transitions_to_ok() -> None:
    h = ih.get_health("whisper")
    h.record_ok()
    assert h.snapshot().state == ih.STATE_OK


def test_single_error_is_degraded() -> None:
    h = ih.get_health("polish")
    h.record_error("boom")
    snap = h.snapshot()
    assert snap.state == ih.STATE_DEGRADED
    assert snap.consecutive_failures == 1
    assert snap.last_error_msg == "boom"


def test_repeated_errors_escalate_to_failed() -> None:
    h = ih.get_health("polish")
    for _ in range(ih.HEALTH_FAILURE_THRESHOLD):
        h.record_error("crash")
    snap = h.snapshot()
    assert snap.state == ih.STATE_FAILED
    assert snap.consecutive_failures >= ih.HEALTH_FAILURE_THRESHOLD


def test_recovery_after_failure_drops_to_degraded_briefly() -> None:
    h = ih.get_health("polish")
    h.record_error("e1")
    h.record_error("e2")
    h.record_ok()
    snap = h.snapshot()
    # Just recovered — classifier shows degraded as a "still warm" indicator.
    assert snap.state == ih.STATE_DEGRADED
    assert snap.consecutive_failures == 0


def test_recovery_after_failure_returns_to_ok_once_stale() -> None:
    h = ih.get_health("polish")
    h.record_error("e1")
    h.record_ok()
    # Force the error to be far in the past relative to "now".
    snap = h.snapshot(
        now=h._last_ok_at + ih.HEALTH_STALE_AFTER_RECOVERY_SECONDS + 1
    )  # pylint: disable=protected-access
    assert snap.state == ih.STATE_OK


def test_record_error_accepts_exception() -> None:
    h = ih.get_health("whisper")
    try:
        raise ValueError("bad input")
    except ValueError as exc:
        h.record_error(exc)
    snap = h.snapshot()
    assert snap.state == ih.STATE_DEGRADED
    assert snap.last_error_msg is not None
    assert "ValueError" in snap.last_error_msg
    assert "bad input" in snap.last_error_msg


def test_record_error_truncates_long_message() -> None:
    h = ih.get_health("whisper")
    h.record_error("x" * 1000)
    snap = h.snapshot()
    assert snap.last_error_msg is not None
    assert (
        len(snap.last_error_msg) <= ih._ERROR_MESSAGE_MAX_LEN
    )  # pylint: disable=protected-access


def test_snapshot_serializes_to_and_from_dict() -> None:
    h = ih.get_health("polish")
    h.record_ok()
    h.record_error("oops")
    snap = h.snapshot()
    payload = snap.as_dict()
    assert payload["server"] == "polish"
    assert payload["state"] == ih.STATE_DEGRADED
    assert payload["consecutive_failures"] == 1
    round_trip = ih.InferenceHealthSnapshot.from_dict(payload)
    assert round_trip == snap


def test_snapshot_from_dict_is_tolerant_of_missing_keys() -> None:
    snap = ih.InferenceHealthSnapshot.from_dict({"server": "x"})
    assert snap.server == "x"
    assert snap.state == ih.STATE_UNKNOWN
    assert snap.last_ok_at is None


def test_registry_returns_same_instance_per_key() -> None:
    a = ih.get_health("whisper")
    b = ih.get_health("whisper")
    assert a is b


def test_registry_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        ih.get_health("   ")


def test_all_snapshots_includes_every_registered_server() -> None:
    ih.get_health("whisper").record_ok()
    ih.get_health("polish").record_error("oh no")
    names = {s.server for s in ih.all_snapshots()}
    assert names == {"whisper", "polish"}


def test_replace_from_overwrites_local_state() -> None:
    h = ih.get_health("whisper")
    h.record_error("local err")
    incoming = ih.InferenceHealthSnapshot(
        server="whisper",
        state=ih.STATE_OK,
        last_ok_at=1234.0,
        last_error_at=None,
        last_error_msg=None,
        consecutive_failures=0,
    )
    h.replace_from(incoming)
    snap = h.snapshot()
    assert snap.consecutive_failures == 0
    assert snap.last_ok_at == 1234.0
    assert snap.last_error_at is None


def test_concurrent_record_calls_are_thread_safe() -> None:
    h = ih.get_health("whisper")

    def writer_ok() -> None:
        for _ in range(200):
            h.record_ok()

    def writer_err() -> None:
        for _ in range(200):
            h.record_error("e")

    threads = [
        threading.Thread(target=writer_ok),
        threading.Thread(target=writer_err),
        threading.Thread(target=writer_ok),
        threading.Thread(target=writer_err),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    snap = h.snapshot()
    assert snap.state in {ih.STATE_OK, ih.STATE_DEGRADED, ih.STATE_FAILED}
