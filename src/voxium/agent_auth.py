"""Bearer-token authentication helpers for the MCP control plane.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

# ---------------------------------------------------------------------------
# Token minting
# ---------------------------------------------------------------------------

TOKEN_BYTES = 32  # 256-bit token


def mint_token() -> str:
    """Return a URL-safe, 256-bit bearer token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def validate_token_length(token: str) -> bool:
    """Check that *token* has the expected URL-safe length (base64 of 32 bytes)."""
    # secrets.token_urlsafe(32) produces 43 characters.
    return len(token) == 43


# ---------------------------------------------------------------------------
# Constant-time comparison
# ---------------------------------------------------------------------------


def compare_tokens(a: str, b: str) -> bool:
    """Compare two bearer tokens in constant time."""
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except ValueError:
        # hmac.compare_digest raises on length mismatch
        return False


# ---------------------------------------------------------------------------
# Bearer extraction from ASGI scope / HTTP Authorization header
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BearerParseResult:
    """Result of parsing an Authorization header."""

    token: str | None = None  # extracted bearer token or None
    error: str | None = None  # reason for failure or None


def parse_bearer(authorization: str | None) -> BearerParseResult:
    """
    Extract a bearer token from an ``Authorization`` header value.

    Returns a token on success, or an error description on failure.
    """
    if not authorization:
        return BearerParseResult(error="missing_authorization")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return BearerParseResult(error="bad_scheme")
    return BearerParseResult(token=parts[1])


# ---------------------------------------------------------------------------
# Query-string token rejection
# ---------------------------------------------------------------------------


def has_query_token(query_string: str | None) -> bool:
    """
    Return ``True`` if the URL query string carries a ``token`` parameter.

    Such requests must be rejected outright (400) to prevent token leakage
    via ``ps``, server access logs, or browser history.
    """
    if not query_string:
        return False
    # Quick check: no need to fully parse; presence of "token" is enough
    pairs = query_string.split("&")
    for pair in pairs:
        key, _value, _rest = pair.partition("=")
        if key.lower() == "token":
            return True
    return False


# ---------------------------------------------------------------------------
# Bind-host validation
# ---------------------------------------------------------------------------

ALLOWED_BIND = "127.0.0.1"


def validate_bind_host(host: str, allow_non_loopback: bool = False) -> str | None:
    """
    Validate a bind host for the MCP listener.

    Returns ``None`` on success, or an error description on failure.

    Even when *allow_non_loopback* is set to ``True``, the function
    currently rejects non-loopback hosts — remote MCP is unimplemented.
    """
    if host != ALLOWED_BIND:
        return "bind_host_must_be_127.0.0.1"
    return None


# ---------------------------------------------------------------------------
# Session credential file (mode 0600 / Windows DACL fallback)
# ---------------------------------------------------------------------------


def _user_config_run_dir(user_dir: Path | None = None) -> Path:
    """Resolve ``~/.config/voxium/run/`` (or override for tests)."""
    base = user_dir or Path.home() / ".config" / "voxium"
    run = base / "run"
    run.mkdir(parents=True, exist_ok=True)
    return run


def _write_json_atomic(
    path: Path,
    data: dict[str, Any],
    *,
    chmod_func: Any = None,  # injected for testing (default = os.chmod)
    dacl_func: Any = None,  # injected for testing (default = _apply_current_user_dacl)
) -> None:
    """
    Write *data* as compact JSON to *path* via a temp-file + rename.

    On POSIX, forces mode ``0o600`` (user read/write only).
    On Windows, attempts a current-user-only DACL as a fallback.
    Raises on failure to tighten permissions.
    """
    tmp: IO[str] | None = None
    try:
        fd = None
        dir_parent = path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(dir_parent), suffix=".tmp")
        tmp = os.fdopen(fd, "w")
        json.dump(data, tmp, separators=(",", ":"))
        tmp.close()
        tmp = None

        # Tighten permissions before rename
        _restrict_file_access(
            Path(tmp_name), chmod_func=chmod_func, dacl_func=dacl_func
        )

        os.replace(tmp_name, str(path))
    except Exception:
        # Clean up temp file on any failure
        if tmp is not None:
            try:
                tmp.close()
            except OSError:
                pass
        if "tmp_name" in locals() and Path(tmp_name).exists():
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise


