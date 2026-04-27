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
    *,
    peak_abs: float | None = None,
    rms_dbfs: float | None = None,
) -> dict:
    out = dict(base)
    rec: dict = {
        "captured_frames": int(captured_frames),
        "chunks": int(chunks),
        "capture_seconds": round_audio_float(
            captured_frames / sample_rate if sample_rate else None
        ),
        "wall_seconds": round_audio_float(wall_seconds),
        "callback_statuses": list(callback_statuses),
    }
    if peak_abs is not None:
        rec["peak_abs"] = round_audio_float(peak_abs, 6)
    if rms_dbfs is not None:
        rec["rms_dbfs"] = round_audio_float(rms_dbfs, 2)
    out["recording"] = rec
    return out
