"""Protocol types for the MCP tool error/success envelope.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Closed error code enum
# ---------------------------------------------------------------------------

VALID_ERROR_CODES: frozenset[str] = frozenset(
    {
        "unauthenticated",
        "scope_denied",
        "invalid_arguments",
        "shutting_down",
        "session_busy",
        "wrong_input_mode",
        "hotkey_held",
        "microphone_unavailable",
        "not_recording",
        "nothing_to_retransmit",
        "history_empty",
        "history_index_invalid",
        "not_found",
        "no_target_window",
        "paste_debounced",
        "paste_failed",
        "clipboard_failed",
        "clipboard_too_large",
        "timeout",
        "rate_limited",
        "log_source_unknown",
        "internal",
        "already_recording",  # success flag, listed for completeness
    }
)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolError:
    """Structured error for MCP tool responses."""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """Structured success for MCP tool responses."""

    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": True, "data": self.data}
