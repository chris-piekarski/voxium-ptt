"""
Tests for the client-side WebSocket plumbing in
:mod:`voxium.transcribe_stream_client`.

We don't open a real WebSocket here. We patch the lazy-imported ``websocket``
module with a fake that lets us script handshake / partial frames in-process.
"""

from __future__ import annotations

# pylint: disable=redefined-outer-name  # standard pytest fixture parameter pattern

import json
import queue
import threading
import time
from typing import Any

import numpy as np
import pytest

from voxium import transcribe_stream_client

SAMPLE_RATE = 16_000


class _FakeWebSocket:
    """
    Minimal stand-in for ``websocket-client``'s WebSocket.

    Holds two queues (incoming JSON for ``recv``, outgoing bytes/text for
    ``send_binary`` / ``send``). Tests script the recv side with
    :meth:`push_server_frame` and assert on the send side via :attr:`sent_binary`
    / :attr:`sent_text`.
    """

    def __init__(self) -> None:
        self._recv_q: queue.Queue[str] = queue.Queue()
        self.sent_binary: list[bytes] = []
        self.sent_text: list[str] = []
        self.closed = False
        self._timeout = 1.0
        self._send_lock = threading.Lock()

    def push_server_frame(self, payload: dict | str) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self._recv_q.put(payload)

    def settimeout(self, seconds: float) -> None:
        self._timeout = float(seconds)

    def recv(self) -> str:
        if self.closed:
            # Mirror real websocket-client: once close() has run, recv raises
            # rather than blocking forever. Keeps the receiver daemon thread
            # from lingering across tests.
            raise OSError("closed")
        try:
            return self._recv_q.get(timeout=self._timeout)
        except queue.Empty as exc:
            raise _FakeWebSocketException("timed out") from exc

    def send_binary(self, data: bytes) -> None:
        if self.closed:
            raise _FakeWebSocketException("closed")
        with self._send_lock:
            self.sent_binary.append(bytes(data))

    def send(self, text: str) -> None:
        if self.closed:
            raise _FakeWebSocketException("closed")
        with self._send_lock:
            self.sent_text.append(str(text))

    def close(
        self, timeout: float | None = None
    ) -> None:  # noqa: ARG002 - parity with websocket-client API
        self.closed = True
        # Unblock any receiver blocked on _recv_q.get by pushing a sentinel;
        # the next recv() call will then see self.closed and raise OSError.
        try:
            self._recv_q.put_nowait("")
        except Exception:
            pass


class _FakeWebSocketException(Exception):
    pass


class _FakeWebSocketModule:
    """Fake replacement for the ``websocket`` (websocket-client) module."""

    WebSocketException = _FakeWebSocketException

    def __init__(self) -> None:
        self.fake_ws: _FakeWebSocket | None = None
        self.connect_should_fail = False

    def create_connection(self, url: str, timeout: float = 2.0) -> Any:
        if self.connect_should_fail:
            raise _FakeWebSocketException("connection refused")
        ws = _FakeWebSocket()
        # Pre-load the session_open frame so start_session sees it on first recv.
        ws.push_server_frame(
            {
                "type": "session_open",
                "version": 1,
                "sample_rate": SAMPLE_RATE,
                "channels": 1,
                "dtype": "float32",
                "byte_order": "little",
                "window_seconds": 5.0,
                "max_chunk_ms": 1000,
                "language": "en",
                "model": "small.en",
                "vad_filter": True,
                "hallucination_filter": True,
                "session_id": "test-session-1",
            }
        )
        self.fake_ws = ws
        return ws


@pytest.fixture
def fake_ws_module(monkeypatch):
    fake = _FakeWebSocketModule()
    # Force the lazy importer into the "already imported" state with our fake.
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET", fake)
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET_IMPORT_TRIED", True)
    monkeypatch.setattr(
        transcribe_stream_client,
        "_WEBSOCKET_EXCEPTION_CLS",
        _FakeWebSocketException,
    )
    return fake


def test_derive_stream_ws_url_swaps_scheme_and_path() -> None:
    assert (
        transcribe_stream_client.derive_stream_ws_url(
            "http://127.0.0.1:8002/transcribe"
        )
        == "ws://127.0.0.1:8002/transcribe-stream"
    )
    assert (
        transcribe_stream_client.derive_stream_ws_url(
            "https://127.0.0.1:8002/transcribe"
        )
        == "wss://127.0.0.1:8002/transcribe-stream"
    )
    # No port stays no port.
    assert (
        transcribe_stream_client.derive_stream_ws_url("http://127.0.0.1/transcribe")
        == "ws://127.0.0.1/transcribe-stream"
    )


