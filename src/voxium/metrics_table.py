"""Rich table of inference / capture / GPU metrics (builds on ``metrics_text``)."""

from __future__ import annotations

from rich.columns import Columns
from rich.console import RenderableType
from rich.constrain import Constrain
from rich.table import Table

from voxium.metrics_text import (
    format_bytes,
    format_number,
    format_number_plain,
    format_optional_seconds,
    format_seconds,
)

# Minimum terminal inner width (chars) to justify multiple KV columns — avoids unreadable slivers.
_METRICS_COL_3_MIN_W = 100
_METRICS_COL_2_MIN_W = 64


def _metrics_column_count(available_width: int) -> int:
    """More columns when wide; default ~80-col consoles use two columns for readable cells."""
    if available_width >= _METRICS_COL_3_MIN_W:
        return 3
    if available_width >= _METRICS_COL_2_MIN_W:
        return 2
    return 1


def gpu_metrics_label_value_pairs(
    gpu: dict,
    *,
    plain: bool = False,
) -> list[tuple[str, str]]:
    """
    Same field strings as the GPU block in the inference metrics table
    (Rich markup when ``plain`` is False).
    """
    fn = format_number_plain if plain else format_number
    return [
        ("GPU", str(gpu.get("name") or gpu.get("provider") or "available")),
        (
            "VRAM",
            f"peak {fn(gpu.get('vram_used_peak_mb'), ' MB', 1)} / "
            f"{fn(gpu.get('vram_total_mb'), ' MB', 1)}",
        ),
        (
            "Util",
            f"avg {fn(gpu.get('utilization_avg_percent'), '%', 1)} / "
            f"peak {fn(gpu.get('utilization_peak_percent'), '%', 1)}",
        ),
        (
            "Power",
            f"avg {fn(gpu.get('power_avg_watts'), ' W', 2)} / "
            f"peak {fn(gpu.get('power_peak_watts'), ' W', 2)} / "
            f"limit {fn(gpu.get('power_limit_watts'), ' W', 2)}",
        ),
        ("Temp", fn(gpu.get("temperature_peak_c"), " C", 1)),
        ("Energy", fn(gpu.get("energy_wh_estimate"), " Wh", 6)),
    ]


def format_gpu_metrics_plaintext(gpu: dict | None) -> str:
    """Plain-text block matching the GPU section of the Voxium inference metrics box."""
    if not gpu:
        return "No GPU metrics in this readout (n/a). Run a PTT pass or check the local server, copy."
    if gpu.get("_error"):
        reason = gpu.get("_reason") or ""
        return (
            f"GPU metrics unavailable: {gpu.get('_error')}"
            + (f"\n{reason}" if reason else "")
            + "\nIs the /transcribe server up and GPU metrics enabled, copy?"
        )
    lines = []
    for label, value in gpu_metrics_label_value_pairs(gpu, plain=True):
        lines.append(f"{label:<13} {value}")
    return "\n".join(lines)


def _rows_request_output_compact(metrics: dict) -> list[tuple[str, str]]:
    """Request timing + I/O: same numbers as the first :func:`_rows_request_block` fields, merged lines."""
    return [
        ("Request", str(metrics.get("request_id", "n/a"))),
        (
            "Audio / Inf / Total",
            f"{format_seconds(metrics.get('audio_seconds'))} · "
            f"{format_seconds(metrics.get('transcription_seconds'))} · "
            f"{format_seconds(metrics.get('total_request_seconds'))}",
        ),
        (
            "RTF / Input",
            f"{format_number(metrics.get('realtime_factor'), 'x', 4)} · "
            f"{format_bytes(metrics.get('input_bytes'))}",
        ),
        (
            "Output",
            f"{metrics.get('output_chars', 0)} chars / {metrics.get('segments', 0)} segments",
        ),
    ]


