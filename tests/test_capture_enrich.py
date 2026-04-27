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
