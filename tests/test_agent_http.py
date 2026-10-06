"""Tests for ``voxium.agent_http`` — bearer middleware.

Pure module: no MCP SDK import. We invoke the ASGI middleware directly
against a fake inner app.  ``httpx`` is **not** a hard dependency here; we
use its test fixtures only when available (already a dev extra).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from voxium.agent_http import AgentBearerMiddleware

# ---------------------------------------------------------------------------
# Fake ASGI app (returns 200 with JSON body)
# ---------------------------------------------------------------------------


async def _ok_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app that returns 200 {"ok": true}."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": b'{"ok": true}'})


# ---------------------------------------------------------------------------
# Test helper: drive the ASGI middleware directly
# ---------------------------------------------------------------------------


TOKEN = "test-bearer-token-abc123"


def _build_scope(
    path: str = "/",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": hdrs,
    }


@pytest.fixture
def middleware():
    return AgentBearerMiddleware(_ok_app, token=TOKEN)


class TestBearerMiddleware:
    """``AgentBearerMiddleware`` enforces bearer-auth on every request."""

    def _call(self, middleware, scope):
        """Run the ASGI middleware and collect responses."""
        responses: list[dict[str, Any]] = []
        state = {"more": True}

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(msg):
            responses.append(msg)
            if msg.get("type") == "http.response.body":
                state["more"] = False

        asyncio.get_event_loop().run_until_complete(middleware(scope, receive, send))
        return responses, state

    def test_200_with_valid_bearer(self, middleware) -> None:
        scope = _build_scope(headers={"authorization": f"Bearer {TOKEN}"})
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 200

    def test_401_without_authorization_header(self, middleware) -> None:
        scope = _build_scope(headers={})
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 401

    def test_401_wrong_token(self, middleware) -> None:
        scope = _build_scope(headers={"authorization": "Bearer wrong-token"})
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 401

    def test_401_bad_scheme(self, middleware) -> None:
        scope = _build_scope(headers={"authorization": f"Basic {TOKEN}"})
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 401

    def test_400_query_token_rejected(self, middleware) -> None:
        scope = _build_scope(
            path="/",
            headers={"authorization": f"Bearer {TOKEN}"},
        )
        scope["query_string"] = b"token=test"
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 400

    def test_403_non_loopback_host(self, middleware) -> None:
        scope = _build_scope(
            headers={
                "authorization": f"Bearer {TOKEN}",
                "host": "evil.example.com",
            }
        )
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 403

    def test_200_loopback_host_127(self, middleware) -> None:
        scope = _build_scope(
            headers={
                "authorization": f"Bearer {TOKEN}",
                "host": "127.0.0.1:12345",
            }
        )
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 200

    def test_200_loopback_host_localhost(self, middleware) -> None:
        scope = _build_scope(
            headers={
                "authorization": f"Bearer {TOKEN}",
                "host": "localhost:12345",
            }
        )
        responses, _ = self._call(middleware, scope)
        start = responses[0]
        assert start["status"] == 200