def _rows_audio_input_compact(metrics: dict) -> list[tuple[str, str]]:
    """
    Transcript (cyan) panel: one dense row with **audio in**, **infer / total** latency, and **RTF**.

    Value strings spell out what each number is (still one row, ``|`` / ``·`` separators).
    """
    return [
        (
            "Audio",
            f"duration {format_seconds(metrics.get('audio_seconds'))} · "
            f"WAV {format_bytes(metrics.get('input_bytes'))} | "
            f"infer {format_seconds(metrics.get('transcription_seconds'))} · "
            f"end-to-end {format_seconds(metrics.get('total_request_seconds'))} | "
            f"RTF {format_number(metrics.get('realtime_factor'), 'x', 4)}",
        ),
    ]


def _rows_model_compact(model: dict) -> list[tuple[str, str]]:
    """Model section: three dense rows (same fields as the former five-row block) with short labels in-value."""
    language = model.get("language") or "auto"
    language_probability = format_number(model.get("language_probability"), "", 4)
    return [
        (
            "Model",
            f"name {model.get('name') or 'unknown'} · "
            f"lang {language} · "
            f"p={language_probability} | "
            f"VAD: keep {format_seconds(model.get('duration_after_vad_seconds'))} · "
            f"trim {format_seconds(model.get('vad_removed_seconds'))} · "
            f"{format_number(model.get('vad_removed_percent'), '% of input', 2)}",
        ),
        (
            "Tokens",
            f"decoder {model.get('decoder_tokens', 0)} tok | "
            f"{format_number(model.get('tokens_per_audio_second'), ' tok/s (audio)', 2)} · "
            f"{format_number(model.get('tokens_per_inference_second'), ' tok/s (infer)', 2)} | "
            f"{model.get('output_words', 0)} words · "
            f"{format_number(model.get('chars_per_token'), ' ch/token', 2)} · "
            f"~{format_number(model.get('input_audio_frames_estimate'), '', 0)} frames (est.)",
        ),
        (
            "Quality",
            f"avg logprob {format_number(model.get('avg_logprob'), '', 4)} · "
            f"no-speech p {format_number(model.get('max_no_speech_prob'), '', 4)} · "
            f"compress {format_number(model.get('max_compression_ratio'), '', 3)}",
        ),
    ]


def _rows_request_block(metrics: dict) -> list[tuple[str, str]]:
    return [
        ("Request", str(metrics.get("request_id", "n/a"))),
        ("Audio", format_seconds(metrics.get("audio_seconds"))),
        ("Inference", format_seconds(metrics.get("transcription_seconds"))),
        ("Total", format_seconds(metrics.get("total_request_seconds"))),
        ("Realtime", format_number(metrics.get("realtime_factor"), "x", 4)),
        ("Input", format_bytes(metrics.get("input_bytes"))),
        (
            "Output",
            f"{metrics.get('output_chars', 0)} chars / {metrics.get('segments', 0)} segments",
        ),
    ]


def _rows_model_block(model: dict) -> list[tuple[str, str]]:
    language = model.get("language") or "auto"
    language_probability = format_number(model.get("language_probability"), "", 4)
    return [
        ("Model", str(model.get("name") or "unknown")),
        ("Language", f"{language} / p={language_probability}"),
        (
            "VAD",
            f"after {format_seconds(model.get('duration_after_vad_seconds'))} / "
            f"removed {format_seconds(model.get('vad_removed_seconds'))} "
            f"({format_number(model.get('vad_removed_percent'), '%', 2)})",
        ),
        (
            "Tokens",
            f"{model.get('decoder_tokens', 0)} decoded / "
            f"{format_number(model.get('tokens_per_audio_second'), '/audio-s', 2)} / "
            f"{format_number(model.get('tokens_per_inference_second'), '/infer-s', 2)}",
        ),
        (
            "Text",
            f"{model.get('output_words', 0)} words / "
            f"{format_number(model.get('chars_per_token'), ' chars/token', 2)}",
        ),
        (
            "Frames",
            f"{format_number(model.get('input_audio_frames_estimate'), '', 0)} audio frames est.",
        ),
        (
            "Quality",
            f"logprob {format_number(model.get('avg_logprob'), '', 4)} / "
            f"no-speech {format_number(model.get('max_no_speech_prob'), '', 4)} / "
            f"compress {format_number(model.get('max_compression_ratio'), '', 3)}",
        ),
    ]


