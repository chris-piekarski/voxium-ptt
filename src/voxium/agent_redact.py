"""Log redaction helpers for the MCP control plane.

Pure module: no network, no microphone, no MCP SDK import.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Constants & compiled patterns
# ---------------------------------------------------------------------------

# Absolute paths: POSIX, Windows, macOS
_PATH_PATTERNS = re.compile(
    r"(?:/home/[^,\s\"'<>]*)|(?:/Users/[^,\s\"'<>]*)|(?:(?:[A-Za-z]):\\[^,\s\"'<>]*)"
)

# Authorization: Bearer <token>
_AUTH_PATTERN = re.compile(
    r"(Authorization\s*:\s*(?:Bearer\s+)?)\S+",
    re.IGNORECASE,
)

# Generic long base64url-ish tokens (40+ chars of URL-safe base64 alphabet)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{40,}")

_PEM_BEGIN = "-----BEGIN"
_PEM_END = "-----END"

_REDACT_PLACEHOLDER = "<redacted>"
_PATH_PLACEHOLDER = "<path>"


def redact_log_line(line: str, *, extra_tokens: Iterable[str] | None = None) -> str:
    """
    Redact a single log line before it leaves the client.

    Replaces:
    - Authorization headers and bearer tokens
    - Long base64url-looking strings (potential leaked tokens)
    - Absolute file paths (POSIX / Windows / macOS)
    - PEM-like blocks (collapse to placeholder)
    - Any *extra_tokens* (e.g. the live bearer)

    Returns the redacted line. A line that is *only* a path still becomes
    ``<path>`` (non-empty).
    """
    if not line:
        return line

    # 1. Replace explicit extra tokens first (most specific)
    for tok in extra_tokens or ():
        line = line.replace(tok, _REDACT_PLACEHOLDER)

    # 2. Authorization: Bearer <token>
    line = _AUTH_PATTERN.sub(lambda _m: _REDACT_PLACEHOLDER, line)

    # 3. Long token-shaped strings (only if they don't look like paths at this point)
    def _maybe_redact_token(m: re.Match) -> str:
        val = m.group(0)
        # Don't redact if it looks like a code identifier in quotes
        return _REDACT_PLACEHOLDER if _looks_like_token(val) else val

    line = _TOKEN_PATTERN.sub(_maybe_redact_token, line)

    # 4. Absolute paths
    line = _PATH_PATTERNS.sub(_PATH_PLACEHOLDER, line)

    # 5. PEM blocks: collapse the entire BEGIN/END span to placeholder
    line = re.sub(r"-----BEGIN[\s\S]*?-----END", _REDACT_PLACEHOLDER, line)

    return line


def redact_lines(
    lines: Iterable[str],
    *,
    max_lines: int | None = None,
    extra_tokens: Iterable[str] | None = None,
) -> tuple[list[str], bool]:
    """
    Redact an iterable of log lines, optionally capped.

    Returns ``(lines, truncated)``.
    """
    result: list[str] = []
    truncated = False
    for line in lines:
        if max_lines is not None and len(result) >= max_lines:
            truncated = True
            break
        result.append(redact_log_line(line, extra_tokens=extra_tokens))
    return result, truncated


# ---------------------------------------------------------------------------
# Internal heuristics
# ---------------------------------------------------------------------------


def _looks_like_token(s: str) -> bool:
    """Return True if *s* looks like a bearer token rather than code."""
    # Tokens from secrets.token_urlsafe contain only [A-Za-z0-9_-] and are
    # rarely surrounded by quotes in log lines.
    return all(
        ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-="
        for ch in s
    )
