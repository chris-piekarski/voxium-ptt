"""Unit tests for on-station standby (local + Zulu, FFT strip, path, tail)."""

from voxium.standby_fft import SPECTRUM_BARS, SPECTRUM_DISPLAY_WIDTH
from voxium.standby_telemetry import (
    _format_path_brief,
    _tail_from_context,
    build_standby_detail_line,
)

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


def test_morse_marquee_fills_remaining_standby_width() -> None:
    content_width = SPECTRUM_DISPLAY_WIDTH + 2 + 18
    s = build_standby_detail_line(
        0,
        {
            "last_spectrum_fft": _FAKE_SPEC,
            "last_transcript_text": "sos",
        },
        content_width=content_width,
    )
    label_line = s.plain.splitlines()[1]
    code_line = s.plain.splitlines()[2]

    assert label_line.startswith(" " * SPECTRUM_DISPLAY_WIDTH + "  ")
    assert len(label_line) == content_width
    assert " S   O   S " in label_line
    assert code_line.startswith(_FAKE_SPEC + "  ")
    assert len(code_line) == content_width
    assert "... --- ..." in code_line


def test_morse_marquee_animates_with_standby_tick() -> None:
    context = {
        "last_spectrum_fft": _FAKE_SPEC,
        "last_transcript_text": "copy",
    }
    a = build_standby_detail_line(
        0, context, content_width=SPECTRUM_DISPLAY_WIDTH + 2 + 18
    )
    b = build_standby_detail_line(
        3, context, content_width=SPECTRUM_DISPLAY_WIDTH + 2 + 18
    )

    assert a.plain.splitlines()[1] != b.plain.splitlines()[1]
    assert a.plain.splitlines()[2] != b.plain.splitlines()[2]


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


def test_decode_tail_shows_cw_audio_playing() -> None:
    s = build_standby_detail_line(
        0,
        {
            "has_last_decode": True,
            "morse_audio_playing": True,
            "last_realtime_factor": 0.4,
        },
    )
    assert "CW audio on" in s.plain


def test_path_brief_falls_back_on_non_numeric_durations() -> None:
    s = _format_path_brief(
        {
            "last_ptt_wall_s": "nope",
            "last_ptt_audio_s": "bad",
        }
    )
    assert "kHz" in s and "PTT" in s


def test_path_brief_same_key_and_wire_hides_key() -> None:
    s = _format_path_brief(
        {
            "last_ptt_wall_s": 1.0,
            "last_ptt_audio_s": 1.05,
        }
    )
    assert "1.1s on wire" in s and "key" not in s


def test_tail_partial_decode_without_metrics() -> None:
    t = _tail_from_context({"has_last_decode": True})
    assert "partial" in t


def test_tail_skips_non_numeric_metrics() -> None:
    t = _tail_from_context(
        {
            "has_last_decode": True,
            "last_realtime_factor": "nope",
            "last_capture_peak": "bad",
            "last_capture_rms_dbfs": "x",
        }
    )
    assert "partial" in t
