"""Streamable-HTTP middleware for the MCP control-plane listener.

Pure module: no MCP SDK import, no network, no microphone.

Provides bearer-token validation via :func:`agent_auth.compare_tokens` and
query-string token rejection.  Tests target a fake ASGI app so that the
middleware can be exercised without the MCP SDK.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

from voxium.agent_auth import (
    compare_tokens,
    parse_bearer,
)

__all__ = ["AgentBearerMiddleware"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed loopback hosts
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class AgentBearerMiddleware:
    """ASGI middleware that enforces bearer-auth on the MCP control plane.

    * **401** when ``Authorization`` is missing or the bearer token does
      not match the session credential.
    * **400** when the request URL carries a ``token=`` query param
      (query-string tokens are rejected outright).
    * **403** when the ``Host`` header resolves to a non-loopback address.

    Matching requests are forwarded to *inner* (the wrapped ASGI app).

    Parameters
    ----------
    app:
        The inner ASGI callable (e.g. the streamable-HTTP mount).
    token:
        The bearer token written into ``~/.config/voxium/run/agent.json``.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    # ------------------------------------------------------------------
    # ASGI interface
    # ------------------------------------------------------------------

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # -- Query-string token rejection --------------------------------
        _qs = parse_qs(urlparse(scope.get("path", "")).query)
        if "token" in _qs:
            await _respond_json(
                send, 400, {"detail": "Query-string tokens are not accepted"}
            )
            return

        # -- Bearer token validation -------------------------------------
        headers = dict(scope.get("headers", []))
        auth_value = _get_header(headers, b"authorization")
        if not auth_value:
            await _respond_json(send, 401, {"detail": "Missing Authorization header"})
            return

        result = parse_bearer(auth_value)
        if result.error is not None or not compare_tokens(result.token, self._token):
            await _respond_json(send, 401, {"detail": "Invalid bearer token"})
            return

        # -- Host / loopback guard ---------------------------------------
        host_value = _get_header(headers, b"host")
        if host_value:
            host, _port = _split_host_port(host_value)
            if host and host not in _LOOPBACK_HOSTS:
                await _respond_json(
                    send, 403, {"detail": f"Non-loopback host rejected: {host!r}"}
                )
                return

        await self._app(scope, receive, send)


# ---------------------------------------------------------------------------
# Helpers (pure, testable)
# ---------------------------------------------------------------------------


def _get_header(headers: dict[bytes, bytes], key: bytes) -> str | None:
    val = headers.get(key)
    if val is None:
        return None
    return val.decode("utf-8", errors="replace")


def _split_host_port(value: str) -> tuple[str, int | None]:
    """Best-effort host:port split (no socket resolution)."""
    if not value:
        return "", None
    parts = value.rsplit(":", 1)
    host = parts[0] or ""
    port: int | None = None
    if len(parts) == 2:
        try:
            port = int(parts[1])
        except ValueError:
            pass
    return host, port


async def _respond_json(send: Any, status_code: int, body: dict[str, Any]) -> None:
    """Send a minimal JSON error response (ASGI interface)."""
    content = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": content})
