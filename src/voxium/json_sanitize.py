"""JSON-serialization helpers for numpy / capture metadata (pure)."""

from __future__ import annotations

import numpy as np


def json_safe_audio_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [json_safe_audio_value(item) for item in value]
    return str(value)


def round_audio_float(value, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
