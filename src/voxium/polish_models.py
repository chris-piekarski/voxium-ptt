"""Compatibility wrapper for trusted polish model IDs."""

from __future__ import annotations

from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
    POLISH_MODEL_NAMES,
    validate_polish_model_name,
)

DEFAULT_POLISH_MODEL = POLISH_DEFAULT_MODEL
TRUSTED_POLISH_TAGS: frozenset[str] = frozenset(POLISH_MODEL_NAMES)
DEFAULT_TRUSTED_POLISH_TAG = DEFAULT_TRUSTED_POLISH_MODEL_ID


def validate_polish_model_tag(name: str) -> str:
    """Preserve the helper name used across runtime modules."""
    n = (name or "").strip()
    if not n:
        raise ValueError("Polish model is required, copy.")
    return validate_polish_model_name(n)
