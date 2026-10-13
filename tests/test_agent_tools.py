"""Tests for voxium.agent_tools (catalog, scopes, filtering)."""

from voxium.agent_protocol import VALID_ERROR_CODES
from voxium.agent_tools import (
    ALL_SCOPES,
    ALL_TOOLS,
    DEFAULT_SCOPES,
    TOOLS_BY_NAME,
    filter_tools_by_scopes,
    validate_scopes,
)

# ---------------------------------------------------------------------------
# Catalog basics
# ---------------------------------------------------------------------------


def test_catalog_names_unique():
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == len(set(names))


def test_all_tools_in_lookup():
    for t in ALL_TOOLS:
        assert TOOLS_BY_NAME[t.name] is t


def test_every_tool_scopes_subset_of_known():
    for t in ALL_TOOLS:
        assert t.scopes.issubset(
            ALL_SCOPES
        ), f"Tool {t.name} has unknown scope {t.scopes - ALL_SCOPES}"


def test_default_scopes_are_valid():
    for scope in DEFAULT_SCOPES:
        assert scope in ALL_SCOPES


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------


def test_validate_scopes_ok():
    assert validate_scopes(["status:read"]) == []


def test_validate_scopes_unknown():
    unknown = validate_scopes(["status:read", "bad_scope"])
    assert "bad_scope" in unknown
    assert "status:read" not in unknown


def test_validate_scopes_all_unknown():
    result = validate_scopes(["foo", "bar"])
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_filter_tools_empty_scopes():
    tools = filter_tools_by_scopes(set())
    assert all(t.name == "session_status" for t in tools) or (
        # session_status requires status:read, so empty should return nothing
        len(tools)
        == 0
    )


def test_filter_tools_status_only():
    tools = filter_tools_by_scopes({"status:read"})
    names = {t.name for t in tools}
    assert "session_status" in names
    assert "ptt_engage" not in names  # needs input:ptt


def test_filter_tools_all_scopes():
    tools = filter_tools_by_scopes(ALL_SCOPES)
    assert len(tools) == len(ALL_TOOLS)


def test_filter_tools_transcript_only():
    tools = filter_tools_by_scopes({"transcript:read"})
    names = {t.name for t in tools}
    assert "transmission_last" in names
    assert "history_list" in names
    assert "history_get" in names
    assert "clipboard_set" not in names  # needs clipboard:write


# ---------------------------------------------------------------------------
# Input schema rejects unknown fields
# ---------------------------------------------------------------------------


def test_input_schema_additional_properties_false():
    # TOOL_HISTORY_GET has additionalProperties: False
    from voxium.agent_dispatch import _validate_input
    from voxium.agent_tools import TOOL_HISTORY_GET

    err = _validate_input(
        {"index": 1, "bad_field": True}, TOOL_HISTORY_GET.input_schema
    )
    assert err is not None
    assert err.code == "invalid_arguments"


def test_input_schema_accepts_known():
    from voxium.agent_dispatch import _validate_input
    from voxium.agent_tools import TOOL_HISTORY_GET

    err = _validate_input({"index": 1}, TOOL_HISTORY_GET.input_schema)
    assert err is None


# ---------------------------------------------------------------------------
# Error code completeness
# ---------------------------------------------------------------------------


def test_error_codes_non_empty():
    assert len(VALID_ERROR_CODES) >= 20
