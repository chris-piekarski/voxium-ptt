"""Dispatch layer: rate limiting, scope gates, and the SessionPort protocol.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Protocol

from voxium.agent_protocol import ToolError, ToolResult
from voxium.agent_tools import (
    TOOLS_BY_NAME,
    ToolDefinition,
    filter_tools_by_scopes,
)

# ---------------------------------------------------------------------------
# Client states (mirrors app.py State enum without importing it)
# ---------------------------------------------------------------------------


class ClientState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    COMMAND_INPUT = "command_input"


# ---------------------------------------------------------------------------
# Inflight flags
# ---------------------------------------------------------------------------


class InflightFlag(str, Enum):
    RETRANSMIT = "retransmit"
    SLASH_APPLY = "slash_apply"


# ---------------------------------------------------------------------------
# SessionPort protocol — the real client or a test fake
# ---------------------------------------------------------------------------


class SessionPort(Protocol):
    """Minimal interface the dispatch layer needs from the running client."""

    @property
    def state(self) -> ClientState: ...
    @property
    def input_mode(self) -> str: ...  # "ptt" | "vox"
    @property
    def inflight(self) -> set[InflightFlag]: ...
    @property
    def active_hotkey(self) -> str | None: ...
    @property
    def paste_debounce_remaining_ms(self) -> float: ...
    @property
    def has_pending_audio(self) -> bool: ...
    @property
    def history_count(self) -> int: ...
    @property
    def is_shutting_down(self) -> bool: ...
    @property
    def agent_ptt_open(self) -> bool: ...  # ptt_engage was accepted; release is exempt

    def get_active_window(self) -> Any: ...
    def history_text_by_index(self, index: int) -> str | None: ...
    def history_list_entries(self, limit: int, query: str) -> list[dict[str, Any]]: ...
    def clipboard_set_text(self, text: str) -> int: ...
    def paste_for_agent(self, text: str, window: Any) -> int: ...
    def next_replay_cursor_index(self) -> tuple[int, str] | None: ...
    def advance_replay_cursor(self) -> None: ...


# ---------------------------------------------------------------------------
# Rate limiter — token bucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """
    Simple sliding-window token bucket.

    *capacity*: max tokens.  *refill_per_second*: tokens added each second.
    ``acquire()`` returns ``True`` if a token is available, ``False`` otherwise.
    """

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self._capacity = capacity
        self._refill = refill_per_second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> tuple[bool, int]:
        """
        Try to consume one token.

        Returns ``(ok, retry_after_ms)``.  When ``ok`` is ``False``,
        ``retry_after_ms`` estimates how long to wait.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill,
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0
            # Estimate recovery time
            needed = 1.0 - self._tokens
            if self._refill <= 0:
                ms = 1000  # fallback when refill is disabled
            else:
                ms = int((needed / self._refill) * 1000) + 1
            return False, ms


