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
    "_agent_dispatch",
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

# Dispatch instance (set by start_agent_listener, None when listener not active)
_agent_dispatch: Any = None

# ---------------------------------------------------------------------------
# session_status live-state callable
# ---------------------------------------------------------------------------

_state_callable: Callable[[], dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Process-wide transmission log (PR 5)
# ---------------------------------------------------------------------------

_transmission_log: Any = None  # lazy import of transmission_log.TransmissionLog
_transmission_wait_busy: bool = False  # concurrency cap for transmission_wait

# Configured loopback server URL (set by start_agent_listener).
# Handlers read this for health/stats probes; None → return null fields.
_server_url: str | None = None

# Shutdown flag for dispatch layer (set by stop_agent_listener).
_shutting_down: bool = False


class _LazySession:
    """Minimal SessionPort for the dispatch layer.

    Read-only tools are idempotent so the busy-path never fires.
    This class provides the protocol surface for scope and shutting-down checks.
    """

    @property
    def is_shutting_down(self) -> bool:
        return _shutting_down

    # Remaining protocol methods are stubs — never called for read-only tools.
    @property
    def state(self):
        from voxium.agent_dispatch import ClientState

        return ClientState.IDLE

    @property
    def input_mode(self):
        return "ptt"

    @property
    def inflight(self):
        return set()

    @property
    def active_hotkey(self):
        return None

    @property
    def paste_debounce_remaining_ms(self):
        return 0.0

    @property
    def has_pending_audio(self):
        return False

    @property
    def history_count(self):
        return 0

    @property
    def agent_ptt_open(self):
        return False

    def get_active_window(self):
        return None

    def history_text_by_index(self, index: int):
        return None

    def history_list_entries(self, limit: int, query: str):
        return []

    def clipboard_set_text(self, text: str):
        return 0

    def paste_for_agent(self, text: str, window: Any):
        return 0

    def next_replay_cursor_index(self):
        return None

    def advance_replay_cursor(self) -> None:
        pass


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
    server_url: str | None = None,
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
    server_url:
        Configured loopback server URL for health/stats probes.
        ``None`` → handlers skip network and return ``null`` fields.
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

    # -- Build MCP server + dispatch layer -------------------------------------
    server = MCPServer(name="voxium-agent", version="0.0.1")

    # Store configured server URL for health/stats probes
    global _server_url
    _server_url = server_url

    # -- Build read-tool handler map (closures capture _server_url) -------------
    from voxium.agent_dispatch import (
        _HANDLER_MAP,
        AgentDispatch,
    )

    handler_map = _build_read_handler_map()

    # Register our handlers in the dispatch handler registry
    _HANDLER_MAP.update(handler_map)

    # Dispatch: scope checks, rate-limits, idempotent busy-gate.
    # Read-only tools are idempotent so busy-path never fires.
    # Scope set controls which scopes are granted; no FakeSessionPort used.
    dispatch = AgentDispatch(
        session=_LazySession(),
        granted_scopes=set(scopes),
    )
    _agent_dispatch = dispatch

    # -- Register MCP tools (scope-gated) routed through dispatch ---------------
    _register_tools_via_dispatch(server, dispatch)

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
    global _listener_thread, _listener_sock, _server_url, _shutting_down

    # Signal dispatch to reject new tool calls
    _shutting_down = True

    if _listener_sock is not None:
        try:
            _listener_sock.close()
        except OSError:
            pass
        _listener_sock = None

    if _listener_thread is not None and _listener_thread.is_alive():
        _listener_thread.join(timeout=5)
        _listener_thread = None

    # Clear server URL so stale probes never fire after shutdown
    _server_url = None

    # Unlink credential file so a stale token is never left on disk.
    try:
        from voxium.agent_auth import unlink_session_credential

        unlink_session_credential()
    except Exception:  # noqa: BLE001, S110
        pass


# ---------------------------------------------------------------------------
# Read-tool handler map (populated by _build_read_handler_map)
# ---------------------------------------------------------------------------

_DEBUG_HEALTH_DEFAULTS: dict[str, Any] = {
    "device": "unknown",
    "compute": "none",
    "vad_enabled": False,
    "loaded_transcribe_models": [],
    "polish_enabled_default": False,
    "polish_llama_cpp_reachable": False,
    "polish_loaded_model": "none",
    "streaming_enabled": False,
    "streaming_open_sessions": 0,
    "cuda_device_name": "",
    "status": "unknown",
}


def _rec_to_dict(rec) -> dict[str, Any] | None:
    """Convert a TransmissionRecord to a strict JSON dict."""
    if rec is None:
        return None
    return {
        "id": rec.id,
        "state": rec.state,
        "transcript": rec.transcript,
        "filtered": rec.filtered,
        "paste_ok": rec.paste_ok,
        "paste_error": rec.paste_error,
        "duration_ms": rec.duration_ms,
        "model": rec.model,
    }


def _build_read_handler_map() -> dict[str, Callable]:
    """Build _HANDLER_MAP entries for read-only tools.

    Each handler receives ``(dispatch, raw_input)`` and returns a dict
    (or None for not-found). Scope, rate-limit, and busy checks are
    enforced by the dispatch layer *before* the handler runs.
    """

    def _handler_session_status(_session: Any, _ri: dict) -> dict[str, Any]:
        fn = _state_callable
        base = fn() if fn is not None else default_state_callable()
        # inflight derives from dispatch-layer sessions (retransmit / slash_apply),
        # not from tlog.get_inflight() — read-only tools never populate inflight
        base["inflight"] = []
        tlog = get_transmission_log()
        base["transmission_latest_id"] = tlog.latest_id() if tlog is not None else None
        return base

    def _handler_transmission_last(_session: Any, _ri: dict) -> dict[str, Any] | None:
        tlog = get_transmission_log()
        rec = tlog.last() if tlog is not None else None
        return _rec_to_dict(rec) if rec is not None else None

    def _handler_transmission_get(_session: Any, ri: dict) -> dict[str, Any] | None:
        tid = ri.get("id")
        if tid is None:
            return None
        tlog = get_transmission_log()
        if tlog is None:
            return None
        rec = tlog.get(tid)
        if rec is None:
            from voxium.agent_protocol import ToolError

            return ToolError(
                code="not_found",
                message=f"Transmission id {tid} not found.",
            ).to_dict()
        return _rec_to_dict(rec)

    def _handler_transmission_wait(_session: Any, ri: dict) -> dict[str, Any] | None:
        global _transmission_wait_busy
        tid = ri.get("id")
        timeout = ri.get("timeout_seconds", 30.0)
        if _transmission_wait_busy:
            from voxium.agent_protocol import ToolError

            return ToolError(
                code="session_busy",
                message="Another transmission_wait is already in progress.",
                retryable=True,
                details={"reason": "transmission_wait"},
            ).to_dict()
        tlog = get_transmission_log()
        if tlog is None or tid is None:
            return None
        # Return not_found if tid doesn't exist yet
        if not tlog.get(tid):
            from voxium.agent_protocol import ToolError

            return ToolError(
                code="not_found",
                message=f"Transmission id {tid} not found.",
            ).to_dict()
        _transmission_wait_busy = True
        try:
            rec = tlog.wait(tid, timeout=timeout)
            if rec is not None:
                return _rec_to_dict(rec)
            # Timeout: tlog.wait returned None
            from voxium.agent_protocol import ToolError

            return ToolError(
                code="timeout",
                message=f"Transmission id {tid} timed out after {timeout}s.",
                retryable=True,
            ).to_dict()
        finally:
            _transmission_wait_busy = False

    def _handler_history_list(_session: Any, ri: dict) -> dict[str, Any]:
        limit = ri.get("limit", 25)
        query = ri.get("query", "")
        hist = get_session_history()
        if hist is None:
            return {"entries": [], "total": 0}
        # Delegate limit and query filtering to iter_entries
        entries = list(hist.iter_entries(limit=limit, query=query))
        total = len(entries)
        return {"entries": entries, "total": total}

    def _handler_history_get(_session: Any, ri: dict) -> dict[str, Any] | None:
        index = ri.get("index")
        if index is None:
            return None
        hist = get_session_history()
        if hist is None:
            return None
        try:
            return hist.get_entry(index)
        except (IndexError, KeyError):
            return None

    def _take_health_snapshot() -> dict[str, Any]:
        """Take a health snapshot (cached).

        Uses the configured server URL. Returns null info when unavailable.
        Never opens a socket with localhost:0.
        """
        now = time.monotonic()
        url = _server_url
        if url is None:
            return {"healthy": False, "info": None}
        # Simple cache key: url + ttl
        cache_key = f"{url}:{int(now // 10)}"
        cached = getattr(_take_health_snapshot, "_cache", None)
        if cached and cached[0] == cache_key:
            return cached[1]
        snapshot: dict[str, Any] = {"healthy": False}
        health_url = url + "/health"
        try:
            import requests

            resp = requests.get(health_url, timeout=3)
            snapshot["healthy"] = resp.status_code == 200
            snapshot["info"] = resp.json() if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001
            snapshot["info"] = None
        _take_health_snapshot._cache = (cache_key, snapshot)  # type: ignore[attr-defined]
        return snapshot

    # Strict field list for debug_health — no raw model names, no raw port.
    _debug_health_fields = frozenset(
        {
            "healthy",
            "server_up",
            "status",
            "device",
            "compute",
            "vad_enabled",
            "loaded_transcribe_models",
            "polish_enabled_default",
            "polish_llama_cpp_reachable",
            "polish_loaded_model",
            "streaming_enabled",
            "streaming_open_sessions",
            "cuda_device_name",
        }
    )

    def _handler_debug_health(_session: Any, _ri: dict) -> dict[str, Any]:
        snap = _take_health_snapshot()
        info = snap.get("info") or {}
        result: dict[str, Any] = {}
        for key in _debug_health_fields:
            if key in ("healthy", "server_up"):
                result[key] = snap.get("healthy", False)
            elif key == "status":
                result[key] = "healthy" if snap.get("healthy") else "degraded"
            else:
                result[key] = info.get(key, _DEBUG_HEALTH_DEFAULTS.get(key))
        return result

    def _handler_debug_stats(_session: Any, _ri: dict) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            from voxium.persistent_stats import config_stats_path

            p = config_stats_path()
            if p.exists():
                import json

                result["persistent"] = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            result["persistent"] = None
        try:
            import requests

            url = _server_url
            if url:
                resp = requests.get(url + "/stats", timeout=2)
                result["server"] = resp.json() if resp.status_code == 200 else None
            else:
                result["server"] = None
        except Exception:  # noqa: BLE001
            result["server"] = None
        return result

    def _handler_debug_disk(_session: Any, _ri: dict) -> list[dict[str, Any]]:
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

    return {
        "session_status": _handler_session_status,
        "transmission_last": _handler_transmission_last,
        "transmission_get": _handler_transmission_get,
        "transmission_wait": _handler_transmission_wait,
        "history_list": _handler_history_list,
        "history_get": _handler_history_get,
        "debug_health": _handler_debug_health,
        "debug_stats": _handler_debug_stats,
        "debug_disk": _handler_debug_disk,
    }


def _register_tools_via_dispatch(server: Any, dispatch: Any) -> None:
    """Register MCP tools that route through AgentDispatch.dispatch.

    Each tool handler extracts *tool_name* and *raw_input*, calls
    ``dispatch.dispatch(tool_name, raw_input)``, and returns the
    ``data`` payload. Errors are raised so the MCP SDK returns
    ``isError: true`` to the client.
    """

    def _call_dispatch(tool_name: str, raw_input: dict) -> Any:
        """
        Call dispatch.dispatch and unwrap the result.

        Dispatch may return:
        - A ToolError envelope (``{"ok": False, "error": {...}}``) → raise
        - The raw handler return value (dict, list, None) → pass through
        """
        result = dispatch.dispatch(tool_name, raw_input)
        # ToolError envelopes have ``ok: False`` — everything else is success.
        if isinstance(result, dict) and not result.get("ok", True):
            err = result.get("error", {})
            msg = (
                err.get("message", err.get("code", "internal"))
                if isinstance(err, dict)
                else "internal"
            )
            raise RuntimeError(f"tool error: {msg}")
        return result

    # session_status — no args
    @server.tool("session_status")
    def tool_session_status() -> dict[str, Any]:
        return _call_dispatch("session_status", {})

    # transmission_last — no args
    @server.tool("transmission_last")
    def tool_transmission_last() -> dict[str, Any] | None:
        return _call_dispatch("transmission_last", {})

    # transmission_get — id
    @server.tool("transmission_get")
    def tool_transmission_get(id: int) -> dict[str, Any] | None:
        return _call_dispatch("transmission_get", {"id": id})

    # transmission_wait — id, timeout_seconds
    @server.tool("transmission_wait")
    def tool_transmission_wait(
        id: int, timeout_seconds: float = 30.0
    ) -> dict[str, Any] | None:
        return _call_dispatch(
            "transmission_wait", {"id": id, "timeout_seconds": timeout_seconds}
        )

    # history_list — limit, query
    @server.tool("history_list")
    def tool_history_list(limit: int = 25, query: str = "") -> dict[str, Any]:
        return _call_dispatch("history_list", {"limit": limit, "query": query})

    # history_get — index
    @server.tool("history_get")
    def tool_history_get(index: int) -> dict[str, Any] | None:
        return _call_dispatch("history_get", {"index": index})

    # debug_health — no args
    @server.tool("debug_health")
    def tool_debug_health() -> dict[str, Any]:
        return _call_dispatch("debug_health", {})

    # debug_stats — no args
    @server.tool("debug_stats")
    def tool_debug_stats() -> dict[str, Any]:
        return _call_dispatch("debug_stats", {})

    # debug_disk — no args
    @server.tool("debug_disk")
    def tool_debug_disk() -> list[dict[str, Any]]:
        return _call_dispatch("debug_disk", {})


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
    import asyncio

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
    asyncio.run(srv.serve(sockets=[sock]))
