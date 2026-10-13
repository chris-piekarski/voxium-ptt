"""Declarative MCP tool catalog and JSON-schema definitions.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Known scopes
# ---------------------------------------------------------------------------

ALL_SCOPES: frozenset[str] = frozenset(
    {
        "status:read",
        "transcript:read",
        "clipboard:write",
        "output:paste",
        "input:ptt",
        "input:vox",
        "session:retransmit",
        "debug:read",
        "logs:read",
    }
)

DEFAULT_SCOPES: list[str] = ["status:read", "transcript:read"]


# ---------------------------------------------------------------------------
# Individual tool definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDefinition:
    """Declarative description of one MCP tool."""

    name: str
    description: str
    scopes: frozenset[str]
    input_schema: dict[str, Any]
    idempotent: bool = False

    # JSON schema properties for the input object
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)


def _base_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    props = properties or {}
    req = required or []
    schema: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if req:
        schema["required"] = req
    return schema


# ---- Read-only tools ----

TOOL_SESSION_STATUS = ToolDefinition(
    name="session_status",
    description="Structured snapshot of this client's state.",
    scopes=frozenset({"status:read"}),
    input_schema=_base_schema(),
    idempotent=True,
)

TOOL_TRANSMISSION_LAST = ToolDefinition(
    name="transmission_last",
    description="Return the most recent transmission record.",
    scopes=frozenset({"transcript:read"}),
    input_schema=_base_schema(),
    idempotent=True,
)

TOOL_TRANSMISSION_GET = ToolDefinition(
    name="transmission_get",
    description="Return a transmission record by id.",
    scopes=frozenset({"transcript:read"}),
    input_schema=_base_schema(
        properties={"id": {"type": "integer"}},
        required=["id"],
    ),
    idempotent=True,
)

TOOL_TRANSMISSION_WAIT = ToolDefinition(
    name="transmission_wait",
    description="Long-poll for a transmission to finish.",
    scopes=frozenset({"transcript:read"}),
    input_schema=_base_schema(
        properties={
            "id": {"type": "integer"},
            "timeout_seconds": {"type": "number", "default": 30},
        },
        required=["id"],
    ),
    idempotent=True,
)

TOOL_HISTORY_LIST = ToolDefinition(
    name="history_list",
    description="List recent history entries (read-only).",
    scopes=frozenset({"transcript:read"}),
    input_schema=_base_schema(
        properties={
            "limit": {"type": "integer", "default": 25},
            "query": {"type": "string", "default": ""},
        },
    ),
    idempotent=True,
)

TOOL_HISTORY_GET = ToolDefinition(
    name="history_get",
    description="Return a specific history entry by index.",
    scopes=frozenset({"transcript:read"}),
    input_schema=_base_schema(
        properties={"index": {"type": "integer"}},
        required=["index"],
    ),
    idempotent=True,
)

# ---- Clipboard tools ----

TOOL_CLIPBOARD_SET = ToolDefinition(
    name="clipboard_set",
    description="Place text on the system clipboard.",
    scopes=frozenset({"clipboard:write"}),
    input_schema=_base_schema(
        properties={"text": {"type": "string"}},
        required=["text"],
    ),
)

TOOL_HISTORY_COPY = ToolDefinition(
    name="history_copy",
    description="Copy a history entry to the clipboard.",
    scopes=frozenset({"clipboard:write", "transcript:read"}),
    input_schema=_base_schema(
        properties={"index": {"type": "integer"}},
        required=["index"],
    ),
    idempotent=True,
)

# ---- Paste tools ----

TOOL_PASTE_HISTORY = ToolDefinition(
    name="paste_history",
    description="Inject one history line or advance the F8 replay cycle.",
    scopes=frozenset({"output:paste", "transcript:read"}),
    input_schema=_base_schema(
        properties={
            "index": {"type": "integer"},
            "cycle": {"type": "boolean"},
        },
    ),
)

# ---- PTT tools ----

TOOL_PTT_ENGAGE = ToolDefinition(
    name="ptt_engage",
    description="Press (engage) the PTT key to start recording.",
    scopes=frozenset({"input:ptt"}),
    input_schema=_base_schema(),
)

TOOL_PTT_RELEASE = ToolDefinition(
    name="ptt_release",
    description="Release the PTT key to stop recording and start transcription.",
    scopes=frozenset({"input:ptt"}),
    input_schema=_base_schema(),
)

# ---- VOX tools ----

TOOL_INPUT_MODE_SET = ToolDefinition(
    name="input_mode_set",
    description="Switch between PTT and VOX input modes.",
    scopes=frozenset({"input:vox"}),
    input_schema=_base_schema(
        properties={
            "mode": {"type": "string", "enum": ["ptt", "vox"]},
        },
        required=["mode"],
    ),
)

# ---- Retransmit tools ----

TOOL_RETRANSMIT_LAST = ToolDefinition(
    name="retransmit_last",
    description="Retransmit the last audio capture (F6 retry).",
    scopes=frozenset({"session:retransmit"}),
    input_schema=_base_schema(),
)

# ---- Debug tools ----

TOOL_DEBUG_HEALTH = ToolDefinition(
    name="debug_health",
    description="Structured health snapshot (JSON).",
    scopes=frozenset({"debug:read"}),
    input_schema=_base_schema(),
    idempotent=True,
)

TOOL_DEBUG_STATS = ToolDefinition(
    name="debug_stats",
    description="Structured persistent and server statistics.",
    scopes=frozenset({"debug:read"}),
    input_schema=_base_schema(),
    idempotent=True,
)

TOOL_DEBUG_DISK = ToolDefinition(
    name="debug_disk",
    description="Disk usage report for local data directories.",
    scopes=frozenset({"debug:read"}),
    input_schema=_base_schema(),
    idempotent=True,
)

TOOL_DEBUG_LOGS = ToolDefinition(
    name="debug_logs",
    description="Tail allowlisted log files and the in-memory client ring.",
    scopes=frozenset({"logs:read"}),
    input_schema=_base_schema(
        properties={
            "source": {
                "type": "string",
                "enum": ["server", "llama", "client"],
            },
            "max_lines": {"type": "integer", "default": 100},
        },
        required=["source"],
    ),
    idempotent=True,
)

# ---------------------------------------------------------------------------
# Full catalog
# ---------------------------------------------------------------------------

ALL_TOOLS: tuple[ToolDefinition, ...] = (
    TOOL_SESSION_STATUS,
    TOOL_TRANSMISSION_LAST,
    TOOL_TRANSMISSION_GET,
    TOOL_TRANSMISSION_WAIT,
    TOOL_HISTORY_LIST,
    TOOL_HISTORY_GET,
    TOOL_CLIPBOARD_SET,
    TOOL_HISTORY_COPY,
    TOOL_PASTE_HISTORY,
    TOOL_PTT_ENGAGE,
    TOOL_PTT_RELEASE,
    TOOL_INPUT_MODE_SET,
    TOOL_RETRANSMIT_LAST,
    TOOL_DEBUG_HEALTH,
    TOOL_DEBUG_STATS,
    TOOL_DEBUG_DISK,
    TOOL_DEBUG_LOGS,
)

# Lookup by name
TOOLS_BY_NAME: dict[str, ToolDefinition] = {t.name: t for t in ALL_TOOLS}


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_tools_by_scopes(
    granted: set[str] | frozenset[str],
) -> tuple[ToolDefinition, ...]:
    """Return only tools whose required scopes are a subset of *granted*."""
    granted_set = set(granted)
    return tuple(t for t in ALL_TOOLS if t.scopes.issubset(granted_set))


def validate_scopes(
    scopes: list[str],
) -> list[str]:
    """
    Return a list of unknown scopes (not in :data:`ALL_SCOPES`).

    Empty list means all scopes are valid.
    """
    return [s for s in scopes if s not in ALL_SCOPES]
