"""In-memory transmission log for MCP agent reads.

Each recording/transcription cycle is a *transmission* with a monotonic id.
Records are kept in RAM and dropped on exit. ``/history clear`` also wipes this log.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RECORDS = 100


# ---------------------------------------------------------------------------
# TransmissionRecord
# ---------------------------------------------------------------------------


@dataclass
class TransmissionRecord:
    """Immutable snapshot of one transcription attempt."""

    # Assigned at begin()
    id: int
    state: str = "transcribing"  # transcribing | no_speech | error | completed
    # Assigned at finish()
    transcript: str | None = None
    filtered: bool = False  # short audio, no-speech, hallucination
    # Paste result (None = legacy paste_text path, agent path sets bool)
    paste_ok: bool | None = None
    # Machine code for paste failure (set only on agent path)
    paste_error: str | None = None
    # Timing
    duration_ms: int | None = None
    # Model metadata
    model: str | None = None


# ---------------------------------------------------------------------------
# TransmissionLog
# ---------------------------------------------------------------------------


class TransmissionLog:
    """
    Thread-safe, bounded in-memory log of transmission records.

    *max_records*: capacity cap. Oldest records are dropped when the cap is hit.
    """

    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._max_records = max_records
        self._records: dict[int, TransmissionRecord] = {}
        self._next_id: int = 1
        self._active_ids: set[int] = set()

    # ---- Public API ----

    def begin(self) -> int:
        """
        Start a new transmission record.

        Must be called *before* the transcription thread is started (matching
        the design doc requirement). Returns the new monotonic id.
        """
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            record = TransmissionRecord(id=tid, state="transcribing")
            self._records[tid] = record
            self._active_ids.add(tid)
            self._evict_if_needed()
            return tid

    def finish(
        self,
        tid: int,
        *,
        transcript: str | None = None,
        state: str = "completed",
        filtered: bool = False,
        paste_ok: bool | None = None,
        paste_error: str | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> bool:
        """
        Mark a transmission as finished.

        Returns ``True`` if the record was found and updated.
        """
        with self._lock:
            rec = self._records.get(tid)
            if rec is None:
                return False
            rec.state = state
            rec.transcript = transcript
            rec.filtered = filtered
            rec.paste_ok = paste_ok
            rec.paste_error = paste_error
            rec.duration_ms = duration_ms
            rec.model = model
            self._active_ids.discard(tid)
            return True

    def get(self, tid: int) -> TransmissionRecord | None:
        """Return a record by id, or ``None`` if not found."""
        with self._lock:
            return self._records.get(tid)

    def last(self) -> TransmissionRecord | None:
        """Return the most recently created record (any state)."""
        with self._lock:
            if not self._records:
                return None
            max_tid = max(self._records.keys())
            return self._records[max_tid]

    def latest_id(self) -> int | None:
        """Return the largest assigned id, or ``None`` if empty."""
        with self._lock:
            return self._next_id - 1 if self._records else None

    def has_active(self, tid: int) -> bool:
        """Return ``True`` if *tid* is still in ``transcribing`` state."""
        with self._lock:
            return tid in self._active_ids

    def has_any_active(self) -> bool:
        """Return ``True`` if any record is actively transcribing."""
        with self._lock:
            return bool(self._active_ids)

    def get_inflight(self) -> list[int]:
        """Return a sorted list of active (inflight) transmission ids."""
        with self._lock:
            return sorted(self._active_ids)

    def clear(self) -> None:
        """Wipe all records (called by ``/history clear``)."""
        with self._lock:
            self._records.clear()
            self._active_ids.clear()

    def wait(self, tid: int, timeout: float = 30.0) -> TransmissionRecord | None:
        """Poll for *tid* to finish (or not exist). Returns the record
        or ``None`` on timeout / not-found.

        This is a blocking sync poll; assume the MCP SDK runs sync tools
        on a worker thread so ``time.sleep`` does not block ``asyncio``.
        """
        deadline = time.monotonic() + min(timeout, 240.0)
        while True:
            rec = self.get(tid)
            if rec is None or rec.state != "transcribing":
                return rec
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.25)

    def record_count(self) -> int:
        """Return the number of stored records."""
        with self._lock:
            return len(self._records)

    # ---- Internal ----

    def _evict_if_needed(self) -> None:
        """
        Drop oldest records when the cap is exceeded.

        Evicted records are removed from ``_active_ids`` and the logic
        prefers dropping **finished** records before active ones (so the
        agent can still track ongoing transmissions).
        """
        while len(self._records) > self._max_records:
            # dict is insertion-ordered; keys are monotonic tids.
            # Prefer finished records (state != "transcribing") first.
            finished = [
                tid for tid, rec in self._records.items() if rec.state != "transcribing"
            ]
            if finished:
                to_drop = min(finished)  # oldest finished record
            else:
                to_drop = min(self._records.keys())  # fallback: oldest active
            del self._records[to_drop]
            self._active_ids.discard(to_drop)


# ---------------------------------------------------------------------------
# Standalone helpers (safe to call from finally blocks)
# ---------------------------------------------------------------------------


def finish_record(
    tlog: TransmissionLog | None,
    tid: int | None,
    *,
    transcript: str | None = None,
    state: str = "error",
) -> None:
    """
    Finish a transmission record, guarding against ``None`` *tlog* / *tid*.

    Does **not** catch exceptions — callers in ``finally`` blocks should wrap
    the invocation so a ``finish`` failure cannot suppress downstream logic
    (e.g. VOX re-queue).
    """
    if tlog is None or tid is None:
        return
    tlog.finish(tid, transcript=transcript, state=state)
