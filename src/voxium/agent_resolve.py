"""Agent flag-precedence resolver.

Pure module: no TUI, no microphone, no MCP SDK import.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["resolve_agent_enabled"]


def resolve_agent_enabled(
    *,
    cli_flag: bool | None = None,
    env: str | None = None,
    file_agent: dict[str, Any] | None = None,
    _env_get: Any = None,
) -> bool:
    """Resolve whether the opt-in MCP listener should start.

    Precedence (highest wins):

    1. CLI flag (``--agent`` or ``--no-agent``) — ``True`` or ``False``.
    2. Environment variable ``VOXIUM_AGENT`` — ``1``/``true``/``yes`` is on,
       ``0``/``false``/``no`` is off.  Any other set value is *off*.
    3. YAML ``agent.enabled``.
    4. Hardcoded default: **off**.

    Parameters
    ----------
    cli_flag:
        ``True`` if ``--agent`` was explicit, ``False`` if ``--no-agent``
        was explicit, ``None`` if neither was given.
    env:
        The raw value of ``VOXIUM_AGENT`` from the environment, or ``None``
        if unset.  In production this is read automatically via ``_env_get``.
    file_agent:
        The ``agent`` section of the merged YAML config (a dict, or empty).
    _env_get:
        Callable to read environment variables (``os.environ.get`` by default).
        Overridable in tests; ignored when *env* is explicitly provided.

    Returns
    -------
    bool
        ``True`` when the listener should start, ``False`` otherwise.
    """
    # 1. CLI wins outright
    if cli_flag is not None:
        return cli_flag

    # 2. Environment variable (only if explicitly set)
    raw_env = env if env is not None else (_env_get or os.environ.get)("VOXIUM_AGENT")
    if raw_env is not None:
        norm = raw_env.strip().lower()
        if norm in ("1", "true", "yes"):
            return True
        if norm in ("0", "false", "no"):
            return False
        # Any other explicitly-set value falls through as "off"
        return False

    # 3. YAML config
    if isinstance(file_agent, dict):
        return bool(file_agent.get("enabled", False))

    # 4. Hardcoded default
    return False


def _copy_yaml_scopes(
    file_agent: dict[str, Any] | None,
) -> list[str] | None:
    """Extract an explicit scope list from YAML agent config, or None."""
    if isinstance(file_agent, dict):
        scopes = file_agent.get("scopes")
        if isinstance(scopes, list) and scopes:
            return scopes
    return None
