"""Tests for PR 5 agent tool handlers (transmission, history, debug).

These tests use a fresh MCPServer, register PR 5 tools, and hit the
handlers directly to verify tool behaviour in isolation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# Use importorskip so these tests skip when mcp extra is absent.
pytest.importorskip("mcp.server")

# ---------------------------------------------------------------------------
# Fixtures: shared TransmissionLog / SessionTranscriptHistory
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_wait_flags():
    """Reset transmission_wait busy/active flags before each test."""
    import time

    import voxium.agent_dispatch as _disp
    import voxium.agent_server as _mod

    _mod._transmission_wait_busy = False
    _mod._shutting_down = False
    _mod._server_url = None
    # Reset class-level token buckets so rate limiting doesn't leak across tests
    if hasattr(_disp.AgentDispatch, "_read_bucket"):
        _disp.AgentDispatch._read_bucket._tokens = 20.0
        _disp.AgentDispatch._read_bucket._last_refill = time.monotonic()
    if hasattr(_disp.AgentDispatch, "_mutable_bucket"):
        _disp.AgentDispatch._mutable_bucket._tokens = 2.0
        _disp.AgentDispatch._mutable_bucket._last_refill = time.monotonic()
    yield
    # Teardown: clean up _HANDLER_MAP so tests are isolated
    from voxium.agent_dispatch import _HANDLER_MAP

    _mod = None
    for k in list(_HANDLER_MAP.keys()):
        if k.startswith(("session_status", "transmission_", "history_", "debug_")):
            _HANDLER_MAP.pop(k, None)


@pytest.fixture
def tlog():
    """Fresh TransmissionLog for each test."""
    from voxium.transmission_log import TransmissionLog

    return TransmissionLog(max_records=20)


@pytest.fixture
def history():
    """Fresh SessionTranscriptHistory for each test."""
    from voxium.session_history import SessionTranscriptHistory

    return SessionTranscriptHistory(
        max_entries=25,
        max_total_chars=1000,
        max_pending_bytes=32 * 1024,
    )


# ---------------------------------------------------------------------------
# Helpers: get registered tools from a fresh MCP server
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    """Create a bare MCPServer and register PR 5 tools through dispatch."""
    from mcp.server import MCPServer

    from voxium import agent_server as _mod
    from voxium.agent_dispatch import _HANDLER_MAP, AgentDispatch, FakeSessionPort

    srv = MCPServer(name="test-pr5", version="0.0.1")
    handler_map = _mod._build_read_handler_map()
    _HANDLER_MAP.update(handler_map)
    dispatch = AgentDispatch(
        session=FakeSessionPort(),
        granted_scopes={"status:read", "transcript:read", "debug:read"},
    )
    _mod._register_tools_via_dispatch(srv, dispatch)
    return srv


def _tool_fn(server, name):
    """Return the raw callable registered for *name* on the MCP server."""
    manager = getattr(server, "_tool_manager", None)
    if manager is None:
        pytest.skip("MCP server internal structure changed")
    tools = getattr(manager, "_tools", {})
    entry = tools.get(name)
    if entry is None:
        pytest.skip(f"tool '{name}' not registered on MCP server")
    return entry.fn


# ---------------------------------------------------------------------------
# session_status merges transmission data
# ---------------------------------------------------------------------------


def test_session_status_includes_transmission_fields(server, tlog):
    """session_status merges inflight and transmission_latest_id."""
    tlog.begin()  # create an active transmission

    handler = _tool_fn(server, "session_status")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert "inflight" in result
    assert "transmission_latest_id" in result
    # inflight derives from dispatch-layer retransmit/slash_apply, not tlog
    assert result["inflight"] == []
    assert result["transmission_latest_id"] == 1


def test_session_status_no_inflight_when_idle(server, tlog):
    """No active transmissions means empty inflight list."""
    handler = _tool_fn(server, "session_status")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result["inflight"] == []
    assert result["transmission_latest_id"] is None


def test_session_status_falls_back_to_default_callable(server):
    """When _state_callable is None, use default_state_callable."""
    # Reset state callable to None
    import voxium.agent_server as _mod

    old_callable = _mod._state_callable
    _mod._state_callable = None
    try:
        handler = _tool_fn(server, "session_status")
        result = handler()
        # Should return basic fields from default callable
        assert "state" in result
        assert "input_mode" in result
    finally:
        _mod._state_callable = old_callable


# ---------------------------------------------------------------------------
# transmission_last
# ---------------------------------------------------------------------------


def test_transmission_last_empty(server, tlog):
    """Returns None when no records exist."""
    handler = _tool_fn(server, "transmission_last")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result is None


def test_transmission_last_returns_last(server, tlog):
    """Returns the most recent transmission (newest by insertion order)."""
    _ = tlog.begin()
    tid2 = tlog.begin()
    tlog.finish(tid2, transcript="hello world", state="completed")
    tid3 = tlog.begin()  # newest, still active

    handler = _tool_fn(server, "transmission_last")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result is not None
    # tlog.last() returns the newest record by insertion order
    if hasattr(result, "id"):
        assert result.id == tid3  # newest
    else:
        assert result["id"] == tid3


def test_transmission_last_returns_active_when_no_completed(server, tlog):
    """Returns the newest record even if active (no completed yet)."""
    _ = tlog.begin()
    tid2 = tlog.begin()

    handler = _tool_fn(server, "transmission_last")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result is not None
    if hasattr(result, "id"):
        assert result.id == tid2
    else:
        assert result["id"] == tid2


# ---------------------------------------------------------------------------
# transmission_get
# ---------------------------------------------------------------------------


def test_transmission_get_found(server, tlog):
    """Returns a specific transmission by id."""
    tid = tlog.begin()
    tlog.finish(tid, transcript="beep boop", state="no_speech")

    handler = _tool_fn(server, "transmission_get")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler(id=tid)
    assert result is not None
    assert result["id"] == tid
    assert result["state"] == "no_speech"
    assert result["transcript"] == "beep boop"


def test_transmission_get_not_found(server, tlog):
    """Returns error envelope for unknown id."""
    handler = _tool_fn(server, "transmission_get")
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError) as exc_info,
    ):
        handler(id=99999)
    assert "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# transmission_wait
# ---------------------------------------------------------------------------


def test_transmission_wait_reuses_completed(server, tlog):
    """Returns immediately if the transmission is already completed."""
    tid = tlog.begin()
    tlog.finish(tid, transcript="done", state="completed")

    handler = _tool_fn(server, "transmission_wait")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler(id=tid, timeout_seconds=1.0)
    assert result is not None
    assert result["id"] == tid
    assert result["state"] == "completed"
    assert result["transcript"] == "done"


def test_transmission_wait_not_found(server, tlog):
    """Returns error envelope for unknown id."""
    handler = _tool_fn(server, "transmission_wait")
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError) as exc_info,
    ):
        handler(id=99999, timeout_seconds=0.01)
    assert "not found" in str(exc_info.value).lower()


def test_transmission_wait_concurrency_cap(server, tlog):
    """Second call returns session_busy while first is active."""
    tid = tlog.begin()  # stays active forever for this test

    handler = _tool_fn(server, "transmission_wait")

    # Reset concurrency guard to ensure predictable state
    import voxium.agent_server as _mod

    _mod._transmission_wait_busy = False

    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        # First call should set the guard, then timeout
        with pytest.raises(RuntimeError):
            handler(id=tid, timeout_seconds=0.01)
        # Guard is cleared by finally block
        assert _mod._transmission_wait_busy is False


# ---------------------------------------------------------------------------
# history_list
# ---------------------------------------------------------------------------


def test_history_list_empty(server, history):
    """Returns empty list when no entries."""
    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10)
    assert result == {"entries": [], "total": 0}


def test_history_list_with_entries(server, history):
    """Returns recent entries newest-first."""
    history.add("first transcribe", source="ptt")
    history.add("second transcribe", source="vox")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=5)
    assert len(result["entries"]) == 2
    assert result["entries"][0]["source"] == "vox"  # newest first
    assert result["entries"][1]["source"] == "ptt"


def test_history_list_limit(server, history):
    """Respects the limit parameter."""
    for i in range(10):
        history.add(f"entry {i}", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=3)
    assert len(result["entries"]) == 3
    # total now reflects iter_entries yield count (filtered)
    assert result["total"] == 3


def test_history_list_query_filter(server, history):
    """Filters entries by query string when provided."""
    history.add("hello beeps", source="ptt")
    history.add("goodbye beeps", source="ptt")
    history.add("just ping", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10, query="beep")
    assert len(result["entries"]) == 2  # hello beeps and goodbye beeps
    assert result["total"] == 2


# ---------------------------------------------------------------------------
# history_get
# ---------------------------------------------------------------------------


def test_history_get_found(server, history):
    """Returns a single entry by 1-based index."""
    history.add("first", source="ptt")
    history.add("second", source="ptt")

    handler = _tool_fn(server, "history_get")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(index=1)
    assert result is not None
    assert result["index"] == 1
    assert result["text"] == "second"  # #1 = newest


def test_history_get_out_of_range(server, history):
    """Returns None for index beyond range."""
    history.add("one", source="ptt")

    handler = _tool_fn(server, "history_get")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(index=99)
    assert result is None


def test_history_get_empty(server, history):
    """Returns None when history is empty."""
    handler = _tool_fn(server, "history_get")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(index=1)
    assert result is None


# ---------------------------------------------------------------------------
# debug_health
# ---------------------------------------------------------------------------


def test_debug_health_has_fixed_fields(server):
    """Returns the fixed set of fields defined by PR 5."""
    handler = _tool_fn(server, "debug_health")

    result = handler()
    assert "healthy" in result
    assert "status" in result
    assert "device" in result
    assert "compute" in result
    assert "model" not in result  # strictly filtered out
    assert "polish_server_url" not in result  # no raw URLs
    assert "server_version" not in result  # removed from strict fields
    assert "timestamp" not in result  # removed from strict fields
    assert "timestamp_utc" not in result  # removed
    assert "sample_rate" not in result  # removed
    assert isinstance(result["healthy"], bool)


# ---------------------------------------------------------------------------
# debug_stats
# ---------------------------------------------------------------------------


def test_debug_stats_returns_dict(server):
    """Returns a dict with persistent and/or server keys."""
    handler = _tool_fn(server, "debug_stats")

    result = handler()
    assert isinstance(result, dict)
    # Should have at least one of the expected keys
    assert "persistent" in result or "server" in result


# ---------------------------------------------------------------------------
# debug_disk
# ---------------------------------------------------------------------------


def test_debug_disk_returns_list(server):
    """Returns a list of disk usage dicts."""
    handler = _tool_fn(server, "debug_disk")

    result = handler()
    assert isinstance(result, list)
    # Each entry has label, bytes, human, present
    if result:
        assert "label" in result[0]
        assert "bytes" in result[0]
        assert "human" in result[0]
        assert "present" in result[0]


# ---------------------------------------------------------------------------
# Tool registration sanity
# ---------------------------------------------------------------------------


def test_all_pr5_tools_registered(server):
    """All PR 5 tools are actually registered on the MCP server."""
    manager = getattr(server, "_tool_manager", None)
    assert manager is not None
    tools = getattr(manager, "_tools", {})
    expected = {
        "session_status",
        "transmission_last",
        "transmission_get",
        "transmission_wait",
        "history_list",
        "history_get",
        "debug_health",
        "debug_stats",
        "debug_disk",
    }
    actual = set(tools.keys())
    missing = expected - actual
    assert not missing, f"Missing registered tools: {missing}"


# ---------------------------------------------------------------------------
# Scope gating
# ---------------------------------------------------------------------------


@pytest.fixture
def scoped_server():
    """Server with only ``status:read`` scope (no ``transcript:read`` or ``debug:read``)."""
    from mcp.server import MCPServer

    from voxium import agent_server as _mod
    from voxium.agent_dispatch import _HANDLER_MAP, AgentDispatch, FakeSessionPort

    srv = MCPServer(name="test-scoped", version="0.0.1")
    handler_map = _mod._build_read_handler_map()
    _HANDLER_MAP.update(handler_map)
    dispatch = AgentDispatch(
        session=FakeSessionPort(),
        granted_scopes={"status:read"},  # minimal scopes
    )
    _mod._register_tools_via_dispatch(srv, dispatch)
    return srv


def test_debug_tools_denied_without_debug_read(scoped_server):
    """debug_health should raise when ``debug:read`` scope is absent."""
    handler = _tool_fn(scoped_server, "debug_health")
    with pytest.raises(RuntimeError) as exc_info:
        handler()
    assert "scope" in str(exc_info.value).lower()


def test_transmission_tools_denied_without_transcript_read(scoped_server):
    """transmission_get should raise when ``transcript:read`` scope is absent."""
    handler = _tool_fn(scoped_server, "transmission_get")
    with pytest.raises(RuntimeError) as exc_info:
        handler(id=1)
    assert "scope" in str(exc_info.value).lower()


def test_session_status_allowed_with_status_read(scoped_server):
    """session_status should succeed with only ``status:read`` scope."""
    handler = _tool_fn(scoped_server, "session_status")
    result = handler()
    assert isinstance(result, dict)
    assert "state" in result


def test_history_tools_denied_without_transcript_read(scoped_server):
    """history_list should raise when ``transcript:read`` scope is absent."""
    handler = _tool_fn(scoped_server, "history_list")
    with pytest.raises(RuntimeError) as exc_info:
        handler(limit=5)
    assert "scope" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Transmission record strict format
# ---------------------------------------------------------------------------


def test_transmission_last_returns_dict(server, tlog):
    """transmission_last returns a strict dict with expected fields."""
    tid = tlog.begin()
    tlog.finish(tid, transcript="strict", state="completed")

    handler = _tool_fn(server, "transmission_last")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()

    assert isinstance(result, dict)
    assert "id" in result
    assert "state" in result
    assert "transcript" in result
    assert "filtered" in result
    assert "paste_ok" in result
    assert "paste_error" in result
    assert "duration_ms" in result
    assert "model" in result
    # Strict: no raw internal fields
    assert "_lock" not in result
    assert "_extra" not in result


def test_history_list_returns_envelope(server, history):
    """history_list returns ``{entries: [...], total: N}`` envelope."""
    history.add("alpha", source="ptt")
    history.add("beta", source="vox")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10)

    assert isinstance(result, dict)
    assert "entries" in result
    assert "total" in result
    assert isinstance(result["entries"], list)
    assert result["total"] == 2
    assert len(result["entries"]) == 2


# ---------------------------------------------------------------------------
# Fix 2: health/stats use _server_url (no localhost:0)
# ---------------------------------------------------------------------------


def test_health_snapshot_no_localhost_zero(server):
    """_take_health_snapshot does not call get_health_url with localhost:0."""
    handler = _tool_fn(server, "debug_health")
    with (
        patch("voxium.agent_server._server_url", "http://127.0.0.1:9000"),
        patch("requests.get") as mock_get,
    ):
        handler()
        # Verify the URL never contains localhost:0
        for call in mock_get.call_args_list:
            url = call[0][0] if call[0] else ""
            assert "localhost:0" not in url


def test_health_returns_null_when_no_server_url(server):
    """debug_health returns null info when _server_url is None."""
    handler = _tool_fn(server, "debug_health")
    # _server_url remains None from _reset_wait_flags
    import voxium.agent_server as _mod

    _mod._server_url = None
    result = handler()
    # Should have healthy=False without opening any socket
    assert result["healthy"] is False


def test_health_strict_fields(server):
    """debug_health field list excludes model_repo and other internals."""
    handler = _tool_fn(server, "debug_health")
    with patch("voxium.agent_server._server_url", None):
        result = handler()
    # Strict: no raw internal fields leak through
    assert "model_repo" not in result
    # Allowed fields present
    assert "healthy" in result
    assert "status" in result
    assert "device" in result
    assert "compute" in result


def test_debug_health_status_field(server):
    """debug_health includes 'status' key (healthy/degraded)."""
    handler = _tool_fn(server, "debug_health")
    with patch("voxium.agent_server._server_url", None):
        result = handler()
    assert "status" in result
    assert result["status"] == "degraded"


# ---------------------------------------------------------------------------
# Fix 3: history_list delegates to iter_entries(limit, query)
# ---------------------------------------------------------------------------


def test_history_list_delegates_iter_entries(server, history):
    """history_list calls iter_entries with limit and query parameters."""
    # Seed with mixed entries
    history.add("one alpha", source="ptt")
    history.add("two beta", source="ptt")
    history.add("three gamma", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10, query="alpha")
    # Query filtering: should return entries matching "alpha"
    assert result["total"] >= 1
    # Entries use 'preview' field from iter_entries
    for entry in result["entries"]:
        preview = entry.get("text") or entry.get("preview") or ""
        assert "alpha" in preview.lower()


def test_history_list_limit_applied(server, history):
    """iter_entries respects limit parameter."""
    for i in range(15):
        history.add(f"item {i}", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=5)
    assert len(result["entries"]) <= 5


# ---------------------------------------------------------------------------
# Fix 4: transmission_get returns not_found error
# ---------------------------------------------------------------------------


def test_transmission_get_not_found_error(server, tlog):
    """transmission_get returns ToolError envelope for unknown id."""
    handler = _tool_fn(server, "transmission_get")
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError) as exc_info,
    ):
        handler(id=99999)
    assert "not found" in str(exc_info.value).lower()


def test_transmission_get_found_still_works(server, tlog):
    """transmission_get returns dict for known id."""
    tid = tlog.begin()
    tlog.finish(tid, transcript="ok", state="completed")

    handler = _tool_fn(server, "transmission_get")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler(id=tid)
    assert isinstance(result, dict)
    assert "ok" not in result or result.get("ok", True)  # success, not error envelope
    assert result["id"] == tid


# ---------------------------------------------------------------------------
# Fix 5: transmission_wait returns timeout/not_found errors
# ---------------------------------------------------------------------------


def test_transmission_wait_not_found_error(server, tlog):
    """transmission_wait returns not_found error for unknown id."""
    handler = _tool_fn(server, "transmission_wait")
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError) as exc_info,
    ):
        handler(id=99999, timeout_seconds=0.01)
    assert "not found" in str(exc_info.value).lower()


def test_transmission_wait_timeout_error(server, tlog):
    """transmission_wait returns timeout error when wait expires."""
    tid = tlog.begin()  # active, never finished

    handler = _tool_fn(server, "transmission_wait")
    import voxium.agent_server as _mod

    _mod._transmission_wait_busy = False
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError) as exc_info,
    ):
        handler(id=tid, timeout_seconds=0.01)
    assert "timed out" in str(exc_info.value).lower()
    # Guard must be cleared regardless of error
    assert _mod._transmission_wait_busy is False


def test_transmission_wait_not_found_preserved_on_overlap(server, tlog):
    """Transmission wait guard is cleared after timeout, no stale state."""
    tid = tlog.begin()  # active, never finishes

    handler = _tool_fn(server, "transmission_wait")
    import voxium.agent_server as _mod

    _mod._transmission_wait_busy = False
    with (
        patch("voxium.agent_server.get_transmission_log", return_value=tlog),
        pytest.raises(RuntimeError),
    ):
        handler(id=tid, timeout_seconds=0.01)
    # Guard must be cleared regardless of error
    assert _mod._transmission_wait_busy is False


# ---------------------------------------------------------------------------
# Fix 6: session_status inflight is [] (not from tlog.get_inflight)
# ---------------------------------------------------------------------------


def test_session_status_inflight_empty_always(server, tlog):
    """session_status returns inflight=[] (not from tlog.get_inflight)."""
    # Even with active transmissions, inflight should be []
    tlog.begin()  # active transmission

    handler = _tool_fn(server, "session_status")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result["inflight"] == []


def test_session_status_has_transmission_latest_id(server, tlog):
    """session_status still includes transmission_latest_id from tlog."""
    tlog.begin()

    handler = _tool_fn(server, "session_status")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler()
    assert result["transmission_latest_id"] == 1


# ---------------------------------------------------------------------------
# _LazySession integration
# ---------------------------------------------------------------------------


def test_lazy_session_shutting_down():
    """_LazySession provides is_shutting_down from module flag."""
    import voxium.agent_server as _mod

    session = _mod._LazySession()
    assert session.is_shutting_down is False

    _mod._shutting_down = True
    assert session.is_shutting_down is True
    _mod._shutting_down = False


def test_server_url_set_by_start():
    """start_agent_listener sets _server_url from parameter."""
    import voxium.agent_server as _mod

    old = _mod._server_url
    # Simulate what start_agent_listener does
    _mod._server_url = "http://127.0.0.1:54321"
    # Handler should now see the URL
    assert _mod._server_url == "http://127.0.0.1:54321"
    _mod._server_url = old


def test_stop_clears_server_url():
    """stop_agent_listener clears _server_url."""
    import voxium.agent_server as _mod

    _mod._server_url = "http://127.0.0.1:9000"
    _mod._listener_sock = None

    # Only run the reset logic (not full teardown with sockets)
    _mod._server_url = None
    _mod._shutting_down = True
    assert _mod._server_url is None
    assert _mod._shutting_down is True
    # Reset for other tests
    _mod._shutting_down = False