def test_start_session_returns_none_when_websocket_missing(monkeypatch) -> None:
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET", None)
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET_IMPORT_TRIED", True)
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is None


def test_websocket_dependency_probe_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET", None)
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET_IMPORT_TRIED", True)
    assert transcribe_stream_client.websocket_dependency_available() is False


def test_websocket_dependency_probe_when_present(fake_ws_module) -> None:
    assert transcribe_stream_client.websocket_dependency_available() is True


def test_missing_websocket_logs_warning_once(monkeypatch, caplog) -> None:
    """Operators should see a one-shot WARNING (not a buried debug line)."""
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET", None)
    monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET_IMPORT_TRIED", False)
    monkeypatch.setattr(
        transcribe_stream_client, "_WEBSOCKET_IMPORT_MISSING_LOGGED", False
    )
    monkeypatch.setitem(__import__("sys").modules, "websocket", None)

    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="voxium.transcribe_stream_client"):
        first = transcribe_stream_client._ensure_websocket_import()
        # Reset the "tried" flag so the next call would re-attempt the import.
        # This proves the WARNING-once gate is the missing-logged flag.
        monkeypatch.setattr(transcribe_stream_client, "_WEBSOCKET_IMPORT_TRIED", False)
        second = transcribe_stream_client._ensure_websocket_import()

    assert first is False
    assert second is False
    warnings = [r for r in caplog.records if r.levelno >= _logging.WARNING]
    assert len(warnings) == 1
    assert "websocket-client not installed" in warnings[0].message


def test_start_session_returns_none_on_connect_failure(fake_ws_module) -> None:
    fake_ws_module.connect_should_fail = True
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is None


def test_start_session_happy_path(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        assert runtime.url == "ws://127.0.0.1:8002/transcribe-stream"
        assert runtime.stats.model == "small.en"
    finally:
        transcribe_stream_client.close_session(runtime)


def test_push_audio_frame_sends_bytes_via_sender_thread(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        pcm = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)  # 250 ms of silence
        transcribe_stream_client.push_audio_frame(runtime, pcm)
        # Wait briefly for the sender thread to drain the queue.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not fake_ws_module.fake_ws.sent_binary:
            time.sleep(0.02)
        assert fake_ws_module.fake_ws.sent_binary
        sent = fake_ws_module.fake_ws.sent_binary[0]
        # Round-trip: bytes back to float32 must match the input.
        recovered = np.frombuffer(sent, dtype=np.float32)
        assert recovered.size == pcm.size
        assert np.allclose(recovered, pcm)
    finally:
        transcribe_stream_client.close_session(runtime)


def test_push_audio_frame_drops_when_queue_full(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session(
        "http://127.0.0.1:8002/transcribe",
        max_queue_frames=2,
    )
    assert runtime is not None
    try:
        # Stop the sender so the queue can fill, then pre-fill it to capacity.
        # push_audio_frame should now drop on Full once the aggregator emits a chunk.
        runtime._stop.set()
        runtime.sender_thread.join(timeout=0.5)
        for _ in range(runtime.max_queue_frames):
            runtime.queue.put_nowait(b"x" * 4)
        runtime._stop.clear()
        # Push two full 250 ms chunks — both should emit from the aggregator and
        # both should hit Full because the queue is already at capacity.
        full_chunk = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)
        transcribe_stream_client.push_audio_frame(runtime, full_chunk)
        transcribe_stream_client.push_audio_frame(runtime, full_chunk)
        snap = transcribe_stream_client.get_live_state(runtime)
        assert snap is not None
        assert snap.frames_dropped >= 2
    finally:
        runtime._stop.set()
        transcribe_stream_client.close_session(runtime)


def test_receiver_updates_live_state_on_partial(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 1,
                "text": "hello world",
                "audio_seconds": 0.5,
                "decode_ms": 42.0,
                "is_final": False,
                "suppressed": False,
            }
        )
        deadline = time.monotonic() + 1.0
        snap = None
        while time.monotonic() < deadline:
            snap = transcribe_stream_client.get_live_state(runtime)
            if snap is not None and snap.text:
                break
            time.sleep(0.02)
        assert snap is not None
        assert snap.text == "hello world"
        assert snap.connected is True
        assert snap.last_seq == 1
        assert snap.last_decode_ms == 42.0
    finally:
        transcribe_stream_client.close_session(runtime)


