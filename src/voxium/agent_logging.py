"""SDK logging guard for the MCP server.

Pure module: no MCP SDK import, no network, no microphone.

The MCP Python SDK (mcp>=2.3) calls ``configure_logging`` unconditionally
during ``MCPServer.__init__``, which routes through
``logging.basicConfig`` and may attach a ``RichHandler`` targeting
``stderr`` when the ``rich`` package is importable.  That pollutes the
root logger and causes duplicate / unexpected output on the terminal.

:func:`install_sdk_log_guard` appends a ``logging.NullHandler`` to the
root logger *before* ``MCPServer`` is constructed.  Because
``basicConfig`` no-ops when the root logger already has handlers, the
SDK never gets past the guard.  Existing handlers (if any) are preserved.
"""

from __future__ import annotations

import logging

__all__ = ["install_sdk_log_guard"]


def install_sdk_log_guard() -> None:
    """Append a :class:`logging.NullHandler` when the root logger has none.

    This is intended as a defensive guard **before** constructing
    ``MCPServer``.  A second call is a no-op (root logger already has
    handlers).  The function does *not* ``basicConfig`` itself.

    **Does not import** ``mcp``.
    """
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        root_logger.addHandler(logging.NullHandler())
