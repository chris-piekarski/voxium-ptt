"""MCP listener bootstrap for the opt-in loopback control plane.

Thin adapter between the ``voxium run`` client lifecycle and the MCP SDK.
The SDK is imported lazily so that all pure modules remain untouched when
the ``agent`` extra is not installed.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "get_session_history",
    "get_transmission_log",
    "install_state_callable",
    "start_agent_listener",
    "stop_agent_listener",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-singleton handles
# ---------------------------------------------------------------------------

_listener_thread: Any = None
_listener_sock: socket.socket | None = None

# ---------------------------------------------------------------------------
# session_status live-state callable
# ---------------------------------------------------------------------------

_state_callable: Callable[[], dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Process-wide transmission log (PR 5)
# ---------------------------------------------------------------------------

_transmission_log: Any = None  # lazy import of transmission_log.TransmissionLog
_transmission_wait_active: bool = False  # concurrency cap for transmission_wait
_health_snapshot: Any = None  # cache for debug_health probe
_health_cache_time: float = 0.0  # monotonic timestamp for health cache
_HEALTH_CACHE_TTL: float = 10.0  # seconds


def get_transmission_log() -> Any:
    """Return the process-wide TransmissionLog singleton (lazy import)."""
    global _transmission_log
    if _transmission_log is None:
        from voxium.transmission_log import TransmissionLog

        _transmission_log = TransmissionLog()
    return _transmission_log


def install_state_callable(fn: Callable[[], dict[str, Any]]) -> None:
    """Install a zero-arg callable that ``session_status`` proxies to.

    If not set, the tool returns ``{"state": "unknown", ...}``.
    """
    global _state_callable

    _state_callable = fn


def default_state_callable() -> dict[str, Any]:
    """Fallback when the client has not installed a live callable."""
    return {
        "state": "unknown",
        "input_mode": "ptt",
        "inflight": [],
        "transmission_latest_id": None,
    }


# ---------------------------------------------------------------------------
# Listener startup / shutdown
# ---------------------------------------------------------------------------


def start_agent_listener(
    agent_cfg: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    user_dir: Path | None = None,
) -> None:
    """Start the opt-in MCP listener on a loopback ephemeral port.

    This is a *best-effort* helper: if any step fails (missing SDK, bind
    error, credential file write failure) the caller is logged neutrally
    and control returns immediately so that PTT still starts.

    Parameters
    ----------
    agent_cfg:
        A dict with at least ``enabled`` (bool). Optional keys:
        ``bind_host``, ``port``, ``allow_non_loopback``.
    scopes:
        Granted scopes. Defaults to ``["status:read", "transcript:read"]``.
    user_dir:
        Override for credential-file directory (tests only).
    """
    scopes = scopes or ["status:read", "transcript:read"]

    # -- Lazy imports (mcp extra may be absent) --------------------------------
    try:
        from mcp.server import MCPServer
    except ImportError:
        logger.info("MCP SDK not installed; pass .[agent] to enable the control plane.")
        return

    # -- Resolve config --------------------------------------------------------
    if not bool(agent_cfg.get("enabled", False)):
        return

    bind_host: str = agent_cfg.get("bind_host", "127.0.0.1")
    bind_port: int = agent_cfg.get("port", 0)
    allow_non_loopback: bool = bool(agent_cfg.get("allow_non_loopback", False))

    # -- Bind-host policy (loopback only) --------------------------------------
    from voxium.agent_auth import (
        SessionCredential,
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

    # Register session_status + PR 5 read-only tools
    _register_session_status_tool(server)
    _register_transmission_tools(server)
    _register_history_tools(server)
    _register_debug_tools(server)

    # -- Bind socket ourselves (127.0.0.1:0) -----------------------------------
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    global _listener_thread, _listener_sock

    try:
        sock.bind((bind_host, bind_port))
        sock.listen(1)

        actual_host, actual_port = sock.getsockname()
        server_url = f"http://{actual_host}:{actual_port}/mcp"

        # -- Mint token and write credential file ------------------------------
        token = mint_token()

        cred = SessionCredential(url=server_url, token=token, scopes=scopes)
        write_session_credential(cred, user_dir=user_dir)

        # -- Mount streamable-HTTP with bearer middleware ----------------------
        from voxium.agent_http import AgentBearerMiddleware

        inner_app = server.streamable_http_app(json_response=True)
        app = AgentBearerMiddleware(inner_app, token=token)

        # -- Serve on a dedicated thread (uvicorn) -----------------------------
        _listener_sock = sock
        _listener_thread = threading.Thread(
            target=_serve_uvicorn, args=(app, server, sock), daemon=True
        )
        _listener_thread.start()

        logger.info(
            "agent listener bound %s:%d  credential %s",
            actual_host,
            actual_port,
            str(
                user_dir / "run" / "agent.json"
                if user_dir
                else "~/.config/voxium/run/agent.json"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # Close the socket, log one neutral line without the token, and return.
        try:
            sock.close()
        except OSError:
            pass
        logger.error("agent listener failed to start: %s; PTT continues.", exc)


def stop_agent_listener() -> None:
    """Close the socket, unlink credentials, and join the serve thread.

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

    # Unlink credential file so a stale token is never left on disk.
    try:
        from voxium.agent_auth import unlink_session_credential

        unlink_session_credential()
    except Exception:  # noqa: BLE001, S110
        pass


