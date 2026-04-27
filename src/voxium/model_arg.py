"""argparse-typed model name (shared by app and server CLIs)."""

from __future__ import annotations

import argparse

from voxium.model_registry import validate_model_name


def trusted_model_arg(value: str) -> str:
    try:
        return validate_model_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
