"""Tests for voxium.agent_dispatch (dispatch, fake session, rate limit)."""

import time

import pytest

from voxium.agent_dispatch import (
    AgentDispatch,
    ClientState,
    FakeSessionPort,
    InflightFlag,
    TokenBucket,
)

# ---------------------------------------------------------------------------
# Helper: reset shared buckets before tests that depend on specific state
# ---------------------------------------------------------------------------


def _reset_mutable_bucket():
    """Reset the shared mutable-rate-limit bucket to capacity 2."""
    AgentDispatch._mutable_bucket = TokenBucket(capacity=2, refill_per_second=2.0)


def _reset_read_bucket():
    """Reset the shared read-rate-limit bucket to capacity 20."""
    AgentDispatch._read_bucket = TokenBucket(capacity=20, refill_per_second=20.0)


# ---------------------------------------------------------------------------
# Fake SessionPort state behaviour
# ---------------------------------------------------------------------------


def test_fake_idle():
    fp = FakeSessionPort()
    assert fp.state == ClientState.IDLE
    assert fp.input_mode == "ptt"
    assert fp.inflight == set()


def test_fake_recording_makes_dispatch_busy():
    fp = FakeSessionPort(state=ClientState.RECORDING)
    dispatch = AgentDispatch(fp, {"status:read", "input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    # ptt_engage while RECORDING = already_recording -> no session_busy
    assert result["ok"] or result.get("error", {}).get("code") != "session_busy"


@pytest.mark.linux
def test_fake_recording_refuses_vox():
    _reset_mutable_bucket()
    fp = FakeSessionPort(state=ClientState.RECORDING)
    dispatch = AgentDispatch(fp, {"input:vox"})
    result = dispatch.dispatch("input_mode_set", {"mode": "vox"})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"
    assert result["error"]["details"]["state"] == "recording"


@pytest.mark.linux
def test_fake_recording_refuses_paste():
    _reset_mutable_bucket()
    fp = FakeSessionPort(state=ClientState.RECORDING)
    dispatch = AgentDispatch(fp, {"output:paste", "transcript:read"})
    result = dispatch.dispatch("paste_history", {"index": 1})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


@pytest.mark.linux
def test_fake_recording_refuses_retransmit():
    _reset_mutable_bucket()
    fp = FakeSessionPort(state=ClientState.RECORDING)
    dispatch = AgentDispatch(fp, {"session:retransmit"})
    result = dispatch.dispatch("retransmit_last", {})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


# ---------------------------------------------------------------------------
# Busy from inflight while IDLE
# ---------------------------------------------------------------------------


@pytest.mark.linux
def test_inflight_retransmit_makes_busy():
    _reset_mutable_bucket()
    fp = FakeSessionPort(
        state=ClientState.IDLE,
        inflight=[InflightFlag.RETRANSMIT],
    )
    dispatch = AgentDispatch(fp, {"input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


@pytest.mark.linux
def test_inflight_slash_apply_makes_busy():
    _reset_mutable_bucket()
    fp = FakeSessionPort(
        state=ClientState.IDLE,
        inflight=[InflightFlag.SLASH_APPLY],
    )
    dispatch = AgentDispatch(fp, {"input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


# ---------------------------------------------------------------------------
# Busy from TRANSCRIBING / COMMAND_INPUT
# ---------------------------------------------------------------------------


@pytest.mark.linux
def test_transcribing_makes_busy():
    _reset_mutable_bucket()
    fp = FakeSessionPort(state=ClientState.TRANSCRIBING)
    dispatch = AgentDispatch(fp, {"input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


@pytest.mark.linux
def test_command_input_makes_busy():
    _reset_mutable_bucket()
    fp = FakeSessionPort(state=ClientState.COMMAND_INPUT)
    dispatch = AgentDispatch(fp, {"input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "session_busy"


# ---------------------------------------------------------------------------
# Wrong mode / hotkey / debounce
# ---------------------------------------------------------------------------


@pytest.mark.linux
def test_hotkey_held():
    _reset_mutable_bucket()
    fp = FakeSessionPort(active_hotkey="f9")
    dispatch = AgentDispatch(fp, {"input:ptt"})
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "hotkey_held"


@pytest.mark.linux
def test_paste_debounced():
    _reset_mutable_bucket()
    fp = FakeSessionPort(paste_debounce_remaining_ms=250)
    dispatch = AgentDispatch(fp, {"output:paste", "transcript:read"})
    result = dispatch.dispatch("paste_history", {"index": 1})
    assert not result["ok"]
    assert result["error"]["code"] == "paste_debounced"


# ---------------------------------------------------------------------------
# Scope denial
# ---------------------------------------------------------------------------


def test_scope_denied():
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"status:read"})  # no input:ptt
    result = dispatch.dispatch("ptt_engage", {})
    assert not result["ok"]
    assert result["error"]["code"] == "scope_denied"


# ---------------------------------------------------------------------------
# Shutting down
# ---------------------------------------------------------------------------


def test_shutting_down():
    fp = FakeSessionPort(is_shutting_down=True)
    dispatch = AgentDispatch(fp, {"status:read"})
    result = dispatch.dispatch("session_status", {})
    assert not result["ok"]
    assert result["error"]["code"] == "shutting_down"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_allows_two_mutations():
    AgentDispatch._mutable_bucket = TokenBucket(capacity=2, refill_per_second=2.0)
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"clipboard:write"})
    r1 = dispatch.dispatch("clipboard_set", {"text": "a"})
    r2 = dispatch.dispatch("clipboard_set", {"text": "b"})
    # Both should not be rate_limited (they'll be internal since handler not wired, but not rate_limited)
    assert r1.get("error", {}).get("code") != "rate_limited"
    assert r2.get("error", {}).get("code") != "rate_limited"


def test_rate_limit_blocks_third_mutation():
    AgentDispatch._mutable_bucket = TokenBucket(capacity=2, refill_per_second=2.0)
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"clipboard:write"})
    dispatch.dispatch("clipboard_set", {"text": "a"})
    dispatch.dispatch("clipboard_set", {"text": "b"})
    r3 = dispatch.dispatch("clipboard_set", {"text": "c"})
    assert not r3["ok"]
    assert r3["error"]["code"] == "rate_limited"
    assert r3["error"]["retryable"] is True
    assert "retry_after_ms" in r3["error"]["details"]


# ---------------------------------------------------------------------------
# JSON schema validation
# ---------------------------------------------------------------------------


def test_invalid_tool_name():
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"status:read"})
    result = dispatch.dispatch("nonexistent_tool", {})
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_arguments"


def test_missing_required_field():
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"transcript:read"})
    result = dispatch.dispatch("transmission_get", {})  # missing 'id'
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_arguments"


def test_unknown_fields_rejected():
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"transcript:read"})
    result = dispatch.dispatch("transmission_get", {"id": 1, "extra": True})
    assert not result["ok"]
    assert result["error"]["code"] == "invalid_arguments"


# ---------------------------------------------------------------------------
# Fake tools/list
# ---------------------------------------------------------------------------


def test_tool_list_filtered():
    fp = FakeSessionPort()
    dispatch = AgentDispatch(fp, {"status:read"})
    tools = dispatch.tool_list()
    names = {t.name for t in tools}
    assert "session_status" in names
    assert "ptt_engage" not in names  # needs input:ptt scope


# ---------------------------------------------------------------------------
# TokenBucket refill
# ---------------------------------------------------------------------------


def test_bucket_refill():
    AgentDispatch._mutable_bucket = TokenBucket(capacity=2, refill_per_second=100.0)
    bucket = AgentDispatch._mutable_bucket
    # Exhaust both tokens
    assert bucket.acquire()[0] is True
    assert bucket.acquire()[0] is True
    assert bucket.acquire()[0] is False
    # Wait for refill
    time.sleep(0.05)  # 50ms at 100/s -> ~5 tokens
    assert bucket.acquire()[0] is True