def _restrict_file_access(
    path: Path,
    *,
    chmod_func: Any = None,
    dacl_func: Any = None,
) -> None:
    """
    Restrict *path* to current-user R/W only.

    POSIX: use :func:`os.chmod` with mode ``0o600``.
    Windows: attempt a current-user DACL.
    Raises if neither succeeds.
    """
    if os.name == "posix":
        _chmod_0600(path, chmod_func=chmod_func)
    else:
        _windows_dacl_current_user(path, dacl_func=dacl_func)


def _chmod_0600(path: Path, *, chmod_func: Any = None) -> None:
    fn = chmod_func if chmod_func is not None else os.chmod
    fn(str(path), stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    # Defensive: verify
    actual = os.stat(str(path)).st_mode & 0o777
    if actual != 0o600:
        raise PermissionError(f"chmod 0600 failed: got octal {actual:o}")


def _apply_current_user_dacl(path: Path) -> bool:
    """
    Attempt to restrict *path* to the current user on Windows.

    Uses :mod:`pywintypes` / :mod:`win32api` / :mod:`win32security` if
    available.  Raises :exc:`PermissionError` if pywin32 cannot be
    imported (missing pywin32 is a hard failure — no heuristic).

    Returns ``True`` on success, ``False`` is never returned; failure
    raises :exc:`PermissionError`.
    """
    try:
        import win32api
        import win32con
        import win32security
    except ImportError as exc:
        raise PermissionError(
            "cannot apply current-user DACL: pywin32 not installed"
        ) from exc

    try:
        user_name = win32api.GetUserName()
        domain = win32api.GetComputerName()
        user_sid, _, _ = win32security.LookupAccountName(domain, user_name)

        sd = win32security.GetSecurityDescriptorDacl(
            win32security.GetFileSecurity(
                str(path), win32security.DACL_SECURITY_INFORMATION
            )
        )
        if sd is None:
            sd = win32security.ACL()

        # Deny everyone, grant current user
        sd.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            user_sid,
        )
        sd.AddAccessDeniedAce(
            win32security.ACL_REVISION,
            win32con.GENERIC_ALL,
            win32security.WORLD_SID,
        )

        new_sd = win32security.SECURITY_DESCRIPTOR()
        new_sd.SetSecurityDescriptorDacl(True, sd, False)
        win32security.SetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION, new_sd
        )
        return True
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - catch-all for cleanup + failure
        raise PermissionError(
            "cannot apply current-user DACL: Windows ACL operation failed"
        ) from exc


def _windows_dacl_current_user(path: Path, *, dacl_func: Any = None) -> None:
    fn = dacl_func if dacl_func is not None else _apply_current_user_dacl
    try:
        result = fn(path)
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-closed on any ACL error
        raise PermissionError(
            f"cannot restrict file to current user on Windows: {exc}"
        ) from exc
    if not result:
        raise PermissionError("cannot restrict file to current user on Windows")


# ---------------------------------------------------------------------------
# Write / unlink session credential file
# ---------------------------------------------------------------------------


@dataclass
class SessionCredential:
    """Credential blob written to ``agent.json``."""

    url: str
    token: str
    scopes: list[str]
    pid: int = field(default_factory=os.getpid)
    created_at: float = field(default_factory=time.monotonic)


def write_session_credential(
    credential: SessionCredential,
    *,
    user_dir: Path | None = None,
    chmod_func: Any = None,
    dacl_func: Any = None,
) -> Path:
    """
    Write the session credential file and return its path.

    Raises ``PermissionError`` if the file cannot be made user-only.
    """
    run_dir = _user_config_run_dir(user_dir)
    path = run_dir / "agent.json"
    data = {
        "url": credential.url,
        "token": credential.token,
        "scopes": credential.scopes,
        "pid": credential.pid,
        "created_at": credential.created_at,
    }
    _write_json_atomic(path, data, chmod_func=chmod_func, dacl_func=dacl_func)
    return path


def unlink_session_credential(user_dir: Path | None = None) -> None:
    """Remove the session credential file (ignore if missing)."""
    run_dir = _user_config_run_dir(user_dir)
    path = run_dir / "agent.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