def _rows_capture_compact(capture: dict) -> list[tuple[str, str]]:
    """All capture fields from :func:`build_metrics_table`, merged for fewer rows."""
    device = capture.get("device") or {}
    host_api = capture.get("host_api") or {}
    backend = capture.get("backend") or {}
    audio_format = capture.get("format") or {}
    stream_info = capture.get("stream") or {}
    recording = capture.get("recording") or {}
    statuses = recording.get("callback_statuses") or []
    device_name = str(device.get("name") or "default input")
    api = str(host_api.get("name") or backend.get("api") or "unknown")
    rows: list[tuple[str, str]] = [
        (
            "Capture / API",
            f"{device_name} · {api}",
        ),
        (
            "Format / dev Hz",
            f"{format_number(audio_format.get('sample_rate_hz'), ' Hz', 0)} / "
            f"{format_number(audio_format.get('channels'), ' ch', 0)} / "
            f"{audio_format.get('dtype') or 'unknown'}"
            f" · {format_number(device.get('default_samplerate_hz'), ' Hz', 0)}",
        ),
        ("Latency", format_optional_seconds(stream_info.get("latency_seconds"))),
        (
            "Rec / wall",
            f"{format_seconds(recording.get('capture_seconds'))} / "
            f"{format_number(recording.get('captured_frames'), ' frames', 0)} / "
            f"{format_number(recording.get('chunks'), ' chunks', 0)} · "
            f"wall {format_seconds(recording.get('wall_seconds'))}",
        ),
    ]
    if statuses:
        rows.append(
            (
                "Flags",
                "; ".join(str(status) for status in statuses[:3]),
            )
        )
    return rows


def _rows_gpu_compact(gpu: dict) -> list[tuple[str, str]]:
    """All GPU fields from :func:`gpu_metrics_label_value_pairs` in fewer rows (values unchanged)."""
    if gpu.get("_error"):
        reason = gpu.get("_reason") or ""
        msg = str(gpu.get("_error"))
        if reason:
            msg = f"{msg} — {reason}"
        return [("GPU", f"[dim]{msg}[/dim]")]

    fn = format_number
    return [
        ("GPU", str(gpu.get("name") or gpu.get("provider") or "available")),
        (
            "VRAM · util",
            f"peak {fn(gpu.get('vram_used_peak_mb'), ' MB', 1)} / "
            f"{fn(gpu.get('vram_total_mb'), ' MB', 1)} · "
            f"avg {fn(gpu.get('utilization_avg_percent'), '%', 1)} / "
            f"peak {fn(gpu.get('utilization_peak_percent'), '%', 1)}",
        ),
        (
            "Power",
            f"avg {fn(gpu.get('power_avg_watts'), ' W', 2)} / "
            f"peak {fn(gpu.get('power_peak_watts'), ' W', 2)} / "
            f"limit {fn(gpu.get('power_limit_watts'), ' W', 2)}",
        ),
        (
            "Temp · energy",
            f"{fn(gpu.get('temperature_peak_c'), ' C', 1)} · "
            f"{fn(gpu.get('energy_wh_estimate'), ' Wh', 6)}",
        ),
    ]


def _rows_transcript_log_compact(metrics: dict) -> list[tuple[str, str]]:
    """
    Voxium transcript (cyan) panel: **audio input** (what went to the server) + **model inference**
    metrics only — no GPU, no client capture, no request id, no separate “transcript out” row
    (``/gpu``, ``/mic``, and :func:`build_metrics_table` stay the full readout).
    """
    rows: list[tuple[str, str]] = []
    rows.extend(_rows_audio_input_compact(metrics))
    model = metrics.get("model")
    if isinstance(model, dict):
        rows.extend(_rows_model_compact(model))
    return rows


def _split_rows_into_n_columns(
    items: list[tuple[str, str]], ncols: int
) -> list[list[tuple[str, str]]]:
    n = len(items)
    if n == 0:
        return [[] for _ in range(max(1, ncols))]
    ncols = min(max(1, ncols), n)
    base, rem = divmod(n, ncols)
    out: list[list[tuple[str, str]]] = []
    idx = 0
    for i in range(ncols):
        take = base + (1 if i < rem else 0)
        out.append(items[idx : idx + take])
        idx += take
    return out


