"""Normalize `sys.argv`-style token lists for the voxium CLI (pure)."""

from __future__ import annotations

from voxium.constants import CLI_COMMANDS


def normalize_cli_args(argv: list[str]) -> list[str]:
    if not argv:
        return ["run"]
    if argv[0] in ("-h", "--help", "--version"):
        return argv
    if argv[0] in CLI_COMMANDS:
        return argv
    if argv[0].startswith("-"):
        return ["run", *argv]
    return argv
