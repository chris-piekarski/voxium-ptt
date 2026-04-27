"""Voxium: PTT & VOX to text on loopback — HAM-style keying, Apollo-style local stack (see docs/brand.md)."""

from __future__ import annotations

from typing import Any

__version__ = "0.0.1"

__all__ = ["__version__", "main"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from voxium.cli.main import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