def _register_session_status_tool(server: Any) -> None:
    """Register ``session_status`` on *server*.

    Returns state, input_mode, inflight records, and latest transmission id.
    """

    @server.tool("session_status")
    def session_status() -> dict[str, Any]:
        """Structured snapshot of this client's state."""
        fn = _state_callable
        base = fn() if fn is not None else default_state_callable()
        # PR 5: merge inflight + latest transmission id
        tlog = get_transmission_log()
        base["inflight"] = list(tlog.get_inflight()) if tlog is not None else []
        base["transmission_latest_id"] = tlog.latest_id() if tlog is not None else None
        return base


# ---------------------------------------------------------------------------
# PR 5: read-only tools (transmission, history, debug)
# ---------------------------------------------------------------------------


def _register_transmission_tools(server: Any) -> None:
    """Register transmission_last, transmission_get, transmission_wait."""

    @server.tool("transmission_last")
    def transmission_last() -> dict[str, Any] | None:
        """Return the most recent transmission record."""
        tlog = get_transmission_log()
        if tlog is None:
            return None
        return tlog.last()

    @server.tool("transmission_get")
    def transmission_get(id: int) -> dict[str, Any] | None:  # type: ignore[override]
        """Return a transmission record by id."""
        tlog = get_transmission_log()
        if tlog is None:
            return None
        return tlog.get(id)

    @server.tool("transmission_wait")
    def transmission_wait(
        id: int, timeout_seconds: float = 30.0
    ) -> dict[str, Any] | None:  # type: ignore[override]
        """Long-poll for a transmission to finish (one-at-a-time concurrency cap)."""
        global _transmission_wait_active
        if _transmission_wait_active:
            return None
        tlog = get_transmission_log()
        if tlog is None:
            return None
        _transmission_wait_active = True
        try:
            return tlog.wait(id, timeout=timeout_seconds)
        finally:
            _transmission_wait_active = False


def _register_history_tools(server: Any) -> None:
    """Register history_list, history_get."""

    @server.tool("history_list")
    def history_list(limit: int = 25, query: str = "") -> list[dict[str, Any]]:  # type: ignore[override]
        """List recent history entries (read-only)."""
        hist = get_session_history()
        if hist is None:
            return []
        entries = list(hist.iter_entries())
        if query:
            q = query.lower()
            entries = [
                e
                for e in entries
                if q in (e.get("text") or e.get("preview") or "").lower()
            ]
        return entries[-limit:] if len(entries) > limit else entries

    @server.tool("history_get")
    def history_get(index: int) -> dict[str, Any] | None:  # type: ignore[override]
        """Return a specific history entry by index."""
        hist = get_session_history()
        if hist is None:
            return None
        try:
            return hist.get_entry(index)
        except (IndexError, KeyError):
            return None


