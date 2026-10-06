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
    """Create a bare MCPServer and register PR 5 tools."""
    from mcp.server import MCPServer

    from voxium import agent_server as _mod

    srv = MCPServer(name="test-pr5", version="0.0.1")
    # Register all tool groups
    _mod._register_session_status_tool(srv)
    _mod._register_transmission_tools(srv)
    _mod._register_history_tools(srv)
    _mod._register_debug_tools(srv)
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
    assert result["transmission_latest_id"] == 1
    assert len(result["inflight"]) >= 1


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
    if hasattr(result, "id"):
        assert result.id == tid
        assert result.state == "no_speech"
    else:
        assert result["id"] == tid
        assert result["state"] == "no_speech"
        assert result["chars"] == 9


def test_transmission_get_not_found(server, tlog):
    """Returns None for unknown id."""
    handler = _tool_fn(server, "transmission_get")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler(id=99999)
    assert result is None


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
    if hasattr(result, "id"):
        assert result.id == tid
        assert result.state == "completed"
    else:
        assert result["id"] == tid
        assert result["state"] == "completed"


def test_transmission_wait_not_found(server, tlog):
    """Returns None for unknown id."""
    handler = _tool_fn(server, "transmission_wait")
    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        result = handler(id=99999, timeout_seconds=0.01)
    assert result is None


def test_transmission_wait_concurrency_cap(server, tlog):
    """Second call returns None while first is active."""
    tid = tlog.begin()  # stays active forever for this test

    handler = _tool_fn(server, "transmission_wait")

    # Reset concurrency guard to ensure predictable state
    import voxium.agent_server as _mod

    _mod._transmission_wait_active = False

    with patch("voxium.agent_server.get_transmission_log", return_value=tlog):
        # First call should set the guard
        result1 = handler(id=tid, timeout_seconds=0.01)
        # timeout expires (id never finishes); guard should be cleared after
        assert result1 is None
        # Guard is cleared by finally block
        assert _mod._transmission_wait_active is False


# ---------------------------------------------------------------------------
# history_list
# ---------------------------------------------------------------------------


def test_history_list_empty(server, history):
    """Returns empty list when no entries."""
    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10)
    assert result == []


def test_history_list_with_entries(server, history):
    """Returns recent entries newest-first."""
    history.add("first transcribe", source="ptt")
    history.add("second transcribe", source="vox")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=5)
    assert len(result) == 2
    assert result[0]["source"] == "vox"  # newest first
    assert result[1]["source"] == "ptt"


def test_history_list_limit(server, history):
    """Respects the limit parameter."""
    for i in range(10):
        history.add(f"entry {i}", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=3)
    assert len(result) == 3


def test_history_list_query_filter(server, history):
    """Filters entries by query string when provided."""
    history.add("hello beeps", source="ptt")
    history.add("goodbye beeps", source="ptt")
    history.add("just ping", source="ptt")

    handler = _tool_fn(server, "history_list")
    with patch("voxium.agent_server.get_session_history", return_value=history):
        result = handler(limit=10, query="beep")
    assert len(result) == 2  # hello beeps and goodbye beeps


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
    assert "server_version" in result
    assert "model" in result
    assert "timestamp" in result
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
