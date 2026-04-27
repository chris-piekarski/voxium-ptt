"""Unit tests for on-station standby (local + Zulu, FFT strip, path, tail)."""

from voxium.standby_fft import SPECTRUM_BARS, SPECTRUM_DISPLAY_WIDTH
from voxium.standby_telemetry import build_standby_detail_line

_FAKE_SPEC = "▁" * 4 + "▆" * 4 + "▇" * 8 + "▃" * 16  # 32


def test_no_decode_flats_spectrum() -> None:
    from voxium.standby_fft import reset_spectrum_state

    reset_spectrum_state()
    s = build_standby_detail_line(0, {"sample_rate_hz": 16000, "channels": 1})
    assert SPECTRUM_BARS[0] * SPECTRUM_DISPLAY_WIDTH in s.plain
    assert "Z" in s.plain  # Zulu (UTC) leg of the clock
    assert " local · " in s.plain  # local wall time + UTC for the same instant
    assert "kHz" in s.plain and "PTT a take" in s.plain


def test_context_last_spectrum_fft_appears_in_line() -> None:
    s = build_standby_detail_line(
        0,
        {
            "sample_rate_hz": 16000,
            "channels": 1,
            "last_ptt_wall_s": 2.5,
            "last_ptt_audio_s": 2.0,
            "last_spectrum_fft": _FAKE_SPEC,
        },
    )
    assert "2.0s on wire" in s.plain and "key 2.5" in s.plain
    assert "Test Mic" not in s.plain
    assert _FAKE_SPEC in s.plain


def test_decode_bars_use_spectrum_chars() -> None:
    s = build_standby_detail_line(
        3,
        {
            "has_last_decode": True,
            "sample_rate_hz": 16000,
            "channels": 1,
            "last_realtime_factor": 0.4,
            "last_audio_seconds": 2.0,
            "last_spectrum_fft": _FAKE_SPEC,
        },
    )
    assert _FAKE_SPEC in s.plain
    assert "kHz" in s.plain
    assert "mono" in s.plain
    assert "audio in" not in s.plain


def test_decode_tail_shows_level_not_redundant_audio_duration() -> None:
    s = build_standby_detail_line(
        0,
        {
            "has_last_decode": True,
            "last_realtime_factor": 0.4,
            "last_model_name": "tiny",
            "last_audio_seconds": 9.99,
            "last_capture_peak": 0.123,
            "last_capture_rms_dbfs": -28.0,
        },
    )
    assert "last RTF" in s.plain
    assert "pk 0.123" in s.plain
    assert "RMS -28" in s.plain
    assert "audio in" not in s.plain
    assert "9.99" not in s.plain
