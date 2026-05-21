"""Tests for the WhisperHealthPoller client helper."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import voxium.inference_health as ih
import voxium.inference_health_client as ihc


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict | list | None = None
    raise_on_json: bool = False

    def json(self) -> dict | list:
        if self.raise_on_json:
            raise ValueError("not json")
        return self.payload  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _reset_registry():
    ih.reset_for_tests()
    yield
    ih.reset_for_tests()


def _ok_response(server: str = "whisper", state: str = ih.STATE_OK) -> _FakeResponse:
    return _FakeResponse(
        status_code=200,
        payload={
            "snapshots": [
                {
                    "server": server,
                    "state": state,
                    "last_ok_at": 100.0,
                    "last_error_at": None,
                    "last_error_msg": None,
                    "consecutive_failures": 0,
                }
            ]
        },
    )


def test_fetch_returns_whisper_snapshot_on_success() -> None:
    snap = ihc.fetch_whisper_inference_health(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _ok_response(),
    )
    assert snap is not None
    assert snap.server == "whisper"
    assert snap.state == ih.STATE_OK


def test_fetch_returns_none_on_http_error() -> None:
    snap = ihc.fetch_whisper_inference_health(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _FakeResponse(status_code=503),
    )
    assert snap is None


def test_fetch_returns_none_on_request_exception() -> None:
    import requests

    def _boom(*a, **k):
        raise requests.ConnectionError("refused")

    snap = ihc.fetch_whisper_inference_health(
        "http://127.0.0.1:9001",
        requests_get=_boom,
    )
    assert snap is None


def test_fetch_returns_none_when_no_whisper_in_payload() -> None:
    snap = ihc.fetch_whisper_inference_health(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _ok_response(server="polish"),
    )
    assert snap is None


def test_fetch_returns_none_on_malformed_json() -> None:
    snap = ihc.fetch_whisper_inference_health(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _FakeResponse(
            status_code=200, raise_on_json=True
        ),
    )
    assert snap is None


def test_fetch_handles_empty_server_url() -> None:
    snap = ihc.fetch_whisper_inference_health(
        "",
        requests_get=lambda url, timeout: _ok_response(),
    )
    assert snap is None


def test_poll_and_apply_replaces_local_whisper_health() -> None:
    ih.get_health("whisper").record_error("local")
    ok = ihc.poll_and_apply_whisper_health(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _ok_response(),
    )
    assert ok is True
    snap = ih.get_health("whisper").snapshot()
    assert snap.consecutive_failures == 0
    assert snap.last_ok_at == 100.0


def test_tick_once_marks_unreachable_after_threshold() -> None:
    poller = ihc.WhisperHealthPoller(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: _FakeResponse(status_code=500),
        unreachable_threshold=2,
    )
    # First failure: silent; second crosses threshold and records an error.
    assert poller.tick_once() is False
    assert ih.get_health("whisper").snapshot().state == ih.STATE_UNKNOWN
    assert poller.tick_once() is False
    assert ih.get_health("whisper").snapshot().state in {
        ih.STATE_DEGRADED,
        ih.STATE_FAILED,
    }


def test_tick_once_clears_failures_on_recovery() -> None:
    seq = [
        _FakeResponse(status_code=500),
        _FakeResponse(status_code=500),
        _ok_response(),
    ]
    calls = iter(seq)
    poller = ihc.WhisperHealthPoller(
        "http://127.0.0.1:9001",
        requests_get=lambda url, timeout: next(calls),
        unreachable_threshold=2,
    )
    poller.tick_once()
    poller.tick_once()  # crosses threshold -> error recorded
    poller.tick_once()  # success -> replace_from
    snap = ih.get_health("whisper").snapshot()
    assert snap.last_ok_at == 100.0
