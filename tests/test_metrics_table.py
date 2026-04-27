"""Tests for voxium.metrics_table (Rich table, no display)."""

from io import StringIO

from rich.console import Console

from voxium.metrics_table import build_metrics_table


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