# Mutable exemption state is exposed via the session's agent_ptt_open
# property; dispatch reads it from session to determine rate-exemptions.

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class AgentDispatch:
    """
    Route MCP tool calls through auth, scope, rate-limit, and session-busy checks.
    """

    # Rate-limit buckets
    _mutable_bucket = TokenBucket(capacity=2, refill_per_second=2.0)
    _read_bucket = TokenBucket(capacity=20, refill_per_second=20.0)

    def __init__(
        self,
        session: SessionPort,
        granted_scopes: set[str] | frozenset[str],
    ) -> None:
        self._session = session
        self._granted = set(granted_scopes)

    # ---- Public API ----

    def tool_list(
        self, requested_scopes: set[str] | None = None
    ) -> list[ToolDefinition]:
        """Return available tools filtered by granted scopes (and optionally extra filter)."""
        tools = filter_tools_by_scopes(self._granted)
        if requested_scopes:
            further = filter_tools_by_scopes(set(self._granted) & requested_scopes)
            return list(further)
        return list(tools)

    def dispatch(self, tool_name: str, raw_input: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a tool call and return the JSON envelope.

        This method handles:
        - Rate limiting (with exemptions)
        - Scope checks via :meth:`_check_scope`
        - Shutting-down guard
        - Session busy / state checks
        - JSON schema validation
        - Routing to the handler function
        """
        tool_def = TOOLS_BY_NAME.get(tool_name)
        if tool_def is None:
            return ToolError(
                code="invalid_arguments",
                message=f"Unknown tool: {tool_name}",
            ).to_dict()

        # --- Scope gate ---
        if not tool_def.scopes.issubset(self._granted):
            return ToolError(
                code="scope_denied",
                message=f"Insufficient scope for tool '{tool_name}'.",
            ).to_dict()

        # --- Shutting down (before idempotent success) ---
        if self._session.is_shutting_down:
            return ToolError(
                code="shutting_down",
                message="Client is shutting down.",
            ).to_dict()

        # --- Idempotent success: ptt_engage while RECORDING ---
        # After scope + shutting_down so an unscoped caller gets scope_denied
        # even when already recording.  Before rate-limit/busy so no tokens
        # are consumed and no start-recording hook fires.
        if tool_name == "ptt_engage" and self._session.state == ClientState.RECORDING:
            return ToolResult(data={"ok": True, "already_recording": True}).to_dict()

        # --- Validate input against schema ---
        schema_error = _validate_input(raw_input, tool_def.input_schema)
        if schema_error:
            return schema_error.to_dict()

        # --- Rate limiting ---
        is_mutation = not tool_def.idempotent
        is_read = tool_def.idempotent

        if is_mutation and not _is_rate_exempt(tool_name, self._session, raw_input):
            ok, retry_ms = self._mutable_bucket.acquire()
            if not ok:
                return ToolError(
                    code="rate_limited",
                    message="Too many mutating requests.",
                    retryable=True,
                    details={"retry_after_ms": retry_ms},
                ).to_dict()
        elif is_read:
            ok, retry_ms = self._read_bucket.acquire()
            if not ok:
                return ToolError(
                    code="rate_limited",
                    message="Too many read requests.",
                    retryable=True,
                    details={"retry_after_ms": retry_ms},
                ).to_dict()

        # --- Busy / state checks for mutating tools ---
        if is_mutation:
            busy = self._check_session_busy(tool_name)
            if busy:
                return busy.to_dict()

            # Hotkey held? (applies to: ptt_engage, paste_history, retransmit_last,
            # input_mode_set — ptt_release returns early from busy check)
            if (
                tool_name
                in {
                    "ptt_engage",
                    "paste_history",
                    "retransmit_last",
                    "input_mode_set",
                }
                and self._session.active_hotkey is not None
            ):
                return ToolError(
                    code="hotkey_held",
                    message="A hotkey is currently held by the operator.",
                    retryable=True,
                ).to_dict()

            # Paste debounce — only for paste_history
            if (
                tool_name == "paste_history"
                and self._session.paste_debounce_remaining_ms > 0
            ):
                return ToolError(
                    code="paste_debounced",
                    message="Paste is debounced; try again shortly.",
                    retryable=True,
                    details={
                        "retry_after_ms": int(self._session.paste_debounce_remaining_ms)
                    },
                ).to_dict()

        # --- Routing (handlers are wired per-tool) ---
        return self._route(tool_def, raw_input)

    # ---- Internal ----

    def _route(
        self,
        tool_def: ToolDefinition,
        raw_input: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Route to the specific tool handler.

        In PR 2 this returns ``internal`` errors — real handlers are wired
        in later PRs via the ``SessionPort`` implementation.
        """
        try:
            handler = _HANDLER_MAP.get(tool_def.name)
            if handler is None:
                return ToolError(
                    code="internal",
                    message=f"Tool '{tool_def.name}' is not yet implemented.",
                ).to_dict()
            return handler(self._session, raw_input)
        except Exception as exc:  # noqa: BLE001 - surface as tool error
            return ToolError(
                code="internal",
                message=f"Tool execution failed: {exc}",
            ).to_dict()

    def _check_session_busy(self, tool_name: str) -> ToolError | None:
        """
        Return a ``session_busy`` error if the client state is not IDLE or
        if an inflight flag is set.

        Special cases:
        - ``ptt_engage`` while ``RECORDING`` is handled earlier (early success).
        - ``ptt_release`` while not ``RECORDING`` → ``not_recording``.
        - Tools requiring ``output:paste``, ``input:vox``, ``session:retransmit``
          are refused while ``RECORDING`` (``details.state: recording``).

        ``reason`` is one of: ``state``, ``retransmit``, ``slash_apply``.
        When ``reason`` is ``state``, the actual state value is included.
        ``reason: inflight`` is never used.

        Inflight flags determine ``reason``:
        - ``RETRANSMIT`` / ``RETRANSMIT_LAST`` → ``reason: retransmit``
        - ``SLASH_APPLY`` → ``reason: slash_apply``
        - anything else → ``reason: state``
        """
        idle_states = {ClientState.IDLE}

        def _inflight_reason() -> str:
            """Derive reason from inflight flags."""
            for flag in self._session.inflight:
                name = getattr(flag, "name", str(flag)).lower()
                if "retransmit" in name:
                    return "retransmit"
                if "slash" in name:
                    return "slash_apply"
            return "state"

        if tool_name == "ptt_engage":
            # Already handled earlier (RECORDING → early success).
            if self._session.state not in idle_states:
                return ToolError(
                    code="session_busy",
                    message="Session is busy.",
                    retryable=True,
                    details={"reason": "state", "state": self._session.state.value},
                )
            if self._session.inflight:
                return ToolError(
                    code="session_busy",
                    message="Session is busy.",
                    retryable=True,
                    details={
                        "reason": _inflight_reason(),
                        "state": self._session.state.value,
                    },
                )
            return None
        if tool_name == "ptt_release":
            return None  # Release is handled in the tool handler, not here
        if self._session.state == ClientState.RECORDING:
            return ToolError(
                code="session_busy",
                message="Cannot perform action while recording.",
                retryable=True,
                details={"reason": "state", "state": self._session.state.value},
            )
        # General idle/inflight check
        if self._session.state not in idle_states:
            return ToolError(
                code="session_busy",
                message="Session is busy.",
                retryable=True,
                details={"reason": "state", "state": self._session.state.value},
            )
        if self._session.inflight:
            return ToolError(
                code="session_busy",
                message="Session is busy.",
                retryable=True,
                details={
                    "reason": _inflight_reason(),
                    "state": self._session.state.value,
                },
            )
        return None


def _is_rate_exempt(
    tool_name: str,
    session: SessionPort,
    params: dict[str, Any],
) -> bool:
    """
    Certain mutations are exempt from the rate limiter.

    - ``ptt_release`` is free while ``session.agent_ptt_open`` is True.
    - ``input_mode_set`` with ``mode: ptt`` is free while ``input_mode == vox``
      (disarm sequence).
    """
    if tool_name == "ptt_release":
        return bool(session.agent_ptt_open)
    if tool_name == "input_mode_set":
        return session.input_mode == "vox" and params.get("mode") == "ptt"
    return False


# ---------------------------------------------------------------------------
# Fake SessionPort for unit tests
# ---------------------------------------------------------------------------


class FakeSessionPort:
    """
    In-memory implementation of :class:`SessionPort` for tests.

    No network, no mic. All state is set via attributes.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.state: ClientState = kwargs.get("state", ClientState.IDLE)
        self.input_mode: str = kwargs.get("input_mode", "ptt")
        self.inflight: set[InflightFlag] = set(kwargs.get("inflight", []))
        self.active_hotkey: str | None = kwargs.get("active_hotkey", None)
        self.paste_debounce_remaining_ms: float = kwargs.get(
            "paste_debounce_remaining_ms", 0
        )
        self.has_pending_audio: bool = kwargs.get("has_pending_audio", False)
        self.history_count: int = kwargs.get("history_count", 0)
        self.is_shutting_down: bool = kwargs.get("is_shutting_down", False)
        self.agent_ptt_open: bool = kwargs.get("agent_ptt_open", False)
        self._history_items: list[str] = kwargs.get("_history_items", [])
        self._window: Any = kwargs.get("_window", None)
        self._clipboard_writes: list[str] = []
        self._paste_calls: list[tuple[str, Any]] = []
        self._replay_cursor: int = 0
        self._mode_writes: list[str] = []
        self._cursor_advance_count: int = 0

    def get_active_window(self) -> Any:
        return self._window

    def history_text_by_index(self, index: int) -> str | None:
        if not self._history_items or index < 1 or index > len(self._history_items):
            return None
        return self._history_items[-index]  # 1 = newest

    def history_list_entries(
        self, limit: int = 25, query: str = ""
    ) -> list[dict[str, Any]]:
        items = self._history_items[::-1]  # newest first
        if query:
            q = query.lower()
            items = [i for i in items if q in i.lower()]
        entries = []
        for idx, text in enumerate(items[:limit], start=1):
            entries.append(
                {
                    "index": idx,
                    "source": "ptt",
                    "preview": text[:100].replace("\n", " "),
                    "chars": len(text),
                }
            )
        return entries

    def clipboard_set_text(self, text: str) -> int:
        self._clipboard_writes.append(text)
        return len(text)

    def paste_for_agent(self, text: str, window: Any) -> int:
        if window is None:
            return -1  # paste cannot proceed without a target
        self._paste_calls.append((text, window))
        return 0  # success

    def next_replay_cursor_index(self) -> tuple[int, str] | None:
        if not self._history_items:
            return None
        idx = self._replay_cursor % len(self._history_items)
        return (idx + 1, self._history_items[len(self._history_items) - 1 - idx])

    def advance_replay_cursor(self) -> None:
        self._cursor_advance_count += 1
        self._replay_cursor += 1

    def set_input_mode(self, mode: str) -> None:
        self._mode_writes.append(mode)
        self.input_mode = mode


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _validate_input(
    raw: dict[str, Any],
    schema: dict[str, Any],
) -> ToolError | None:
    """
    Validate *raw* against the tool's input schema.

    Returns a :class:`ToolError` on failure, ``None`` on success.
    Rejects unknown fields when ``additionalProperties`` is ``False``.
    """
    if not isinstance(raw, dict):
        return ToolError(
            code="invalid_arguments",
            message="Input must be a JSON object.",
        )

    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    additional_ok = schema.get("additionalProperties", True)

    # Check required fields
    for key in required_set:
        if key not in raw:
            return ToolError(
                code="invalid_arguments",
                message=f"Missing required field: {key}",
            )

    # Check unexpected fields
    if not additional_ok:
        known = set(properties.keys())
        extras = set(raw.keys()) - known
        if extras:
            return ToolError(
                code="invalid_arguments",
                message=f"Unknown fields: {', '.join(sorted(extras))}",
            )

    # Basic type checks
    for key, val in raw.items():
        prop_schema = properties.get(key)
        if prop_schema and _type_mismatch(val, prop_schema.get("type")):
            return ToolError(
                code="invalid_arguments",
                message=f"Field '{key}' has wrong type (expected {prop_schema['type']}).",
            )

    return None


def _type_mismatch(value: Any, expected_type: str | None) -> bool:
    """Check whether *value* matches the JSON schema *expected_type*."""
    if expected_type is None:
        return False
    type_map: dict[str, type[Any] | Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected = type_map.get(expected_type)
    if expected is None:
        return False
    # bool is subclass of int in Python; special case
    if expected_type == "integer" and isinstance(value, bool):
        return True
    if expected_type == "number" and isinstance(value, bool):
        return True
    return not isinstance(value, expected)


# ---------------------------------------------------------------------------
# Handler registry (empty in PR 2 — real wiring in later PRs)
# ---------------------------------------------------------------------------

_HANDLER_MAP: dict[str, Any] = {}
