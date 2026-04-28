from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from voxium.console_status import print_slash_command_downlink, wrap_telemetry_block
from voxium.metrics_table import format_gpu_metrics_plaintext
from voxium.model_registry import TRUSTED_MODELS
from voxium.session_history import SessionTranscriptHistory
from voxium.slash_commands import (
    run_slash_line,
    slash_data_needs,
)


def test_run_slash_help_variants() -> None:
    for line in ("/help", "/?", "/h", "/  help  "):
        out = run_slash_line(line)
        assert "Slashed commands" in out.text
        assert "/help" in out.text or "help" in out.text.lower()
        assert "/history" in out.text
        assert "/history clear" in out.text
        assert "/history search" in out.text
        assert "/disk" in out.text


def test_run_slash_not_wired() -> None:
    out = run_slash_line("/nope")
    assert "nope" in out.text.lower() or "not wired" in out.text.lower()


def test_run_slash_without_leading_slash() -> None:
    out = run_slash_line("help")
    assert "leading slash" in out.text.lower()


def test_slash_data_needs() -> None:
    assert (
        slash_data_needs("/help").server_gpu is False
        and slash_data_needs("/help").mic_capture is False
    )
    assert (
        slash_data_needs("/gpu").server_gpu is True
        and slash_data_needs("/gpu").mic_capture is False
    )
    assert (
        slash_data_needs("/mic").mic_capture is True
        and slash_data_needs("/mic").server_gpu is False
    )
    assert (
        slash_data_needs("/models").server_gpu is False
        and slash_data_needs("/models").mic_capture is False
    )
    assert (
        slash_data_needs("/history").server_gpu is False
        and slash_data_needs("/history").mic_capture is False
    )
    assert (
        slash_data_needs("/disk").server_gpu is False
        and slash_data_needs("/disk").mic_capture is False
    )


def test_run_slash_models_lists_all_trusted() -> None:
    out = run_slash_line("/models")
    for name in TRUSTED_MODELS:
        assert name in out.text
    assert out.result_rich is not None


def test_run_slash_models_marks_active_and_on_disk(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("voxium.model_disk.models_dir", lambda: tmp_path)
    snap = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "z"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    out = run_slash_line("/models", session_model="base")
    assert "[ACTIVE]" in out.text
    assert "[ON DISK]" in out.text
    assert "  • base —" in out.text


def test_run_slash_models_select_sets_result() -> None:
    out = run_slash_line("/models base")
    assert out.selected_model == "base"
    assert "base" in out.text.lower()

    out2 = run_slash_line("/model large-v3")
    assert out2.selected_model == "large-v3"


def test_run_slash_models_invalid() -> None:
    out = run_slash_line("/models not-a-real-model")
    assert out.selected_model is None
    assert "unsupported" in out.text.lower() or "allowed" in out.text.lower()


def test_run_slash_models_too_many_tokens() -> None:
    out = run_slash_line("/models base extra")
    assert out.selected_model is None
    assert "one model id" in out.text.lower()


def test_run_slash_disk_matches_make_disk_usage_shape() -> None:
    out = run_slash_line("/disk")
    assert "=== Voxium local data (repository) ===" in out.text
    assert "--- models/ ---" in out.text
    assert "--- logs/ ---" in out.text
    out_du = run_slash_line("/du")
    assert "=== Voxium local data (repository) ===" in out_du.text


def test_run_slash_history_with_gpu_sk_does_not_replace_buffer() -> None:
    """``/history`` must use *transcript_history*; extra ``**sk`` (e.g. ``gpu``) is separate."""
    mem = SessionTranscriptHistory(
        max_entries=3, max_total_chars=1_000, max_pending_bytes=0
    )
    mem.add("ground truth line")
    out = run_slash_line(
        "/history",
        session_model=None,
        transcript_history=mem,
        gpu={"name": "test-gpu"},
    )
    assert "ground truth" in out.text
    assert "No transcriptions" not in out.text


def test_run_slash_history_list_and_expand_and_copy() -> None:
    mem = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    mem.add("alpha bravo")
    mem.add("charlie")
    out0 = run_slash_line("/history", transcript_history=mem)
    assert "📋" in out0.text and "charlie" in out0.text
    out1 = run_slash_line("/history 2", transcript_history=mem)
    assert "alpha" in out1.text
    # Headless CI has no OS clipboard; mock so the success path is asserted.
    with patch("voxium.slash_commands.pyperclip.copy"):
        out2 = run_slash_line("/history copy 1", transcript_history=mem)
    assert "Copied" in out2.text and "clipboard" in out2.text.lower()


def test_run_slash_history_clear() -> None:
    mem = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=100
    )
    mem.add("x")
    out = run_slash_line("/history clear", transcript_history=mem)
    assert "Cleared" in out.text and "1 transcript line" in out.text
    assert len(mem) == 0
    out_empty = run_slash_line("/history clear", transcript_history=mem)
    assert "already empty" in out_empty.text
    out_bad = run_slash_line("/history clear oops", transcript_history=mem)
    assert "alone" in out_bad.text.lower()
    out_hist = run_slash_line("/hist clear", transcript_history=mem)
    assert "already empty" in out_hist.text


