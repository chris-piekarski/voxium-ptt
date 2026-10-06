"""MCP listener bootstrap for the opt-in loopback control plane.

Thin adapter between the ``voxium run`` client lifecycle and the MCP SDK.
The SDK is imported lazily so that all pure modules remain untouched when
the ``agent`` extra is not installed.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any

__all__ = ["start_agent_listener", "stop_agent_listener"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-singleton handles
# ---------------------------------------------------------------------------

_listener_thread: Any = None
_listener_sock: socket.socket | None = None


# ---------------------------------------------------------------------------
# Listener startup / shutdown
# ---------------------------------------------------------------------------


def start_agent_listener(config: Any) -> None:
    """Start the opt-in MCP listener on a loopback ephemeral port.

    This is a *best-effort* helper: if any step fails (missing SDK, bind
    error, credential file write failure) the caller is logged neutrally
    and control returns immediately so that PTT still starts.

    Parameters
    ----------
    config:
        The merged CLI / YAML config namespace passed through ``run_client``.
        Expected (safe defaults used when missing):

        * ``agent`` (dict) -- ``enabled``, ``bind_host``, ``port``, ``scopes``,
          ``allow_non_loopback``.
    """
    # -- Lazy imports (mcp extra may be absent) --------------------------------
    try:
        from mcp.server import MCPServer
    except ImportError:
        logger.info("MCP SDK not installed; pass .[agent] to enable the control plane.")
        return

    # -- Resolve config --------------------------------------------------------
    agent_cfg: dict[str, Any] = getattr(config, "agent", None) or {}
    if not agent_cfg.get("enabled", False):
        return

    bind_host: str = agent_cfg.get("bind_host", "127.0.0.1")
    bind_port: int = agent_cfg.get("port", 0)
    scopes: list[str] = agent_cfg.get("scopes") or ["status:read", "transcript:read"]
    allow_non_loopback: bool = bool(agent_cfg.get("allow_non_loopback", False))

    # -- Bind-host policy (loopback only) --------------------------------------
    from voxium.agent_auth import (
        mint_token,
        validate_bind_host,
        write_session_credential,
    )

    host_err = validate_bind_host(bind_host, allow_non_loopback)
    if host_err:
        logger.error("agent listener skipped: %s", host_err)
        return

    # -- SDK log guard (NullHandler on root logger) ----------------------------
    from voxium.agent_logging import install_sdk_log_guard

    install_sdk_log_guard()

    # -- Build MCP server ------------------------------------------------------
    server = MCPServer(name="voxium-agent", version="0.0.1")

    # Register session_status (only tool in PR 4)
    _register_session_status_tool(server)

    # -- Bind socket ourselves (127.0.0.1:0) -----------------------------------
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, bind_port))
        sock.listen(1)
    except OSError as exc:
        logger.error("agent listener bind failed: %s", exc)
        return

    actual_host, actual_port = sock.getsockname()
    server_url = f"http://{actual_host}:{actual_port}/mcp"

    # -- Mint token and write credential file ----------------------------------
    global _listener_thread, _listener_sock

    token = mint_token()
    cred_path = _session_credential_path()

    try:
        write_session_credential(
            path=cred_path,
            url=server_url,
            token=token,
            scopes=scopes,
        )
    except OSError as exc:
        logger.error("agent credential file write failed, skipping listener: %s", exc)
        sock.close()
        return

    # -- Mount streamable-HTTP with bearer middleware --------------------------
    from voxium.agent_http import AgentBearerMiddleware

    inner_app = server.streamable_http_app(json_response=True)
    app = AgentBearerMiddleware(inner_app, token=token)

    # -- Serve on a dedicated thread -------------------------------------------
    _listener_sock = sock
    _listener_thread = threading.Thread(
        target=_serve_uvicorn, args=(app, sock), daemon=True
    )
    _listener_thread.start()

    logger.info(
        "agent listener bound %s:%d  credential %s",
        actual_host,
        actual_port,
        cred_path,
    )


def stop_agent_listener() -> None:
    """Close the listening socket and join the serve thread.

    No-op when the listener was never started.
    """
    global _listener_thread, _listener_sock

    if _listener_sock is not None:
        try:
            _listener_sock.close()
        except OSError:
            pass
        _listener_sock = None

    if _listener_thread is not None and _listener_thread.is_alive():
        _listener_thread.join(timeout=5)
        _listener_thread = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _register_session_status_tool(server: Any) -> None:
    """Register ``session_status`` on *server* (PR 4 only tool)."""

    @server.tool("session_status")
    def session_status() -> dict[str, Any]:
        """Structured snapshot of this client's state."""
        return {
            "state": "idle",
            "input_mode": "ptt",
            "transmission_latest_id": None,
        }


def _serve_uvicorn(app: Any, sock: socket.socket) -> None:
    """Serve *app* via uvicorn on a pre-bound socket."""
    try:
        from uvicorn import Config, Server
    except ImportError:
        logger.error("uvicorn import failed; agent listener will not serve")
        return

    cfg = Config(
        app=app,
        host="127.0.0.1",
        port=0,
        log_level="error",
        factory=False,
    )
    srv = Server(cfg)
    srv._orig_listen_sockets = [sock]  # type: ignore[attr-defined]
    srv.serve()


def _session_credential_path() -> str:
    import pathlib

    return str(pathlib.Path.home() / ".config" / "voxium" / "run" / "agent.json")
