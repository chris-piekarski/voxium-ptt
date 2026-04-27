"""Voxium: PTT *vox* to text on loopback — HAM-style keying, Apollo-style local stack (see docs/brand.md)."""

from __future__ import annotations

from typing import Any

__version__ = "1.0.0"

__all__ = ["__version__", "main"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from voxium.cli.main import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