def test_run_slash_history_search() -> None:
    mem = SessionTranscriptHistory(
        max_entries=10, max_total_chars=10_000, max_pending_bytes=0
    )
    mem.add("alpha meeting notes")
    mem.add("bravo only")
    out = run_slash_line("/history search meeting", transcript_history=mem)
    assert "meeting" in out.text and "#2" in out.text
    assert "bravo" not in out.text
    out_empty = run_slash_line("/history search  ", transcript_history=mem)
    assert "Use /history search" in out_empty.text
    out_hist = run_slash_line("/hist search alpha", transcript_history=mem)
    assert "alpha" in out_hist.text and "#2" in out_hist.text


def test_format_gpu_metrics_plaintext_matches_inference_box_shape() -> None:
    gpu = {
        "name": "NVIDIA GeForce RTX 5090",
        "provider": "pynvml",
        "vram_used_peak_mb": 18775.0,
        "vram_total_mb": 32607.0,
        "utilization_avg_percent": 11.0,
        "utilization_peak_percent": 11.0,
        "power_avg_watts": None,
        "power_peak_watts": None,
        "power_limit_watts": 600.0,
        "temperature_peak_c": 44.0,
        "energy_wh_estimate": None,
    }
    t = format_gpu_metrics_plaintext(gpu)
    assert "RTX 5090" in t
    assert "18775" in t
    assert "32607" in t
    assert "11" in t
    assert "600" in t
    assert "44" in t

    out = run_slash_line("/gpu", gpu=gpu)
    assert "RTX 5090" in out.text
    assert "VRAM" in out.text


def test_run_slash_mic() -> None:
    mic = {
        "backend": {
            "library": "python-sounddevice",
            "api": "PortAudio",
            "sounddevice_version": "0.4.6",
        },
        "device": {
            "index": 0,
            "name": "Test Mic",
            "max_input_channels": 2,
            "default_samplerate_hz": 48000.0,
        },
        "host_api": {"name": "MME"},
        "format": {"sample_rate_hz": 16000, "channels": 1, "dtype": "float32"},
    }
    out_m = run_slash_line("/mic", mic_info=mic)
    assert "Test Mic" in out_m.text
    out_m2 = run_slash_line("/audio", mic_info=mic)
    assert "Test Mic" in out_m2.text


def test_format_gpu_unavailable() -> None:
    t = format_gpu_metrics_plaintext(
        {"_error": "gpu_metrics_unavailable", "_reason": "disabled"}
    )
    assert "unavailable" in t.lower() or "disabled" in t.lower()


def test_format_mic_report_error() -> None:
    from voxium.slash_commands import format_mic_report

    t = format_mic_report({"error": "bogus"})
    assert "bogus" in t
    assert "error" in t.lower()


def test_wrap_telemetry_block_breaks_long_lines() -> None:
    s = "word " * 30
    out = wrap_telemetry_block(s, width=40)
    lines = out.splitlines()
    assert len(lines) >= 2
    assert all(len(line) <= 40 for line in lines if line)


def test_print_slash_command_downlink_uses_telemetry_channel() -> None:
    buf = StringIO()
    c = Console(file=buf, width=80, force_terminal=True, color_system="standard")
    print_slash_command_downlink(c, "/help", "Slashed commands")
    s = buf.getvalue()
    assert "Command: /help" in s
    assert "Slashed commands" in s
    assert "help" in s


def test_print_slash_command_downlink_rich() -> None:
    from rich.text import Text

    buf = StringIO()
    c = Console(file=buf, width=80, force_terminal=True, color_system="standard")
    body = Text("Line one\n", style="dim")
    body.append("Line two", style="bold")
    print_slash_command_downlink(c, "/models", "", result_rich=body)
    s = buf.getvalue()
    assert "Command: /models" in s
    assert "Line one" in s
    assert "Line two" in s
    assert "models" in s
