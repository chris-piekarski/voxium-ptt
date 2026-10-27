"""Inference-server health tracking surfaced to the operator HUD.

Each inference server (Whisper STT, llama.cpp polish, …) owns one
:class:`InferenceHealth` instance. Callers post outcomes via
:meth:`record_ok` / :meth:`record_error`; the tri-state classifier in
:meth:`InferenceHealth.snapshot` turns those into ``ok`` / ``degraded`` /
``failed`` / ``unknown`` for the HUD indicator.

The registry is *process-local*. The Whisper server (separate uvicorn
process) tracks its own "whisper" entry and exposes a snapshot over
HTTP; the main app keeps a "polish" entry directly and replaces its
"whisper" entry from the polled snapshot. See
``voxium.inference_health_client``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

# Tri-state classifier thresholds.
HEALTH_STALE_AFTER_RECOVERY_SECONDS = 90.0
HEALTH_FAILURE_THRESHOLD = 2
_ERROR_MESSAGE_MAX_LEN = 200


# Indicator state values returned by snapshot().state.
STATE_OK = "ok"
STATE_DEGRADED = "degraded"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class InferenceHealthSnapshot:
    """Immutable view of one server's health at the moment of capture."""

    server: str
    state: str
    last_ok_at: float | None
    last_error_at: float | None
    last_error_msg: str | None
    consecutive_failures: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InferenceHealthSnapshot":
        return cls(
            server=str(payload.get("server") or ""),
            state=str(payload.get("state") or STATE_UNKNOWN),
            last_ok_at=_optional_float(payload.get("last_ok_at")),
            last_error_at=_optional_float(payload.get("last_error_at")),
            last_error_msg=_optional_str(payload.get("last_error_msg")),
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truncate(msg: str, limit: int) -> str:
    if len(msg) <= limit:
        return msg
    return msg[: max(1, limit - 1)].rstrip() + "…"


class InferenceHealth:
    """Thread-safe health tracker for a single inference server."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._last_ok_at: float | None = None
        self._last_error_at: float | None = None
        self._last_error_msg: str | None = None
        self._consecutive_failures: int = 0

    def record_ok(self) -> None:
        """Note a successful inference (or healthy probe)."""
        with self._lock:
            self._last_ok_at = time.time()
            self._consecutive_failures = 0

    def record_error(self, msg: str | BaseException) -> None:
        """Note a failed inference. ``msg`` may be a string or an exception."""
        if isinstance(msg, BaseException):
            rendered = f"{type(msg).__name__}: {msg}".strip()
        else:
            rendered = str(msg).strip()
        rendered = _truncate(rendered, _ERROR_MESSAGE_MAX_LEN)
        with self._lock:
            self._last_error_at = time.time()
            self._last_error_msg = rendered or self._last_error_msg
            self._consecutive_failures += 1

    def snapshot(self, *, now: float | None = None) -> InferenceHealthSnapshot:
        ts = now if now is not None else time.time()
        with self._lock:
            return InferenceHealthSnapshot(
                server=self.name,
                state=self._classify_unsafe(ts),
                last_ok_at=self._last_ok_at,
                last_error_at=self._last_error_at,
                last_error_msg=self._last_error_msg,
                consecutive_failures=self._consecutive_failures,
            )

    def replace_from(self, snap: InferenceHealthSnapshot) -> None:
        """Overwrite local state from a remote snapshot (used by the whisper poller)."""
        with self._lock:
            self._last_ok_at = snap.last_ok_at
            self._last_error_at = snap.last_error_at
            self._last_error_msg = snap.last_error_msg
            self._consecutive_failures = snap.consecutive_failures

    def _classify_unsafe(self, now: float) -> str:
        if self._last_ok_at is None and self._last_error_at is None:
            return STATE_UNKNOWN
        # Repeated failures dominate everything else.
        if self._consecutive_failures >= HEALTH_FAILURE_THRESHOLD:
            return STATE_FAILED
        # Last event was an error and we have not seen a fresher success.
        if self._last_error_at is not None and (
            self._last_ok_at is None or self._last_error_at > self._last_ok_at
        ):
            return (
                STATE_DEGRADED
                if self._consecutive_failures < HEALTH_FAILURE_THRESHOLD
                else STATE_FAILED
            )
        # We have a success that is fresher than the last error. If the error was
        # very recent (relative to the success) treat it as still-warm degraded.
        if (
            self._last_error_at is not None
            and self._last_ok_at is not None
            and (now - self._last_error_at) <= HEALTH_STALE_AFTER_RECOVERY_SECONDS
        ):
            return STATE_DEGRADED
        return STATE_OK


class HealthRegistry:
    """Process-local map: server name → :class:`InferenceHealth`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tracked: dict[str, InferenceHealth] = {}

    def get(self, name: str) -> InferenceHealth:
        key = name.strip()
        if not key:
            raise ValueError("inference health name must be non-empty")
        with self._lock:
            existing = self._tracked.get(key)
            if existing is None:
                existing = InferenceHealth(key)
                self._tracked[key] = existing
            return existing

    def snapshots(self) -> list[InferenceHealthSnapshot]:
        with self._lock:
            trackers = list(self._tracked.values())
        return [tracker.snapshot() for tracker in trackers]

    def reset_for_tests(self) -> None:
        with self._lock:
            self._tracked.clear()


_REGISTRY = HealthRegistry()


def get_health(name: str) -> InferenceHealth:
    """Module-level accessor — both Whisper and the polish daemon use this."""
    return _REGISTRY.get(name)


def all_snapshots() -> list[InferenceHealthSnapshot]:
    """All currently tracked server snapshots (for HUD / /inference-health endpoint)."""
    return _REGISTRY.snapshots()


def reset_for_tests() -> None:
    """Wipe the process-local registry. Tests only."""
    _REGISTRY.reset_for_tests()