def _register_debug_tools(server: Any) -> None:
    """Register debug_health, debug_stats, debug_disk."""

    def _take_health_snapshot() -> dict[str, Any]:
        """Take a health snapshot (cached for _HEALTH_CACHE_TTL seconds)."""
        global _health_snapshot, _health_cache_time
        now = time.monotonic()
        if (
            _health_snapshot is not None
            and (now - _health_cache_time) < _HEALTH_CACHE_TTL
        ):
            return _health_snapshot
        snapshot: dict[str, Any] = {"healthy": False}
        try:
            import requests

            from voxium.loopback import get_health_url

            resp = requests.get(get_health_url("http://localhost:0"), timeout=3)
            snapshot["healthy"] = resp.status_code == 200
            snapshot["info"] = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001
            snapshot["info"] = None
        _health_snapshot = snapshot
        _health_cache_time = now
        return snapshot

    @server.tool("debug_health")
    def debug_health() -> dict[str, Any]:
        """Structured health snapshot (fixed field list)."""
        snap = _take_health_snapshot()
        return {
            "healthy": snap.get("healthy", False),
            "server_up": snap.get("healthy", False),
            "server_version": (snap.get("info") or {}).get("version", "unknown"),
            "timestamp": (snap.get("info") or {}).get("timestamp", ""),
            "timestamp_utc": (snap.get("info") or {}).get("timestamp_utc", ""),
            "model": (snap.get("info") or {}).get("model", "none"),
            "model_name": (snap.get("info") or {}).get("model_name", "unknown"),
            "polish_server_url": (snap.get("info") or {}).get("polish_server_url", ""),
            "polish_model": (snap.get("info") or {}).get("polish_model", "none"),
            "sample_rate": (snap.get("info") or {}).get("sample_rate", 0),
        }

    @server.tool("debug_stats")
    def debug_stats() -> dict[str, Any]:
        """Structured persistent and server statistics."""
        result: dict[str, Any] = {}
        try:
            from voxium.paths import persistent_stats_path

            p = persistent_stats_path()
            if p.exists():
                import json

                result["persistent"] = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            result["persistent"] = None
        try:
            import requests

            from voxium.loopback import get_health_url

            resp = requests.get(
                get_health_url("http://localhost:0").replace("/health", "/stats"),
                timeout=2,
            )
            result["server"] = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001
            result["server"] = None
        return result

    @server.tool("debug_disk")
    def debug_disk() -> list[dict[str, Any]]:
        """Disk usage report for local data directories."""
        entries: list[dict[str, Any]] = []
        try:
            from voxium.disk_usage_report import (
                VOXIUM_DATA_RELPATHS,
                _dir_size,
                _human_size,
            )
            from voxium.paths import repo_root

            root = repo_root()
            for rel in VOXIUM_DATA_RELPATHS:
                p = root / rel
                size = _dir_size(p) if p.exists() else 0
                entries.append(
                    {
                        "label": rel,
                        "bytes": size,
                        "human": _human_size(size),
                        "present": p.exists(),
                    }
                )
        except Exception:  # noqa: BLE001, S110
            pass
        return entries


# ---------------------------------------------------------------------------
# Process-wide session history getter (PR 5)
# ---------------------------------------------------------------------------


def get_session_history() -> Any | None:
    """Return the process-wide SessionTranscriptHistory singleton (lazy import)."""
    try:
        from voxium.session_history import (
            get_transcript_history,
        )

        return get_transcript_history()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serve_uvicorn(app: Any, server: Any, sock: socket.socket) -> None:
    """Serve *app* via uvicorn on a pre-bound socket inside the MCP lifespan."""
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
    srv.serve(sockets=[sock])