def _kv_subtable(
    rows: list[tuple[str, str]],
    *,
    pad: tuple[int, int] = (0, 1),
    value_ratio: int = 3,
    collapse_padding: bool = True,
) -> Table:
    t = Table.grid(padding=pad, pad_edge=False, collapse_padding=collapse_padding)
    t.add_column(style="#94a3b8", no_wrap=True, justify="right", ratio=1)
    t.add_column(
        style="#e2e8f0", no_wrap=False, overflow="fold", ratio=max(2, value_ratio)
    )
    for label, value in rows:
        t.add_row(label, value)
    return t


def build_ptt_log_metrics_layout(
    metrics: dict | None,
    *,
    available_width: int,
) -> RenderableType:
    """
    Key/value layout for the cyan **Voxium** transcript panel: **audio** (duration, size, inf/total,
    RTF on one row) and **model** (three dense rows) — same numbers as before, less vertical space.
    No GPU, no capture/mic block. Column count follows terminal width (1 / 2 / 3); padding is tight.
    """
    if not metrics:
        t = _kv_subtable(
            [("Metrics", "[dim]No server metrics returned[/dim]")],
            pad=(0, 1),
            collapse_padding=False,
        )
        return t

    rows = _rows_transcript_log_compact(metrics)

    ncols = _metrics_column_count(available_width)
    chunks = _split_rows_into_n_columns(rows, ncols)
    subtables: list[RenderableType] = []
    for ch in chunks:
        if ch:
            subtables.append(
                _kv_subtable(
                    ch,
                    value_ratio=4,
                    pad=(0, 1),
                    collapse_padding=False,
                )
            )
    if not subtables:
        return _kv_subtable([], pad=(0, 1), collapse_padding=False)
    if len(subtables) == 1:
        out: RenderableType = subtables[0]
    else:
        # ``width=`` on :class:`rich.columns.Columns` is *per-column*, not total width.
        # Zero padding between subtables for a tight strip.
        out = Columns(
            subtables,
            column_first=False,
            equal=True,
            expand=True,
            padding=(0, 0, 0, 0),
        )
    if available_width and available_width > 12:
        return Constrain(out, width=available_width)
    return out


def build_metrics_table(metrics: dict | None) -> Table:

    table = Table.grid(padding=(0, 2))
    table.add_column(style="#94a3b8", no_wrap=True)
    table.add_column(style="#e2e8f0", no_wrap=True)

    if not metrics:
        table.add_row("Metrics", "[dim]No server metrics returned[/dim]")
        return table

    for label, value in _rows_request_block(metrics):
        table.add_row(label, value)

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
        table.add_row(
            "Audio API", str(host_api.get("name") or backend.get("api") or "unknown")
        )
        table.add_row(
            "Format",
            f"{format_number(audio_format.get('sample_rate_hz'), ' Hz', 0)} / "
            f"{format_number(audio_format.get('channels'), ' ch', 0)} / "
            f"{audio_format.get('dtype') or 'unknown'}",
        )
        table.add_row(
            "Device Rate", format_number(device.get("default_samplerate_hz"), " Hz", 0)
        )
        table.add_row(
            "Latency", format_optional_seconds(stream_info.get("latency_seconds"))
        )
        table.add_row(
            "Recorded",
            f"{format_seconds(recording.get('capture_seconds'))} / "
            f"{format_number(recording.get('captured_frames'), ' frames', 0)} / "
            f"{format_number(recording.get('chunks'), ' chunks', 0)}",
        )
        table.add_row("Capture Wall", format_seconds(recording.get("wall_seconds")))
        if statuses:
            table.add_row(
                "Capture Flags", "; ".join(str(status) for status in statuses[:3])
            )

    model = metrics.get("model") if isinstance(metrics, dict) else None
    if model:
        table.add_row("", "")
        for label, value in _rows_model_block(model):
            table.add_row(label, value)

    gpu = metrics.get("gpu") if isinstance(metrics, dict) else None
    if gpu:
        table.add_row("", "")
        for label, value in gpu_metrics_label_value_pairs(gpu, plain=False):
            table.add_row(label, value)

    return table
