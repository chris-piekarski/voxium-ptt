"""Background poll of the Whisper server's ``/inference-health`` endpoint.

The whisper server runs as a separate uvicorn process. To surface its
``"whisper"`` health in the operator HUD (which lives in the main app
process), this poller fetches ``GET /inference-health``, parses the
``whisper`` snapshot, and copies it into the local registry via
:meth:`InferenceHealth.replace_from`. Polish health lives directly in
the main app process; this poller does not touch it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import requests

from voxium.inference_health import (
    InferenceHealthSnapshot,
    get_health,
)

DEFAULT_POLL_INTERVAL_S = 3.0
DEFAULT_REQUEST_TIMEOUT_S = 1.5
# After this many consecutive failed polls, mark the local whisper entry as
# unreachable so the HUD stops showing a stale green dot for a dead server.
UNREACHABLE_FAILURE_THRESHOLD = 2


def fetch_whisper_inference_health(
    server_url: str,
    *,
    requests_get: Callable[..., Any] = requests.get,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
) -> InferenceHealthSnapshot | None:
    """One-shot fetch. Returns the ``whisper`` snapshot or ``None`` on failure."""
    base = (server_url or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/inference-health"
    try:
        resp = requests_get(url, timeout=timeout)
    except requests.RequestException:
        return None
    except (OSError, ValueError):
        return None
    if getattr(resp, "status_code", 0) != 200:
        return None
    try:
        payload = resp.json()
    except (ValueError, TypeError):
        return None
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
    if not isinstance(snapshots, list):
        return None
    for raw in snapshots:
        if not isinstance(raw, dict):
            continue
        if (raw.get("server") or "") != "whisper":
            continue
        return InferenceHealthSnapshot.from_dict(raw)
    return None


def poll_and_apply_whisper_health(
    server_url: str,
    *,
    requests_get: Callable[..., Any] = requests.get,
) -> bool:
    """Fetch and merge one cycle. Returns ``True`` on successful refresh."""
    snap = fetch_whisper_inference_health(server_url, requests_get=requests_get)
    if snap is None:
        return False
    get_health("whisper").replace_from(snap)
    return True


class WhisperHealthPoller:
    """Daemon thread that keeps the local ``whisper`` registry entry fresh.

    Consecutive failed polls eventually flip the local entry to degraded/failed
    via :meth:`InferenceHealth.record_error` so the HUD surfaces an unreachable
    whisper server instead of a stale "ok" indicator.
    """

    def __init__(
        self,
        server_url: str,
        *,
        interval: float = DEFAULT_POLL_INTERVAL_S,
        requests_get: Callable[..., Any] = requests.get,
        unreachable_threshold: int = UNREACHABLE_FAILURE_THRESHOLD,
    ) -> None:
        self._server_url = server_url
        self._interval = max(0.5, float(interval))
        self._requests_get = requests_get
        self._unreachable_threshold = max(1, int(unreachable_threshold))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_failures = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._consecutive_failures = 0
        self._thread = threading.Thread(
            target=self._run,
            name="voxium-whisper-health-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def tick_once(self) -> bool:
        """One synchronous poll. Returns True if the local registry was refreshed.

        Exposed for tests and for the optional in-thread tick during HUD render.
        """
        ok = poll_and_apply_whisper_health(
            self._server_url, requests_get=self._requests_get
        )
        if ok:
            self._consecutive_failures = 0
            return True
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._unreachable_threshold:
            get_health("whisper").record_error(
                f"whisper server unreachable at {self._server_url}"
            )
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:  # pragma: no cover - never let the thread die
                pass
            self._stop.wait(self._interval)
