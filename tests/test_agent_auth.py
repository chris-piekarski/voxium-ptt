"""Tests for voxium.agent_auth (bearer token, session file, bind rules)."""

import os
from pathlib import Path

import pytest

from voxium.agent_auth import (
    SessionCredential,
    compare_tokens,
    has_query_token,
    mint_token,
    parse_bearer,
    unlink_session_credential,
    validate_bind_host,
    validate_token_length,
    write_session_credential,
)

# ---------------------------------------------------------------------------
# Token minting
# ---------------------------------------------------------------------------


def test_mint_token_length():
    token = mint_token()
    assert validate_token_length(token)


def test_mint_token_uniqueness():
    t1 = mint_token()
    t2 = mint_token()
    assert t1 != t2


def test_compare_tokens_equal():
    token = mint_token()
    assert compare_tokens(token, token) is True


def test_compare_tokens_different():
    assert compare_tokens(mint_token(), mint_token()) is False


def test_compare_tokens_different_length():
    assert compare_tokens(mint_token(), "tooshort") is False


# ---------------------------------------------------------------------------
# Bearer parsing
# ---------------------------------------------------------------------------


def test_parse_bearer_success():
    token = "abc"
    result = parse_bearer(f"Bearer {token}")
    assert result.token == token
    assert result.error is None


def test_parse_bearer_missing():
    result = parse_bearer(None)
    assert result.token is None
    assert result.error == "missing_authorization"


def test_parse_bearer_empty():
    result = parse_bearer("")
    assert result.token is None
    assert result.error == "missing_authorization"


def test_parse_bearer_bad_scheme():
    result = parse_bearer("Basic abc123")
    assert result.token is None
    assert result.error == "bad_scheme"


def test_parse_bearer_no_value():
    result = parse_bearer("Bearer")
    assert result.token is None
    assert result.error == "bad_scheme"


def test_parse_bearer_case_insensitive():
    result = parse_bearer("bearer token123")
    assert result.token == "token123"


# ---------------------------------------------------------------------------
# Query-string token rejection
# ---------------------------------------------------------------------------


def test_has_query_token_present():
    assert has_query_token("token=abc&other=1") is True


def test_has_query_token_absent():
    assert has_query_token("foo=bar") is False


def test_has_query_token_none():
    assert has_query_token(None) is False


def test_has_query_token_empty():
    assert has_query_token("") is False


def test_has_query_token_case_insensitive():
    assert has_query_token("TOKEN=secret") is True


# ---------------------------------------------------------------------------
# Bind-host validation
# ---------------------------------------------------------------------------


def test_validate_bind_host_ok():
    assert validate_bind_host("127.0.0.1") is None


def test_validate_bind_host_reject_non_loopback():
    assert validate_bind_host("0.0.0.0") is not None


def test_validate_bind_host_reject_non_loopback_even_if_flag():
    # allow_non_loopback is explicitly parsed but still rejected
    assert validate_bind_host("0.0.0.0", allow_non_loopback=True) is not None


def test_validate_bind_host_reject_hostname():
    assert validate_bind_host("localhost") is not None


# ---------------------------------------------------------------------------
# Session credential file (mode 0600, atomic write)
# ---------------------------------------------------------------------------


@pytest.mark.linux
def test_write_session_credential_mode_0600(tmp_path: Path) -> None:
    cred = SessionCredential(
        url="http://127.0.0.1:12345/mcp",
        token=mint_token(),
        scopes=["status:read"],
    )
    path = write_session_credential(cred, user_dir=tmp_path)
    assert path.exists()
    mode = os.stat(str(path)).st_mode & 0o777
    assert mode == 0o600


@pytest.mark.linux
def test_write_session_credential_json_content(tmp_path: Path) -> None:
    import json

    token = mint_token()
    cred = SessionCredential(
        url="http://127.0.0.1:5555/mcp",
        token=token,
        scopes=["status:read", "transcript:read"],
    )
    path = write_session_credential(cred, user_dir=tmp_path)
    data = json.loads(path.read_text())
    assert data["url"] == "http://127.0.0.1:5555/mcp"
    assert data["token"] == token
    assert data["scopes"] == ["status:read", "transcript:read"]
    assert "pid" in data


@pytest.mark.linux
def test_write_session_credential_fails_on_bad_chmod(tmp_path: Path) -> None:
    """Inject a failing chmod_func to verify fail-closed behaviour."""

    def bad_chmod(*_args, **_kw):
        raise PermissionError("simulated chmod failure")

    cred = SessionCredential(
        url="http://127.0.0.1:1/mcp",
        token=mint_token(),
        scopes=["status:read"],
    )
    with pytest.raises(PermissionError):
        write_session_credential(cred, user_dir=tmp_path, chmod_func=bad_chmod)


@pytest.mark.linux
def test_unlink_session_credential(tmp_path: Path) -> None:
    cred = SessionCredential(
        url="http://127.0.0.1:1/mcp",
        token=mint_token(),
        scopes=["status:read"],
    )
    path = write_session_credential(cred, user_dir=tmp_path)
    assert path.exists()
    unlink_session_credential(user_dir=tmp_path)
    assert not path.exists()


@pytest.mark.linux
def test_unlink_session_credential_missing(tmp_path: Path) -> None:
    # Should not raise
    unlink_session_credential(user_dir=tmp_path)


# ---------------------------------------------------------------------------
# Windows DACL failure simulation (POSIX tests with injected dacl_func)
# ---------------------------------------------------------------------------


def _simulated_windows_write(user_dir: Path, dacl_func):
    """
    Write on POSIX but inject a Windows-style dacl_func to simulate failure.
    """
    from voxium.agent_auth import _user_config_run_dir

    _ = _user_config_run_dir(user_dir)
    if dacl_func is not None:
        # On POSIX, dacl_func is not called because os.name != 'posix' is false.
        # So we test via a chmod_func that fails:
        raise NotImplementedError("use bad_chmod on POSIX")


def test_windows_dacl_fail_simulation(tmp_path: Path) -> None:
    """
    On Windows (or with injected dacl_func), a failing DACL should raise.
    We test this with an injected Windows code path by directly calling
    _windows_dacl_current_user with a failing function.
    """
    from voxium.agent_auth import _windows_dacl_current_user

    def always_fail(_path):
        return False

    with pytest.raises(PermissionError, match="current user"):
        _windows_dacl_current_user(tmp_path / "agent.json", dacl_func=always_fail)


def test_dacl_missing_pywin32_raises_permission_error(tmp_path: Path) -> None:
    """
    When pywin32 is unavailable (dacl_func raises ImportError), the call
    must fail with PermissionError — the heuristic that returned True
    for paths outside USERPROFILE must NOT exist.
    """
    from voxium.agent_auth import _windows_dacl_current_user

    def simulates_missing_pywin32(_path):
        raise ImportError("No module named 'win32api'")

    with pytest.raises(PermissionError, match="restrict file to current user"):
        _windows_dacl_current_user(
            tmp_path / "agent.json", dacl_func=simulates_missing_pywin32
        )


def test_dacl_heuristic_deleted() -> None:
    """
    The _dacl_fallback_heuristic function must not exist — missing pywin32
    is always a fail.
    """
    from voxium import agent_auth

    assert not hasattr(agent_auth, "_dacl_fallback_heuristic")


def test_compare_tokens_different_length_returns_false():
    """compare_tokens must return False (not raise) when lengths differ."""
    # Long token vs short token
    assert compare_tokens("a" * 43, "short") is False
    # Empty vs non-empty
    assert compare_tokens("", "nonempty") is False
    # Both empty
    assert compare_tokens("", "") is True
