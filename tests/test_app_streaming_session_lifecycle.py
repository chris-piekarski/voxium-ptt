"""
Tests for the streaming-session lifecycle helpers in :mod:`voxium.app`.

Specifically: confirm that opening and closing a /transcribe-stream session never
runs synchronous WebSocket I/O on the calling thread. PTT key handling holds
``state_lock`` during ``start_recording``; if the WS handshake blocked the lock
the operator's PTT release would stall (the original bug behind this fix). These
tests exercise the helpers with a fake ``transcribe_stream_client.start_session``
that sleeps long enough that any synchronous wiring would be obviously broken.
"""

from __future__ import annotations

# pylint: disable=protected-access  # exercising the private lifecycle helpers

import threading
import time
from types import SimpleNamespace

import pytest

import voxium.app as app_mod
from voxium import transcribe_stream_client


@pytest.fixture(autouse=True)
def _isolate_streaming_state(monkeypatch):
    """Reset module-level streaming state between tests."""
    monkeypatch.setattr(app_mod, "_streaming_session", None, raising=False)
    monkeypatch.setattr(app_mod, "ptt_status_box", None, raising=False)
    app_mod._streaming_intent_active.clear()
    yield
    app_mod._streaming_intent_active.clear()
    monkeypatch.setattr(app_mod, "_streaming_session", None, raising=False)


def _fake_runtime():
    """Minimal stand-in for StreamingSessionRuntime for install/teardown checks."""
    return SimpleNamespace(name="fake-runtime")


def _enable_streaming_config(
    monkeypatch, *, server_url: str = "http://127.0.0.1:8002/transcribe"
):
    """Set up app_mod.config so _streaming_enabled_now() returns True."""
    fake_box = SimpleNamespace(
        update_live_readback=lambda *_a, **_k: None,
    )
    fake_config = SimpleNamespace(
        stream_transcribe=True,
        minimal=False,
        quiet=False,
        server_url=server_url,
    )
    monkeypatch.setattr(app_mod, "config", fake_config)
    monkeypatch.setattr(app_mod, "ptt_status_box", fake_box)
    return fake_box


def test_maybe_open_does_not_block_caller(monkeypatch):
    """``_maybe_open_streaming_session`` must return immediately even if start_session sleeps."""
    _enable_streaming_config(monkeypatch)
    sleep_event = threading.Event()
    finish_event = threading.Event()

    def slow_start_session(*_a, **_k):
        sleep_event.set()
        # If the caller of _maybe_open_streaming_session ever waits on this, the
        # test would time out. Releasing only after we've verified the caller
        # already returned ensures the WS open is on a background thread.
        finish_event.wait(timeout=2.0)
        return _fake_runtime()

    monkeypatch.setattr(transcribe_stream_client, "start_session", slow_start_session)

    t0 = time.perf_counter()
    app_mod._maybe_open_streaming_session()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # The synchronous return must be near-instant. Sleep is in a daemon thread.
    # The fake start_session would block for ~2s, so 500ms cleanly distinguishes
    # "spawned a thread" from "waited synchronously" while tolerating WSL/CI scheduler jitter.
    assert elapsed_ms < 500, f"_maybe_open_streaming_session took {elapsed_ms:.1f}ms"
    # The worker thread must have started the slow call by now.
    assert sleep_event.wait(timeout=1.0), "open worker thread never started"
    # Allow the worker to finish; install runs on its thread.
    finish_event.set()
    # Wait for the install to complete (bounded).
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and app_mod._streaming_session is None:
        time.sleep(0.02)
    assert app_mod._streaming_session is not None
    # Cleanup so the autouse fixture's teardown sees a clean state.
    app_mod._streaming_session = None


def test_open_worker_tears_down_runtime_when_intent_cleared(monkeypatch):
    """If the take ends before the WS handshake completes, the runtime is closed."""
    _enable_streaming_config(monkeypatch)
    runtime_ready = threading.Event()
    closed = []

    def slow_start_session(*_a, **_k):
        # Wait until the test has cleared intent before returning the runtime.
        runtime_ready.wait(timeout=2.0)
        return _fake_runtime()

    def fake_close_session(rt):
        closed.append(rt)

    monkeypatch.setattr(transcribe_stream_client, "start_session", slow_start_session)
    monkeypatch.setattr(transcribe_stream_client, "close_session", fake_close_session)

    app_mod._maybe_open_streaming_session()
    # Simulate take ending before WS handshake completes.
    app_mod._streaming_intent_active.clear()
    runtime_ready.set()
    # Wait for the worker to finish and tear down its runtime.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not closed:
        time.sleep(0.02)
    assert closed, "open worker did not tear down orphan runtime"
    assert app_mod._streaming_session is None