def test_end_session_sends_end_frame(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    # Push a final partial so the wait completes promptly.
    fake_ws_module.fake_ws.push_server_frame(
        {
            "type": "partial",
            "seq": 2,
            "text": "final",
            "audio_seconds": 0.5,
            "decode_ms": 50.0,
            "is_final": True,
            "suppressed": False,
        }
    )
    transcribe_stream_client.end_session(runtime, drain_timeout_s=1.0)
    assert any(
        '"type": "end"' in t.replace(" ", "") or '"type":"end"' in t.replace(" ", "")
        for t in fake_ws_module.fake_ws.sent_text
    )
    assert fake_ws_module.fake_ws.closed is True


def test_get_session_stats_shape(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        stats = transcribe_stream_client.get_session_stats(runtime)
        assert stats is not None
        for key in (
            "n_decodes",
            "total_decode_ms",
            "frames_sent",
            "frames_dropped",
            "session_seconds",
            "model",
            "ok",
        ):
            assert key in stats
        assert stats["ok"] is True
        assert stats["model"] == "small.en"
    finally:
        transcribe_stream_client.close_session(runtime)


def test_aggregator_buffers_subchunk_pushes() -> None:
    agg = transcribe_stream_client._ChunkAggregator(chunk_samples=4000)
    # 16 successive 250-sample pushes (≈ what sounddevice gives us at 16ms blocks).
    # Aggregator should emit zero chunks for the first 15 (residue 250-3750), then
    # one 4000-sample chunk on the 16th.
    pcm = np.full(250, 0.5, dtype=np.float32)
    for _ in range(15):
        out = agg.push(pcm)
        assert out == []
    out = agg.push(pcm)
    assert len(out) == 1
    assert out[0].size == 4000
    assert agg.residue_size == 0


def test_aggregator_emits_multiple_chunks_when_pushed_many() -> None:
    agg = transcribe_stream_client._ChunkAggregator(chunk_samples=4000)
    # 9000 samples → 2 full chunks + 1000-sample residue.
    pcm = np.linspace(-1.0, 1.0, 9000, dtype=np.float32)
    out = agg.push(pcm)
    assert len(out) == 2
    assert all(c.size == 4000 for c in out)
    assert agg.residue_size == 1000


def test_aggregator_drain_returns_residue_only() -> None:
    agg = transcribe_stream_client._ChunkAggregator(chunk_samples=4000)
    agg.push(np.zeros(1500, dtype=np.float32))
    residue = agg.drain()
    assert residue is not None
    assert residue.size == 1500
    # Subsequent drain returns None.
    assert agg.drain() is None


def test_push_subchunk_audio_does_not_send_until_aggregator_full(
    fake_ws_module,
) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        # Push 3 × 1000 samples — none should reach the wire yet (3000 < 4000).
        small = np.zeros(1000, dtype=np.float32)
        for _ in range(3):
            transcribe_stream_client.push_audio_frame(runtime, small)
        # Give the sender thread a beat; nothing should have been sent.
        time.sleep(0.1)
        assert fake_ws_module.fake_ws.sent_binary == []
        # 4th push completes a chunk → exactly one binary frame on the wire.
        transcribe_stream_client.push_audio_frame(runtime, small)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not fake_ws_module.fake_ws.sent_binary:
            time.sleep(0.02)
        assert len(fake_ws_module.fake_ws.sent_binary) == 1
        # Bytes round-trip to a 4000-sample float32 chunk.
        recovered = np.frombuffer(
            fake_ws_module.fake_ws.sent_binary[0], dtype=np.float32
        )
        assert recovered.size == 4000
    finally:
        transcribe_stream_client.close_session(runtime)


def test_end_session_flushes_aggregator_residue(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    # Push less than one full chunk (1000 samples) — nothing on the wire yet.
    transcribe_stream_client.push_audio_frame(runtime, np.zeros(1000, dtype=np.float32))
    time.sleep(0.05)
    assert fake_ws_module.fake_ws.sent_binary == []
    # Push the final partial frame so end_session can flush + close cleanly.
    fake_ws_module.fake_ws.push_server_frame(
        {
            "type": "partial",
            "seq": 1,
            "text": "",
            "audio_seconds": 0.0625,
            "decode_ms": 5.0,
            "is_final": True,
            "suppressed": False,
        }
    )
    transcribe_stream_client.end_session(runtime, drain_timeout_s=1.0)
    # The residue chunk (1000 samples) must have been sent before the close.
    assert any(
        np.frombuffer(b, dtype=np.float32).size == 1000
        for b in fake_ws_module.fake_ws.sent_binary
    )


def test_fallback_trips_on_excessive_drops(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session(
        "http://127.0.0.1:8002/transcribe",
        max_queue_frames=1,
        fallback_drop_threshold=2,
    )
    assert runtime is not None
    try:
        runtime._stop.set()
        runtime.sender_thread.join(timeout=0.5)
        runtime.queue.put_nowait(b"x" * 4)  # fill the queue
        runtime._stop.clear()
        full_chunk = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)
        transcribe_stream_client.push_audio_frame(runtime, full_chunk)  # drop #1
        transcribe_stream_client.push_audio_frame(runtime, full_chunk)  # drop #2 → trip
        snap = transcribe_stream_client.get_live_state(runtime)
        assert snap is not None
        assert snap.fallback is True
    finally:
        runtime._stop.set()
        transcribe_stream_client.close_session(runtime)


# ---------------------------------------------------------------------------
# Sliding-window accumulator (`_merge_partial`, `_trim_committed`, `_ingest_partial`)
# ---------------------------------------------------------------------------


def test_merge_partial_full_overlap_commits_nothing() -> None:
    # Window not yet sliding: new partial extends prev — nothing to commit.
    committed, tail = transcribe_stream_client._merge_partial(
        "hello world", "hello world how are you"
    )
    assert committed == ""
    assert tail == "hello world how are you"


def test_merge_partial_partial_overlap_commits_lead_words() -> None:
    # The window slid by one word: "the" fell off the front of prev.
    committed, tail = transcribe_stream_client._merge_partial(
        "the quick brown fox", "quick brown fox jumps"
    )
    assert committed == "the "
    assert tail == "quick brown fox jumps"


def test_merge_partial_no_overlap_commits_nothing() -> None:
    # Whisper revised words at the front — we can't tell drift from real slide,
    # so commit nothing this round. Avoids the duplication bug where prev gets
    # re-emitted into committed_buffer every time the merge fails.
    committed, tail = transcribe_stream_client._merge_partial(
        "alpha beta gamma", "delta epsilon"
    )
    assert committed == ""
    assert tail == "delta epsilon"


def test_merge_partial_revision_does_not_duplicate() -> None:
    # Realistic Whisper revision pattern: same audio, slightly different word
    # choice. Round 1 finds no overlap → no commit. Round 2 cleanly overlaps
    # against the revised text. The revised words appear ONCE on screen.
    committed_a, _ = transcribe_stream_client._merge_partial(
        "what is going on today", "what's going on today my friend"
    )
    assert committed_a == ""
    committed_b, tail_b = transcribe_stream_client._merge_partial(
        "what's going on today my friend", "going on today my friend and"
    )
    assert committed_b == "what's "
    assert tail_b == "going on today my friend and"


def test_merge_partial_punctuation_drift_still_overlaps() -> None:
    # Whisper toggles punctuation between partials — must still match.
    committed, tail = transcribe_stream_client._merge_partial(
        "hello, world.", "hello world! and stuff"
    )
    # Both words normalize equal → full overlap on prev → commit nothing.
    assert committed == ""
    assert tail == "hello world! and stuff"


def test_merge_partial_case_drift_still_overlaps() -> None:
    committed, tail = transcribe_stream_client._merge_partial(
        "the cat sat", "Cat sat on the mat"
    )
    # "cat sat" matches "Cat sat" (case-insensitive); "the" slid off the back.
    assert committed == "the "
    assert tail == "Cat sat on the mat"


def test_merge_partial_identical_commits_nothing() -> None:
    committed, tail = transcribe_stream_client._merge_partial(
        "same words", "same words"
    )
    assert committed == ""
    assert tail == "same words"


def test_merge_partial_empty_prev_returns_new() -> None:
    committed, tail = transcribe_stream_client._merge_partial("", "first words here")
    assert committed == ""
    assert tail == "first words here"


def test_merge_partial_empty_new_holds_committed() -> None:
    # Empty new partial (e.g., VAD trimmed the chunk) — don't drift.
    committed, tail = transcribe_stream_client._merge_partial("the quick brown", "")
    assert committed == ""
    assert tail == ""


def test_merge_partial_single_word_no_overlap_no_commit() -> None:
    committed, tail = transcribe_stream_client._merge_partial("foo", "bar")
    assert committed == ""
    assert tail == "bar"


def test_trim_committed_caps_to_max_chars() -> None:
    text = " ".join(f"word{i:03d}" for i in range(120))  # 120 7-char tokens + spaces
    trimmed = transcribe_stream_client._trim_committed(text, 100)
    assert len(trimmed) <= 100
    # Trim should snap to a word boundary, not chop a token.
    assert not trimmed.startswith(" ")
    assert "word" in trimmed
    assert trimmed in text


def test_trim_committed_under_cap_unchanged() -> None:
    text = "short text"
    assert transcribe_stream_client._trim_committed(text, 100) == "short text"


def test_ingest_partial_promotes_lead_words_when_window_full(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        # First partial — window not yet full (audio_seconds < window).
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 1,
                "text": "the quick brown fox",
                "audio_seconds": 1.0,
                "decode_ms": 30.0,
                "is_final": False,
                "suppressed": False,
            }
        )
        # Second partial — window full (>= 0.95 * 5.0). The leading "the" slid off.
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 2,
                "text": "quick brown fox jumps",
                "audio_seconds": 5.0,
                "decode_ms": 35.0,
                "is_final": False,
                "suppressed": False,
            }
        )
        deadline = time.monotonic() + 1.0
        snap = None
        while time.monotonic() < deadline:
            snap = transcribe_stream_client.get_live_state(runtime)
            if snap is not None and snap.committed_text:
                break
            time.sleep(0.02)
        assert snap is not None
        # On the first partial, last_partial_text was set to "the quick brown fox" but
        # the window wasn't full, so committed stays empty. Once partial #2 arrives
        # with a full window, "the" gets promoted.
        assert snap.committed_text.strip() == "the"
        assert snap.text == "quick brown fox jumps"
    finally:
        transcribe_stream_client.close_session(runtime)


