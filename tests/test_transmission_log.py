"""Tests for voxium.transmission_log (in-memory transmission records)."""

import threading

from voxium.transmission_log import (
    DEFAULT_MAX_RECORDS,
    TransmissionLog,
)

# ---------------------------------------------------------------------------
# Basic begin / finish cycles
# ---------------------------------------------------------------------------


def test_begin_returns_monotonic_ids():
    log = TransmissionLog()
    id1 = log.begin()
    id2 = log.begin()
    assert id1 < id2
    assert id1 >= 1


def test_finish_updates_state():
    log = TransmissionLog()
    tid = log.begin()
    assert log.has_active(tid)
    log.finish(tid, transcript="hello", state="completed")
    assert log.has_active(tid) is False
    rec = log.get(tid)
    assert rec is not None
    assert rec.state == "completed"
    assert rec.transcript == "hello"


def test_last_returns_most_recent():
    log = TransmissionLog()
    log.begin()
    id2 = log.begin()
    last = log.last()
    assert last is not None
    assert last.id == id2


def test_last_empty():
    log = TransmissionLog()
    assert log.last() is None


def test_latest_id():
    log = TransmissionLog()
    assert log.latest_id() is None
    log.begin()
    assert log.latest_id() == 1
    log.begin()
    assert log.latest_id() == 2


def test_finish_unknown_tid_returns_false():
    log = TransmissionLog()
    assert log.finish(9999, state="completed") is False


def test_record_count():
    log = TransmissionLog()
    assert log.record_count() == 0
    log.begin()
    log.begin()
    log.finish(1, state="no_speech")
    assert log.record_count() == 2


# ---------------------------------------------------------------------------
# Capacity cap / eviction
# ---------------------------------------------------------------------------


def test_capacity_evict_oldest():
    log = TransmissionLog(max_records=2)
    id1 = log.begin()
    log.begin()
    id3 = log.begin()
    assert log.record_count() == 2
    assert log.get(id1) is None  # evicted
    assert log.get(id3) is not None


def test_default_capacity():
    log = TransmissionLog()
    assert log._max_records == DEFAULT_MAX_RECORDS


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


def test_clear_wipes_all():
    log = TransmissionLog()
    log.begin()
    log.begin()
    log.finish(1, state="completed")
    log.clear()
    assert log.record_count() == 0
    assert log.last() is None
    assert log.has_active(1) is False
    assert log.has_any_active() is False


# ---------------------------------------------------------------------------
# has_any_active
# ---------------------------------------------------------------------------


def test_has_any_active():
    log = TransmissionLog()
    log.begin()
    assert log.has_any_active() is True
    log.finish(1, state="completed")
    assert log.has_any_active() is False


# ---------------------------------------------------------------------------
# State outcomes
# ---------------------------------------------------------------------------


def test_state_no_speech():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, state="no_speech")
    rec = log.get(tid)
    assert rec.state == "no_speech"


def test_state_error():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, state="error")
    rec = log.get(tid)
    assert rec.state == "error"


def test_filtered_flag():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, transcript="beep", filtered=True)
    rec = log.get(tid)
    assert rec.filtered is True


# ---------------------------------------------------------------------------
# Paste flags
# ---------------------------------------------------------------------------


def test_paste_ok_flag():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, paste_ok=True)
    rec = log.get(tid)
    assert rec.paste_ok is True


def test_paste_error_flag():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, paste_ok=False, paste_error="no_window")
    rec = log.get(tid)
    assert rec.paste_ok is False
    assert rec.paste_error == "no_window"


# ---------------------------------------------------------------------------
# Additional metadata
# ---------------------------------------------------------------------------


def test_duration_and_model():
    log = TransmissionLog()
    tid = log.begin()
    log.finish(tid, duration_ms=1234, model="medium.en")
    rec = log.get(tid)
    assert rec.duration_ms == 1234
    assert rec.model == "medium.en"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_thread_safety_concurrent_begins():
    log = TransmissionLog(max_records=200)
    ids = []
    lock = threading.Lock()

    def _begin():
        tid = log.begin()
        with lock:
            ids.append(tid)

    threads = [threading.Thread(target=_begin) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(ids) == 20
    assert len(set(ids)) == 20  # all unique
    assert log.record_count() == 20
