"""Rich table of inference / capture / GPU metrics (builds on ``metrics_text``)."""

from __future__ import annotations

from rich.table import Table

from voxium.metrics_text import (
    format_bytes,
    format_number,
    format_optional_seconds,
    format_seconds,
)


def build_metrics_table(metrics: dict | None) -> Table:

    table = Table.grid(padding=(0, 2))
    table.add_column(style="#94a3b8", no_wrap=True)
    table.add_column(style="#e2e8f0", no_wrap=True)

    if not metrics:
        table.add_row("Metrics", "[dim]No server metrics returned[/dim]")
        return table

    table.add_row("Request", str(metrics.get("request_id", "n/a")))
    table.add_row("Audio", format_seconds(metrics.get("audio_seconds")))
    table.add_row("Inference", format_seconds(metrics.get("transcription_seconds")))
    table.add_row("Total", format_seconds(metrics.get("total_request_seconds")))
    table.add_row("Realtime", format_number(metrics.get("realtime_factor"), "x", 4))
    table.add_row("Input", format_bytes(metrics.get("input_bytes")))
    table.add_row("Output", f"{metrics.get('output_chars', 0)} chars / {metrics.get('segments', 0)} segments")

    capture = metrics.get("capture") if isinstance(metrics, dict) else None
    if capture:
        device = capture.get("device") or {}
        host_api = capture.get("host_api") or {}
        backend = capture.get("backend") or {}
        audio_format = capture.get("format") or {}
        stream_info = capture.get("stream") or {}
        recording = capture.get("recording") or {}
        statuses = recording.get("callback_statuses") or []

        table.add_row("", "")
        table.add_row("Capture", str(device.get("name") or "default input"))
        table.add_row("Audio API", str(host_api.get("name") or backend.get("api") or "unknown"))
        table.add_row(
            "Format",
            f"{format_number(audio_format.get('sample_rate_hz'), ' Hz', 0)} / "
            f"{format_number(audio_format.get('channels'), ' ch', 0)} / "
            f"{audio_format.get('dtype') or 'unknown'}",
        )
        table.add_row("Device Rate", format_number(device.get("default_samplerate_hz"), " Hz", 0))
        table.add_row("Latency", format_optional_seconds(stream_info.get("latency_seconds")))
        table.add_row(
            "Recorded",
            f"{format_seconds(recording.get('capture_seconds'))} / "
            f"{format_number(recording.get('captured_frames'), ' frames', 0)} / "
            f"{format_number(recording.get('chunks'), ' chunks', 0)}",
        )
        table.add_row("Capture Wall", format_seconds(recording.get("wall_seconds")))
        if statuses:
            table.add_row("Capture Flags", "; ".join(str(status) for status in statuses[:3]))

    model = metrics.get("model") if isinstance(metrics, dict) else None
    if model:
        table.add_row("", "")
        table.add_row("Model", str(model.get("name") or "unknown"))
        language = model.get("language") or "auto"
        language_probability = format_number(model.get("language_probability"), "", 4)
        table.add_row("Language", f"{language} / p={language_probability}")
        table.add_row(
            "VAD",
            f"after {format_seconds(model.get('duration_after_vad_seconds'))} / "
            f"removed {format_seconds(model.get('vad_removed_seconds'))} "
            f"({format_number(model.get('vad_removed_percent'), '%', 2)})",
        )
        table.add_row(
            "Tokens",
            f"{model.get('decoder_tokens', 0)} decoded / "
            f"{format_number(model.get('tokens_per_audio_second'), '/audio-s', 2)} / "
            f"{format_number(model.get('tokens_per_inference_second'), '/infer-s', 2)}",
        )
        table.add_row(
            "Text",
            f"{model.get('output_words', 0)} words / "
            f"{format_number(model.get('chars_per_token'), ' chars/token', 2)}",
        )
        table.add_row("Frames", f"{format_number(model.get('input_audio_frames_estimate'), '', 0)} audio frames est.")
        table.add_row(
            "Quality",
            f"logprob {format_number(model.get('avg_logprob'), '', 4)} / "
            f"no-speech {format_number(model.get('max_no_speech_prob'), '', 4)} / "
            f"compress {format_number(model.get('max_compression_ratio'), '', 3)}",
        )

    gpu = metrics.get("gpu") if isinstance(metrics, dict) else None
    if gpu:
        table.add_row("", "")
        table.add_row("GPU", str(gpu.get("name") or gpu.get("provider") or "available"))
        table.add_row(
            "VRAM",
            f"peak {format_number(gpu.get('vram_used_peak_mb'), ' MB', 1)} / "
            f"{format_number(gpu.get('vram_total_mb'), ' MB', 1)}",
        )
        table.add_row(
            "Util",
            f"avg {format_number(gpu.get('utilization_avg_percent'), '%', 1)} / "
            f"peak {format_number(gpu.get('utilization_peak_percent'), '%', 1)}",
        )
        table.add_row(
            "Power",
            f"avg {format_number(gpu.get('power_avg_watts'), ' W', 2)} / "
            f"peak {format_number(gpu.get('power_peak_watts'), ' W', 2)} / "
            f"limit {format_number(gpu.get('power_limit_watts'), ' W', 2)}",
        )
        table.add_row("Temp", format_number(gpu.get("temperature_peak_c"), " C", 1))
        table.add_row("Energy", format_number(gpu.get("energy_wh_estimate"), " Wh", 6))

    return table