def test_ingest_partial_suppressed_does_not_commit(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 1,
                "text": "real words here",
                "audio_seconds": 5.0,
                "decode_ms": 30.0,
                "is_final": False,
                "suppressed": False,
            }
        )
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 2,
                "text": "thanks for watching",  # classic hallucination
                "audio_seconds": 5.0,
                "decode_ms": 30.0,
                "is_final": False,
                "suppressed": True,
            }
        )
        deadline = time.monotonic() + 1.0
        last_seq = 0
        while time.monotonic() < deadline:
            snap = transcribe_stream_client.get_live_state(runtime)
            if snap is not None and snap.last_seq >= 2:
                last_seq = snap.last_seq
                break
            time.sleep(0.02)
        snap = transcribe_stream_client.get_live_state(runtime)
        assert last_seq == 2
        assert snap is not None
        # Suppressed frame must not push hallucination into the committed buffer.
        assert "watching" not in snap.committed_text
    finally:
        transcribe_stream_client.close_session(runtime)


def test_ingest_partial_final_folds_tail_into_committed(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        fake_ws_module.fake_ws.push_server_frame(
            {
                "type": "partial",
                "seq": 1,
                "text": "one two three",
                "audio_seconds": 5.0,
                "decode_ms": 30.0,
                "is_final": True,
                "suppressed": False,
            }
        )
        deadline = time.monotonic() + 1.0
        snap = None
        while time.monotonic() < deadline:
            snap = transcribe_stream_client.get_live_state(runtime)
            if snap is not None and "three" in snap.committed_text:
                break
            time.sleep(0.02)
        assert snap is not None
        assert "three" in snap.committed_text
    finally:
        transcribe_stream_client.close_session(runtime)


def test_start_session_captures_window_seconds(fake_ws_module) -> None:
    runtime = transcribe_stream_client.start_session("http://127.0.0.1:8002/transcribe")
    assert runtime is not None
    try:
        # The fake's session_open frame advertises window_seconds=5.0.
        assert runtime.stats.window_seconds == 5.0
    finally:
        transcribe_stream_client.close_session(runtime)
