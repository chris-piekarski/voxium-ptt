"""Tests for voxium.metrics_table (Rich table, no display)."""

from io import StringIO

from rich.console import Console

from voxium.metrics_table import (
    _metrics_column_count,
    build_metrics_table,
    build_ptt_log_metrics_layout,
)


def test_build_metrics_table_empty_shows_message():
    t = build_metrics_table(None)
    out = StringIO()
    Console(file=out, width=80, legacy_windows=False, force_terminal=False).print(t, end="")
    assert "No server metrics" in out.getvalue()


def test_build_metrics_table_with_capture_model_gpu():
    metrics = {
        "request_id": "1",
        "audio_seconds": 1.0,
        "transcription_seconds": 0.5,
        "total_request_seconds": 0.6,
        "realtime_factor": 2.0,
        "input_bytes": 1000,
        "output_chars": 3,
        "segments": 1,
        "capture": {
            "device": {"name": "d", "default_samplerate_hz": 48000},
            "host_api": {"name": "win"},
            "backend": {"api": "win"},
            "format": {"sample_rate_hz": 16000, "channels": 1, "dtype": "f32"},
            "stream": {"latency_seconds": [0.1, 0.2]},
            "recording": {
                "callback_statuses": ["a", "b", "c", "d"],
                "capture_seconds": 1.0,
                "captured_frames": 10,
                "chunks": 2,
                "wall_seconds": 1.1,
            },
        },
        "model": {
            "name": "m",
            "language": "en",
            "language_probability": 0.9,
            "duration_after_vad_seconds": 1.0,
            "vad_removed_seconds": 0.0,
            "vad_removed_percent": 0.0,
            "decoder_tokens": 5,
            "tokens_per_audio_second": 1.0,
            "tokens_per_inference_second": 2.0,
            "output_words": 3,
            "chars_per_token": 2.0,
            "input_audio_frames_estimate": 100,
            "avg_logprob": -0.1,
            "max_no_speech_prob": 0.2,
            "max_compression_ratio": 1.5,
        },
        "gpu": {
            "name": "GPU",
            "vram_used_peak_mb": 100.0,
            "vram_total_mb": 8000.0,
            "utilization_avg_percent": 10.0,
            "utilization_peak_percent": 50.0,
            "power_avg_watts": 20.0,
            "power_peak_watts": 30.0,
            "power_limit_watts": 200.0,
            "temperature_peak_c": 70.0,
            "energy_wh_estimate": 0.1,
        },
    }
    t = build_metrics_table(metrics)
    assert t.row_count > 3


def test_ptt_log_metrics_layout_has_fewer_render_lines_than_vertical_table() -> None:
    """Three-column PTT log uses compact merged rows (same metrics, shorter height)."""
    metrics = {
        "request_id": "1",
        "audio_seconds": 1.0,
        "transcription_seconds": 0.5,
        "total_request_seconds": 0.6,
        "realtime_factor": 2.0,
        "input_bytes": 1000,
        "output_chars": 3,
        "segments": 1,
        "model": {
            "name": "m",
            "language": "en",
            "language_probability": 0.9,
            "duration_after_vad_seconds": 1.0,
            "vad_removed_seconds": 0.0,
            "vad_removed_percent": 0.0,
            "decoder_tokens": 5,
            "tokens_per_audio_second": 1.0,
            "tokens_per_inference_second": 2.0,
            "output_words": 3,
            "chars_per_token": 2.0,
            "input_audio_frames_estimate": 100,
            "avg_logprob": -0.1,
            "max_no_speech_prob": 0.2,
            "max_compression_ratio": 1.5,
        },
    }
    full = StringIO()
    vert = build_metrics_table(
        {k: v for k, v in metrics.items() if k not in ("capture", "gpu")},
    )
    wide = build_ptt_log_metrics_layout(
        {k: v for k, v in metrics.items() if k not in ("capture", "gpu")},
        available_width=100,
    )
    Console(
        file=full,
        width=100,
        force_terminal=True,
        legacy_windows=False,
    ).print(vert, end="")
    wide_out = StringIO()
    Console(
        file=wide_out,
        width=100,
        force_terminal=True,
        legacy_windows=False,
    ).print(wide, end="")
    assert "Request" in full.getvalue()
    assert "Request" not in wide_out.getvalue()
    w = wide_out.getvalue()
    assert "Audio" in w and "infer" in w and "end-to-end" in w and "Model" in w and "Tokens" in w
    # Vertical stack is one line per field; compact multi-column layout uses fewer lines.
    assert full.getvalue().count("\n") > wide_out.getvalue().count("\n")


def test_metrics_column_count_responsive() -> None:
    assert _metrics_column_count(120) == 3
    assert _metrics_column_count(100) == 3
    assert _metrics_column_count(99) == 2
    assert _metrics_column_count(80) == 2
    assert _metrics_column_count(63) == 1


def test_transcript_log_layout_omits_capture_and_gpu() -> None:
    """Transcript panel: audio input + model only — no capture, no GPU in the compact layout."""
    metrics = {
        "request_id": "1",
        "audio_seconds": 1.0,
        "transcription_seconds": 0.5,
        "total_request_seconds": 0.6,
        "realtime_factor": 2.0,
        "input_bytes": 1000,
        "output_chars": 3,
        "segments": 1,
        "capture": {
            "device": {"name": "mic", "default_samplerate_hz": 48000},
            "host_api": {"name": "win"},
            "backend": {"api": "win"},
            "format": {"sample_rate_hz": 16000, "channels": 1, "dtype": "f32"},
            "stream": {"latency_seconds": [0.1, 0.2]},
            "recording": {
                "callback_statuses": ["a", "b", "c"],
                "capture_seconds": 1.0,
                "captured_frames": 10,
                "chunks": 2,
                "wall_seconds": 1.1,
            },
        },
        "model": {
            "name": "m",
            "language": "en",
            "language_probability": 0.9,
            "duration_after_vad_seconds": 1.0,
            "vad_removed_seconds": 0.0,
            "vad_removed_percent": 0.0,
            "decoder_tokens": 5,
            "tokens_per_audio_second": 1.0,
            "tokens_per_inference_second": 2.0,
            "output_words": 3,
            "chars_per_token": 2.0,
            "input_audio_frames_estimate": 100,
            "avg_logprob": -0.1,
            "max_no_speech_prob": 0.2,
            "max_compression_ratio": 1.5,
        },
        "gpu": {
            "name": "G",
            "vram_used_peak_mb": 100.0,
            "vram_total_mb": 8000.0,
            "utilization_avg_percent": 10.0,
            "utilization_peak_percent": 50.0,
            "power_avg_watts": 20.0,
            "power_peak_watts": 30.0,
            "power_limit_watts": 200.0,
            "temperature_peak_c": 70.0,
            "energy_wh_estimate": 0.1,
        },
    }
    wide = build_ptt_log_metrics_layout(metrics, available_width=100)
    out = StringIO()
    Console(
        file=out,
        width=100,
        force_terminal=True,
        legacy_windows=False,
    ).print(wide, end="")
    s = out.getvalue()
    assert "Capture" not in s
    assert "Transcript out" not in s
    assert "16000" not in s
    assert "Audio" in s and "RTF" in s
    assert "infer" in s and "end-to-end" in s
    assert "Model" in s and "Tokens" in s
    assert "VRAM · util" not in s