def test_close_does_not_block_caller(monkeypatch):
    """``_close_streaming_session`` must return immediately even if end_session is slow."""
    _enable_streaming_config(monkeypatch)
    runtime = _fake_runtime()
    monkeypatch.setattr(app_mod, "_streaming_session", runtime, raising=False)
    app_mod._streaming_intent_active.set()

    started = threading.Event()
    release = threading.Event()

    def slow_end_session(rt, **_k):
        started.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr(transcribe_stream_client, "end_session", slow_end_session)
    monkeypatch.setattr(
        transcribe_stream_client,
        "get_session_stats",
        lambda rt: {"model": "fake", "ok": True, "n_decodes": 0},
    )

    t0 = time.perf_counter()
    app_mod._close_streaming_session(graceful=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    # Same rationale as test_maybe_open_does_not_block_caller: fake end_session
    # would block ~2s synchronously, so 500ms cleanly proves "spawned a thread".
    assert elapsed_ms < 500, f"_close_streaming_session took {elapsed_ms:.1f}ms"
    # Worker must have started the slow teardown by now.
    assert started.wait(timeout=1.0), "close worker thread never started"
    # The runtime is detached on the calling thread, so subsequent opens can race-free.
    assert app_mod._streaming_session is None
    assert not app_mod._streaming_intent_active.is_set()
    release.set()


def test_close_when_no_session_clears_intent_only(monkeypatch):
    """If no session is installed yet, _close_streaming_session is a no-op except for intent."""
    _enable_streaming_config(monkeypatch)
    app_mod._streaming_intent_active.set()
    app_mod._streaming_session = None

    end_called = []
    monkeypatch.setattr(
        transcribe_stream_client,
        "end_session",
        lambda *a, **k: end_called.append(True),
    )

    app_mod._close_streaming_session(graceful=True)

    assert end_called == []
    assert not app_mod._streaming_intent_active.is_set()


def test_open_worker_does_not_install_when_session_already_present(monkeypatch):
    """A second open completing while a runtime is already installed must not race."""
    _enable_streaming_config(monkeypatch)
    existing = _fake_runtime()
    app_mod._streaming_intent_active.set()
    app_mod._streaming_session = existing

    closed = []
    monkeypatch.setattr(
        transcribe_stream_client,
        "start_session",
        lambda *a, **k: _fake_runtime(),
    )
    monkeypatch.setattr(transcribe_stream_client, "close_session", closed.append)

    # Run the worker directly (synchronously) — the install logic should detect
    # the existing session and tear down the new runtime.
    app_mod._streaming_open_worker("http://127.0.0.1:8002/transcribe")
    assert app_mod._streaming_session is existing
    assert len(closed) == 1


def test_maybe_open_warns_once_when_websocket_dep_missing(monkeypatch):
    """When websocket-client isn't installed, surface one Downlink note + don't spawn."""
    _enable_streaming_config(monkeypatch)
    monkeypatch.setattr(
        transcribe_stream_client, "websocket_dependency_available", lambda: False
    )
    panel_calls = []
    monkeypatch.setattr(
        app_mod,
        "print_agent_telemetry_panel",
        lambda console, items, **kw: panel_calls.append((items, kw)),
    )
    # Force the one-shot guard to fire fresh in this test.
    monkeypatch.setattr(app_mod, "_streaming_missing_dep_warned", False)

    # First call: should emit the panel and NOT touch streaming_intent_active.
    app_mod._maybe_open_streaming_session()
    assert len(panel_calls) == 1
    items, kw = panel_calls[0]
    assert "websocket-client not installed" in items[0][0]
    assert kw.get("downlink_subtitle") == "streaming"
    assert not app_mod._streaming_intent_active.is_set()

    # Second call: must NOT re-emit (one-shot).
    app_mod._maybe_open_streaming_session()
    assert len(panel_calls) == 1


def test_cleanup_client_runtime_tears_down_streaming_session(monkeypatch):
    """
    Ctrl+C path: ``cleanup_client_runtime`` must clear streaming intent and
    detach the runtime so any in-flight handshake tears down its orphan and
    the receiver/sender daemon threads have a clean reason to exit.
    """
    _enable_streaming_config(monkeypatch)
    runtime = _fake_runtime()
    app_mod._streaming_session = runtime
    app_mod._streaming_intent_active.set()

    # Stub all the other things cleanup_client_runtime touches so the test
    # focuses on the streaming hook only.
    monkeypatch.setattr(app_mod, "_stop_morse_audio", lambda: None)
    monkeypatch.setattr(app_mod, "_stop_vox_listening", lambda: None)
    monkeypatch.setattr(app_mod, "_set_input_mode", lambda _m: None)
    monkeypatch.setattr(app_mod, "_end_recording_hud_line", lambda: None)
    monkeypatch.setattr(app_mod, "set_terminal_title", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "stop_managed_llama_cpp", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "stream", None, raising=False)
    monkeypatch.setattr(app_mod, "managed_llama_cpp", None, raising=False)

    # Replace the streaming worker shutdown so we can observe the hand-off.
    closed = []
    monkeypatch.setattr(transcribe_stream_client, "close_session", closed.append)
    monkeypatch.setattr(transcribe_stream_client, "end_session", lambda *a, **k: None)
    monkeypatch.setattr(
        transcribe_stream_client,
        "get_session_stats",
        lambda rt: {"model": "fake", "ok": True, "n_decodes": 0},
    )

    # Use a stubbed status box that records close() calls but does not raise.
    closed_box = []
    fake_box = SimpleNamespace(
        update_live_readback=lambda *_a, **_k: None,
        close=lambda: closed_box.append(True),
    )
    monkeypatch.setattr(app_mod, "ptt_status_box", fake_box)

    app_mod.cleanup_client_runtime()

    # Streaming must be detached and intent cleared, regardless of whether the
    # close worker has finished yet (it runs in a daemon thread).
    assert app_mod._streaming_session is None
    assert not app_mod._streaming_intent_active.is_set()
    # The close worker should have been kicked off on a daemon thread and
    # eventually torn down the runtime via close_session.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not closed:
        time.sleep(0.02)
    assert closed, "close_session was never called from the cleanup hook"
    assert closed_box == [True]
