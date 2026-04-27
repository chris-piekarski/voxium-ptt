"""Merge recording fields into a capture_info dict (pure, no globals)."""

from __future__ import annotations

from voxium.json_sanitize import round_audio_float


def enrich_capture_with_recording(
    base: dict,
    captured_frames: int,
    chunks: int,
    wall_seconds: float | None,
    callback_statuses: list[str],
    sample_rate: int,
) -> dict:
    out = dict(base)
    out["recording"] = {
        "captured_frames": int(captured_frames),
        "chunks": int(chunks),
        "capture_seconds": round_audio_float(
            captured_frames / sample_rate if sample_rate else None
        ),
        "wall_seconds": round_audio_float(wall_seconds),
        "callback_statuses": list(callback_statuses),
    }
    return out
