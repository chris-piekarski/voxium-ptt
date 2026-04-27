"""Tests for voxium.capture_enrich."""

from voxium.capture_enrich import enrich_capture_with_recording


def test_enrich_merges_recording():
    base = {"device": "d"}
    out = enrich_capture_with_recording(
        base,
        captured_frames=100,
        chunks=2,
        wall_seconds=1.25,
        callback_statuses=["s1"],
        sample_rate=0,  # exercise branch: no capture_seconds when falsy
    )
    assert out["device"] == "d"
    r = out["recording"]
    assert r["captured_frames"] == 100
    assert r["callback_statuses"] == ["s1"]
    assert r["capture_seconds"] is None
    assert r["wall_seconds"] is not None
    assert "peak_abs" not in r and "rms_dbfs" not in r


def test_enrich_stores_level_fields():
    out = enrich_capture_with_recording(
        {},
        48_000,
        3,
        1.0,
        [],
        16_000,
        peak_abs=0.2,
        rms_dbfs=-32.5,
    )
    r = out["recording"]
    assert r["peak_abs"] == 0.2
    assert r["rms_dbfs"] == -32.5
