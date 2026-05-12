"""
Client-side WebSocket plumbing for ``/transcribe-stream`` live partials.

This module owns one streaming session per PTT take:

- :class:`StreamingSessionRuntime` holds the live :class:`websocket.WebSocket`,
  a bounded chunk queue, sender + receiver threads, and the thread-safe
  :class:`LiveState` the green PTT strip renders from.
- :func:`start_session` opens the WS and spawns the threads. Returns ``None`` when
  ``websocket-client`` is missing, the URL is unreachable, or the server's ``/health``
  reports streaming disabled — callers fall back silently to the no-live-text path.
- :func:`push_audio_frame` is called from the sounddevice audio callback. It MUST
  never block; uses :meth:`queue.Queue.put_nowait` with drop-on-full backpressure.
- :func:`end_session` sends ``{"type": "end"}``, drains the final ``partial``, and
  closes cleanly. :func:`close_session` skips the flush for ungraceful shutdowns.

The receiver thread parses ``partial`` / ``error`` / ``keepalive`` frames and updates
:class:`LiveState`; the Rich :class:`PttSessionStatusBox` reads it under the same lock.

Polish + paste at end-of-take are completely unaffected — the existing batch
``/transcribe`` POST still runs in :func:`voxium.app.transcribe_and_paste`.
See ``docs/plans/live-transcribe-stream.md`` §3.4 for the full lifecycle table.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import numpy as np

from voxium.constants import (
    SAMPLE_RATE,
    STREAMING_CHUNK_MS_DEFAULT,
    STREAMING_COMMIT_THRESHOLD_RATIO,
    STREAMING_COMMITTED_MAX_CHARS_DEFAULT,
    STREAMING_CONNECT_TIMEOUT_S_DEFAULT,
    STREAMING_FALLBACK_DECODE_RATIO_DEFAULT,
    STREAMING_FALLBACK_DROP_THRESHOLD_DEFAULT,
    STREAMING_MAX_QUEUE_FRAMES_DEFAULT,
)

_LOG = logging.getLogger(__name__)

# Imported lazily so missing websocket-client doesn't break ``voxium run`` for users
# who never enable streaming. See :func:`start_session`. Promoted from a dev-only
# extra to a runtime dependency in Phase 2 — see pyproject.toml.
_WEBSOCKET_IMPORT_TRIED = False
_WEBSOCKET: Any = None
_WEBSOCKET_EXCEPTION_CLS: type[Exception] = Exception
_WEBSOCKET_IMPORT_MISSING_LOGGED = False


def _ensure_websocket_import() -> bool:
    """Lazy-import ``websocket-client``. Returns True if available."""
    global _WEBSOCKET_IMPORT_TRIED, _WEBSOCKET, _WEBSOCKET_EXCEPTION_CLS
    global _WEBSOCKET_IMPORT_MISSING_LOGGED
    if _WEBSOCKET is not None:
        return True
    if _WEBSOCKET_IMPORT_TRIED:
        return False
    _WEBSOCKET_IMPORT_TRIED = True
    try:
        import websocket as _ws_module
    except ImportError:
        if not _WEBSOCKET_IMPORT_MISSING_LOGGED:
            _LOG.warning(
                "Voxium: websocket-client not installed — live transcribe streaming is "
                "disabled this session. Install with `pip install -e .` (or "
                "`pip install websocket-client>=1.7.0`) and restart voxium run."
            )
            _WEBSOCKET_IMPORT_MISSING_LOGGED = True
        return False
    _WEBSOCKET = _ws_module
    exc_cls = getattr(_ws_module, "WebSocketException", Exception)
    if isinstance(exc_cls, type) and issubclass(exc_cls, Exception):
        _WEBSOCKET_EXCEPTION_CLS = exc_cls
    return True


def websocket_dependency_available() -> bool:
    """
    Probe whether ``websocket-client`` is importable, without side effects beyond
    the lazy-import cache. Used by :mod:`voxium.app` to surface a one-shot operator
    note when streaming was opted into but the dep is missing.
    """
    return _ensure_websocket_import()


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LiveState:
    """
    Snapshot the green-strip renderer reads to draw the live readback line.

    Fields are intentionally simple; the renderer compares to the last drawn state
    and only redraws when ``text`` or ``status`` changes.

    The split between :attr:`committed_text` and :attr:`text` exists because the
    server uses a sliding window: once the window starts sliding (audio >= 95% of
    ``window_seconds``), the words at the front of the previous partial fall off
    the buffer and the server can no longer revise them. We promote them into
    ``committed_text`` so the operator sees a *growing* transcript, not just the
    last 5 s of decoded speech. ``text`` stays as the volatile current-window
    decode that may revise on each partial.
    """

    active: bool = False  # WS open and at least one partial received
    connected: bool = False  # last network event was OK
    committed_text: str = ""  # accumulating prefix that has slid off the server window
    text: str = ""  # full window text from the most recent partial (volatile)
    last_seq: int = 0
    last_partial_received_at: float | None = None
    error_code: str | None = None  # last error code, e.g. "max_sessions"
    fallback: bool = False  # auto-fallback tripped this session
    frames_sent: int = 0
    frames_dropped: int = 0
    n_partials: int = 0
    last_decode_ms: float | None = None


@dataclass
class _SessionStats:
    """Internal counters per session, surfaced into LiveState + Phase 2 /profile lane."""

    started_monotonic: float = 0.0
    first_partial_ms: float | None = None
    n_decodes: int = 0
    total_decode_ms: float = 0.0
    max_decode_ms: float = 0.0
    frames_sent: int = 0
    frames_dropped: int = 0
    hallucinations_suppressed: int = 0
    fallback: bool = False
    error: str | None = None
    audio_seconds_last: float = 0.0
    model: str = ""
    # Streaming accumulator state — see :func:`_merge_partial`. The committed
    # buffer is the rolling transcript prefix that has slid off the server's
    # decode window and can no longer be revised. ``last_partial_text`` is the
    # text of the previous partial, used for word-level diff against the new
    # partial when the window is full.
    window_seconds: float = 5.0
    last_partial_text: str = ""
    committed_buffer: str = ""


class _ChunkAggregator:
    """
    Buffer raw float32 samples from the audio callback and emit uniform
    ``chunk_samples``-sized chunks.

    Voxium's sounddevice callback fires every ~16–64 ms with whatever blocksize
    PortAudio picked. Without aggregation, we'd push 15–60 frames/sec to the
    server; the server's 5 s sliding-window decoder would run a full re-decode
    for each one, falling behind on anything but a top-end GPU. Aggregating into
    250 ms chunks (4000 samples @ 16 kHz) gives the planned ~4 re-decodes/sec
    and lets the server stay caught up.

    Threading: instances are touched only from the audio callback thread (single
    writer) and from :func:`end_session` after capture has stopped, so no lock
    is required.
    """

    __slots__ = ("_chunk_samples", "_residue")

    def __init__(self, chunk_samples: int) -> None:
        self._chunk_samples = max(1, int(chunk_samples))
        self._residue: np.ndarray = np.empty(0, dtype=np.float32)

    @property
    def residue_size(self) -> int:
        return int(self._residue.size)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        """
        Append ``samples`` (float32 mono) and return any complete chunks ready to
        send. The returned chunks are independent copies so the caller can hand
        them to a different thread without aliasing the residue.
        """
        if samples.size == 0:
            return []
        # Unify dtype + ensure contiguous so .tobytes is cheap downstream.
        appended = np.ascontiguousarray(samples, dtype=np.float32).ravel()
        if self._residue.size:
            buf = np.concatenate((self._residue, appended))
        else:
            buf = appended
        out: list[np.ndarray] = []
        offset = 0
        while buf.size - offset >= self._chunk_samples:
            out.append(buf[offset : offset + self._chunk_samples].copy())
            offset += self._chunk_samples
        if offset < buf.size:
            self._residue = buf[offset:].copy()
        else:
            self._residue = np.empty(0, dtype=np.float32)
        return out

    def drain(self) -> np.ndarray | None:
        """
        Return whatever residue is in the buffer (any size below ``chunk_samples``).
        Used at end-of-session to flush a partial trailing chunk before close.
        """
        if self._residue.size == 0:
            return None
        out = self._residue
        self._residue = np.empty(0, dtype=np.float32)
        return out


@dataclass
class StreamingSessionRuntime:
    """
    One PTT take's streaming session. Created by :func:`start_session`.

    Callers should not touch the fields directly; use the module-level helpers below.
    """

    ws: Any  # websocket.WebSocket
    url: str
    chunk_ms: int
    max_queue_frames: int
    fallback_drop_threshold: int
    fallback_decode_ratio: float
    sender_thread: threading.Thread | None = None
    receiver_thread: threading.Thread | None = None
    queue: queue.Queue = field(default_factory=queue.Queue)
    state: LiveState = field(default_factory=LiveState)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    stats: _SessionStats = field(default_factory=_SessionStats)
    aggregator: _ChunkAggregator = field(
        default_factory=lambda: _ChunkAggregator(SAMPLE_RATE // 4)
    )
    _stop: threading.Event = field(default_factory=threading.Event)
    _send_done: threading.Event = field(default_factory=threading.Event)
    _final_received: threading.Event = field(default_factory=threading.Event)


# ---------------------------------------------------------------------------
# URL plumbing
# ---------------------------------------------------------------------------


def derive_stream_ws_url(server_url: str) -> str:
    """
    Convert a ``/transcribe`` HTTP URL into the matching ``/transcribe-stream`` WS URL.

    Honors :func:`voxium.loopback.normalize_loopback_url` semantics: callers should
    have already normalized ``server_url`` to the IPv4 form. We just swap scheme +
    path here.
    """
    parsed = urlparse(server_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    rebuilt = parsed._replace(scheme=scheme, path="/transcribe-stream")
    return urlunparse(rebuilt)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def start_session(
    server_url: str,
    *,
    chunk_ms: int = STREAMING_CHUNK_MS_DEFAULT,
    max_queue_frames: int = STREAMING_MAX_QUEUE_FRAMES_DEFAULT,
    fallback_drop_threshold: int = STREAMING_FALLBACK_DROP_THRESHOLD_DEFAULT,
    fallback_decode_ratio: float = STREAMING_FALLBACK_DECODE_RATIO_DEFAULT,
    connect_timeout_s: float = STREAMING_CONNECT_TIMEOUT_S_DEFAULT,
) -> StreamingSessionRuntime | None:
    """
    Open the WS, read ``session_open``, spawn sender + receiver threads.

    Returns the runtime on success; ``None`` when streaming is unavailable for any
    reason (library missing, connect refused, malformed handshake). Callers should
    treat None as "no live readback this take" and continue normally.
    """
    if not _ensure_websocket_import():
        return None
    url = derive_stream_ws_url(server_url)
    try:
        ws = _WEBSOCKET.create_connection(url, timeout=connect_timeout_s)
    except (_WEBSOCKET_EXCEPTION_CLS, OSError) as exc:
        _LOG.debug("transcribe_stream_client: connect failed (%s): %s", url, exc)
        return None

    # Read the session_open frame so we can fail fast if the server is mismatched.
    try:
        ws.settimeout(connect_timeout_s)
        opened_raw = ws.recv()
    except (_WEBSOCKET_EXCEPTION_CLS, OSError) as exc:
        _LOG.debug("transcribe_stream_client: session_open read failed: %s", exc)
        try:
            ws.close()
        except Exception:
            pass
        return None
    try:
        opened = json.loads(opened_raw) if isinstance(opened_raw, str) else None
    except json.JSONDecodeError:
        opened = None
    if not isinstance(opened, dict) or opened.get("type") != "session_open":
        _LOG.debug("transcribe_stream_client: bad session_open: %r", opened)
        try:
            ws.close()
        except Exception:
            pass
        return None

    runtime = StreamingSessionRuntime(
        ws=ws,
        url=url,
        chunk_ms=int(chunk_ms),
        max_queue_frames=int(max_queue_frames),
        fallback_drop_threshold=int(fallback_drop_threshold),
        fallback_decode_ratio=float(fallback_decode_ratio),
        queue=queue.Queue(maxsize=int(max_queue_frames)),
        aggregator=_ChunkAggregator(chunk_samples_for_ms(int(chunk_ms))),
    )
    runtime.stats.started_monotonic = time.monotonic()
    runtime.stats.model = str(opened.get("model") or "")
    try:
        window_seconds = float(opened.get("window_seconds") or 0.0)
    except (TypeError, ValueError):
        window_seconds = 0.0
    if window_seconds > 0:
        runtime.stats.window_seconds = window_seconds

    # Spawn threads
    runtime.sender_thread = threading.Thread(
        target=_sender_loop, args=(runtime,), daemon=True, name="VoxiumStreamSender"
    )
    runtime.receiver_thread = threading.Thread(
        target=_receiver_loop, args=(runtime,), daemon=True, name="VoxiumStreamReceiver"
    )
    runtime.sender_thread.start()
    runtime.receiver_thread.start()
    return runtime


def push_audio_frame(
    runtime: StreamingSessionRuntime | None, pcm_float32: np.ndarray
) -> None:
    """
    Append samples and emit any complete chunk-sized blocks onto the sender queue.

    Real-time-safe (never blocks). Called from the sounddevice audio callback with
    whatever blocksize PortAudio picked (typically 256–1024 samples). The internal
    aggregator coalesces those into uniform 250 ms chunks before they hit the queue,
    matching the cadence the server is designed for. On full queue, the chunk is
    dropped and a counter is incremented; auto-fallback may trip if drops exceed
    ``fallback_drop_threshold``.
    """
    if runtime is None or runtime._stop.is_set():
        return
    if pcm_float32 is None or pcm_float32.size == 0:
        return
    try:
        chunks = runtime.aggregator.push(pcm_float32)
    except Exception:  # pragma: no cover - defensive
        return
    if not chunks:
        return
    for chunk in chunks:
        try:
            frame_bytes = chunk.tobytes()
        except Exception:  # pragma: no cover - defensive
            continue
        try:
            runtime.queue.put_nowait(frame_bytes)
        except queue.Full:
            with runtime.state_lock:
                runtime.stats.frames_dropped += 1
                runtime.state.frames_dropped = runtime.stats.frames_dropped
            _maybe_trip_fallback(runtime)


def end_session(
    runtime: StreamingSessionRuntime | None, *, drain_timeout_s: float = 0.6
) -> None:
    """
    Graceful close: flush any aggregator residue as a final partial chunk, drain
    the send queue, send ``end``, wait briefly for the final ``partial``, then
    close the WS and join threads.
    """
    if runtime is None:
        return
    # Flush any residue audio (less than one full chunk) so the server's last
    # decode includes everything captured. Safe because the audio callback has
    # already stopped before end_session is called.
    try:
        residue = runtime.aggregator.drain()
        if residue is not None and residue.size > 0:
            try:
                runtime.queue.put_nowait(residue.tobytes())
            except queue.Full:
                pass  # Residue too late; final partial decodes whatever made it.
    except Exception:  # pragma: no cover - defensive
        pass
    runtime._stop.set()
    # Wait on the sender's done-event rather than thread.join — the event is set
    # in the sender's finally block, so when it fires we *know* there are no
    # in-flight ws.send_binary calls. websocket-client's send methods are NOT
    # thread-safe; if we let the sender thread keep running while we call
    # ws.send(end), the binary and text frames can interleave and corrupt the
    # protocol. join(timeout) doesn't give us this guarantee — the thread may
    # still be mid-send when the join times out.
    runtime._send_done.wait(timeout=drain_timeout_s)
    try:
        runtime.ws.send(json.dumps({"type": "end"}))
    except Exception:
        pass
    runtime._final_received.wait(timeout=drain_timeout_s)
    _shutdown(runtime)


def close_session(runtime: StreamingSessionRuntime | None) -> None:
    """Ungraceful close — used on Ctrl+C, mic disconnect, errors."""
    if runtime is None:
        return
    runtime._stop.set()
    _shutdown(runtime)


def is_active(runtime: StreamingSessionRuntime | None) -> bool:
    return runtime is not None and not runtime._stop.is_set()


def get_live_state(runtime: StreamingSessionRuntime | None) -> LiveState | None:
    """Return a frozen copy of the current LiveState for renderers."""
    if runtime is None:
        return None
    with runtime.state_lock:
        return LiveState(
            active=runtime.state.active,
            connected=runtime.state.connected,
            committed_text=runtime.state.committed_text,
            text=runtime.state.text,
            last_seq=runtime.state.last_seq,
            last_partial_received_at=runtime.state.last_partial_received_at,
            error_code=runtime.state.error_code,
            fallback=runtime.state.fallback,
            frames_sent=runtime.state.frames_sent,
            frames_dropped=runtime.state.frames_dropped,
            n_partials=runtime.state.n_partials,
            last_decode_ms=runtime.state.last_decode_ms,
        )


def get_session_stats(runtime: StreamingSessionRuntime | None) -> dict | None:
    """Return per-session counters for /profile aggregation."""
    if runtime is None:
        return None
    with runtime.state_lock:
        s = runtime.stats
        elapsed = max(0.0, time.monotonic() - s.started_monotonic)
        return {
            "n_decodes": s.n_decodes,
            "total_decode_ms": round(s.total_decode_ms, 2),
            "avg_decode_ms": (
                round(s.total_decode_ms / s.n_decodes, 2) if s.n_decodes else None
            ),
            "max_decode_ms": round(s.max_decode_ms, 2),
            "frames_sent": s.frames_sent,
            "frames_dropped": s.frames_dropped,
            "first_partial_ms": s.first_partial_ms,
            "fallback": s.fallback,
            "session_seconds": round(elapsed, 3),
            "audio_seconds": s.audio_seconds_last,
            "model": s.model,
            "error": s.error,
            "ok": s.error is None,
        }


# ---------------------------------------------------------------------------
# Internal threads
# ---------------------------------------------------------------------------


def _sender_loop(runtime: StreamingSessionRuntime) -> None:
    """Drain the queue and write binary frames until ``_stop`` is set."""
    chunk_period = max(0.01, runtime.chunk_ms / 1000.0)
    try:
        while not runtime._stop.is_set():
            try:
                frame = runtime.queue.get(timeout=chunk_period)
            except queue.Empty:
                continue
            try:
                runtime.ws.send_binary(frame)
            except Exception as exc:  # noqa: BLE001 - any send failure ends the session
                _LOG.debug("transcribe_stream_client: send failed: %s", exc)
                with runtime.state_lock:
                    runtime.state.connected = False
                    runtime.state.error_code = "send_failed"
                    runtime.stats.error = "send_failed"
                runtime._stop.set()
                break
            with runtime.state_lock:
                runtime.stats.frames_sent += 1
                runtime.state.frames_sent = runtime.stats.frames_sent
    finally:
        runtime._send_done.set()


def _receiver_loop(runtime: StreamingSessionRuntime) -> None:
    """Read JSON frames and update LiveState until the WS closes."""
    try:
        while not runtime._stop.is_set() or not runtime._final_received.is_set():
            try:
                runtime.ws.settimeout(0.5)
                raw = runtime.ws.recv()
            except OSError:
                break
            except _WEBSOCKET_EXCEPTION_CLS as exc:
                # Timeout → loop. Other WS exceptions → bail.
                msg = str(exc).lower()
                if "timed out" in msg:
                    # Don't break on _stop alone: end_session sets _stop BEFORE
                    # sending {"type":"end"}, so a timeout in that gap would exit
                    # the receiver before the server's final partial could arrive.
                    # Only break once the outer condition (stopped AND final
                    # received) is true; otherwise let _shutdown's ws.close() be
                    # the signal to exit via OSError/empty-recv.
                    if runtime._stop.is_set() and runtime._final_received.is_set():
                        break
                    continue
                _LOG.debug("transcribe_stream_client: recv exc: %s", exc)
                break
            if not raw:
                break
            if isinstance(raw, bytes):
                # Server doesn't send binary in v1; ignore.
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = payload.get("type")
            if kind == "partial":
                _ingest_partial(runtime, payload)
                if payload.get("is_final"):
                    runtime._final_received.set()
                    break
            elif kind == "error":
                with runtime.state_lock:
                    runtime.state.error_code = str(payload.get("code") or "error")
                    runtime.stats.error = str(payload.get("code") or "error")
                runtime._stop.set()
                break
            elif kind == "keepalive":
                with runtime.state_lock:
                    runtime.state.connected = True
            # Ignore unknown frames — be lenient on the receive side.
    finally:
        runtime._final_received.set()


_PUNCT_STRIP = ".,!?;:'\"()[]{}—–-"


def _norm_word(w: str) -> str:
    """Lowercase + strip surrounding punctuation for stable suffix-prefix comparison."""
    return w.strip(_PUNCT_STRIP).lower()


def _merge_partial(prev: str, new: str) -> tuple[str, str]:
    """
    Word-level diff between consecutive sliding-window partials.

    Returns ``(committed_delta, tail)`` where:

    - ``committed_delta`` is the text that has slid off the front of the server's
      decode window since ``prev`` and should be appended to the rolling committed
      buffer (with a trailing space when non-empty so concatenation is safe).
    - ``tail`` is the volatile current-window text the renderer should show after
      the committed prefix.

    Algorithm: find the longest k such that ``prev``'s last k words equal ``new``'s
    first k words (case- and punctuation-insensitive — Whisper drifts on those).
    Words before that overlap in ``prev`` are committed.

    Three outcomes:

    - ``k == len(prev_words)`` (full overlap): window hasn't slid past prev yet,
      commit nothing, just refresh the tail.
    - ``0 < k < len(prev_words)`` (partial overlap): the leading ``len(prev) - k``
      words slid off the back of the server's decode window. Commit them.
    - no ``k > 0`` matches (revision / word-boundary change): commit nothing.
      Prev's leading words may genuinely have slid off, but we can't tell them
      apart from a Whisper revision, and committing prev would duplicate any
      words that DID survive into ``new``. The next partial that does overlap
      cleanly will recover. Under-commit > duplicate.
    """
    if not prev or not new:
        return "", new
    p_words = prev.split()
    n_words = new.split()
    if not p_words or not n_words:
        return "", new
    p_norm = [_norm_word(w) for w in p_words]
    n_norm = [_norm_word(w) for w in n_words]
    max_k = min(len(p_words), len(n_words))
    for k in range(max_k, 0, -1):
        if p_norm[-k:] == n_norm[:k]:
            if k == len(p_words):
                return "", new
            committed = " ".join(p_words[:-k])
            return committed + " ", new
    return "", new


def _trim_committed(committed: str, max_chars: int) -> str:
    """Cap the committed buffer at ``max_chars``, trimming whole words from the front."""
    if max_chars <= 0 or len(committed) <= max_chars:
        return committed
    cut = len(committed) - max_chars
    space_idx = committed.find(" ", cut)
    if space_idx == -1:
        return committed[-max_chars:]
    return committed[space_idx + 1 :]


def _ingest_partial(runtime: StreamingSessionRuntime, payload: dict) -> None:
    decode_ms = float(payload.get("decode_ms") or 0.0)
    audio_seconds = float(payload.get("audio_seconds") or 0.0)
    text = str(payload.get("text") or "")
    seq = int(payload.get("seq") or 0)
    suppressed = bool(payload.get("suppressed"))
    is_final = bool(payload.get("is_final"))
    now = time.monotonic()
    with runtime.state_lock:
        s = runtime.stats
        s.n_decodes += 1
        s.total_decode_ms += decode_ms
        s.max_decode_ms = max(s.max_decode_ms, decode_ms)
        if suppressed:
            s.hallucinations_suppressed += 1
        s.audio_seconds_last = audio_seconds
        if s.first_partial_ms is None:
            s.first_partial_ms = round((now - s.started_monotonic) * 1000.0, 2)

        # Sliding-window accumulator. Once the server's window is approaching full
        # (audio_seconds >= ratio * window_seconds), each new partial may have
        # dropped words off the front that we'll never see again. Diff against
        # the previous partial and promote those leading words into committed_buffer.
        # Suppressed (hallucination) frames don't update the accumulator — they
        # echo the prior text and we shouldn't drift on them.
        if not suppressed:
            window_full = (
                s.window_seconds > 0
                and audio_seconds >= s.window_seconds * STREAMING_COMMIT_THRESHOLD_RATIO
            )
            if window_full and s.last_partial_text:
                committed_delta, tail = _merge_partial(s.last_partial_text, text)
                if committed_delta:
                    s.committed_buffer = _trim_committed(
                        s.committed_buffer + committed_delta,
                        STREAMING_COMMITTED_MAX_CHARS_DEFAULT,
                    )
                s.last_partial_text = tail
            else:
                s.last_partial_text = text
            # On is_final, fold whatever's left in last_partial_text into committed
            # so the displayed transcript matches what was actually decoded.
            if is_final and s.last_partial_text:
                tail = s.last_partial_text.strip()
                if tail:
                    sep = "" if not s.committed_buffer else " "
                    s.committed_buffer = _trim_committed(
                        s.committed_buffer + sep + tail,
                        STREAMING_COMMITTED_MAX_CHARS_DEFAULT,
                    )
                s.last_partial_text = ""

        runtime.state.active = True
        runtime.state.connected = True
        runtime.state.committed_text = s.committed_buffer
        runtime.state.text = s.last_partial_text if not suppressed else text
        runtime.state.last_seq = seq
        runtime.state.last_partial_received_at = now
        runtime.state.n_partials = s.n_decodes
        runtime.state.last_decode_ms = decode_ms
        runtime.state.error_code = None


def _maybe_trip_fallback(runtime: StreamingSessionRuntime) -> None:
    """If drops or decode times exceed thresholds, mark the session as fallback."""
    chunk_period_ms = float(runtime.chunk_ms)
    with runtime.state_lock:
        s = runtime.stats
        if s.fallback:
            return
        too_many_drops = s.frames_dropped >= runtime.fallback_drop_threshold
        avg_decode_ms = (s.total_decode_ms / s.n_decodes) if s.n_decodes else 0.0
        too_slow = (
            s.n_decodes >= 3
            and chunk_period_ms > 0
            and avg_decode_ms > runtime.fallback_decode_ratio * chunk_period_ms
        )
        if not (too_many_drops or too_slow):
            return
        s.fallback = True
        runtime.state.fallback = True
        runtime.state.error_code = "fallback"
    runtime._stop.set()


def _shutdown(runtime: StreamingSessionRuntime) -> None:
    # ``websocket-client``'s default close-handshake timeout is 3.0s. On loopback
    # the handshake usually completes in milliseconds, but a half-closed peer
    # can stall it for the full 3s. Cap to 0.5s so the shutdown is bounded; the
    # process is exiting / take is over by the time this runs anyway.
    try:
        runtime.ws.close(timeout=0.5)
    except TypeError:
        # Older websocket-client versions don't accept a timeout kwarg.
        try:
            runtime.ws.close()
        except Exception:
            pass
    except Exception:
        pass
    if runtime.receiver_thread is not None:
        runtime.receiver_thread.join(timeout=0.4)
    if runtime.sender_thread is not None:
        runtime.sender_thread.join(timeout=0.2)


# ---------------------------------------------------------------------------
# Helpers for slicing into uniform chunk_ms blocks (used by the audio tee)
# ---------------------------------------------------------------------------


def chunk_samples_for_ms(chunk_ms: int) -> int:
    """Number of float32 samples in one chunk at the configured ms size."""
    return max(1, int(SAMPLE_RATE * (chunk_ms / 1000.0)))
