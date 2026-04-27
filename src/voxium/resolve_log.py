"""Resolve log level from typical CLI args (pure)."""

from __future__ import annotations


def resolve_log_level(
    *,
    log_level: str | None = None,
    quiet: bool = False,
    verbose: int = 0,
) -> str:
    if log_level:
        return log_level
    if quiet:
        return "ERROR"
    if verbose:
        return "DEBUG"
    return "INFO"
