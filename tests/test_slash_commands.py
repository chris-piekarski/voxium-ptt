from io import StringIO
from unittest.mock import patch

from rich.console import Console

from voxium.console_status import print_slash_command_downlink, wrap_telemetry_block
from voxium.metrics_table import format_gpu_metrics_plaintext
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    trusted_polish_model,
)
from voxium.session_history import SessionTranscriptHistory
from voxium.slash_commands import (
    _line_style,
    _render_catalog,
    build_transcribe_models_catalog_rich,
    run_slash_line,
    slash_data_needs,
)


def test_build_transcribe_models_catalog_rich_branches() -> None:
    plain, _rt = build_transcribe_models_catalog_rich("base", installed_only=False)
    assert "Transcriber" in plain or "transcrib" in plain.lower()
    plain2, _ = build_transcribe_models_catalog_rich("base", installed_only=True)
    assert len(plain2) > 20


def test_line_style_markers() -> None:
    assert "bold" in _line_style(active=True)
    assert "bold" in _line_style(installed=True)
    assert "dim" in _line_style()


def test_render_catalog_includes_footer() -> None:
    plain, _body = _render_catalog(
        "Title",
        [("line1", "detail1", "bold #fff")],
        footer_lines=["foot1", "foot2"],
    )
    assert "Title" in plain
    assert "foot1" in plain


def test_run_slash_help_variants() -> None:
    for line in ("/help", "/?", "/h", "/  help  "):
        out = run_slash_line(line)
        assert "Slash command downlink" in out.text
        assert "Tab for completions" in out.text
        assert "/help" in out.text or "help" in out.text.lower()
        assert "/health" in out.text
        assert "/hotkeys" in out.text
        assert "/hotkeys ptt <f1..f12>" in out.text
        assert "/stats" in out.text
        assert "/history" in out.text
        assert "/history clear" in out.text
        assert "/history search" in out.text
        assert "/disk" in out.text
        assert "Examples:" in out.text


def test_run_slash_not_wired() -> None:
    out = run_slash_line("/nope")
    assert "nope" in out.text.lower() or "not wired" in out.text.lower()


def test_run_slash_without_leading_slash() -> None:
    out = run_slash_line("help")
    assert "leading slash" in out.text.lower()


def test_slash_data_needs() -> None:
    assert (
        slash_data_needs("/help").server_gpu is False
        and slash_data_needs("/help").server_health is False
        and slash_data_needs("/help").server_stats is False
        and slash_data_needs("/help").mic_capture is False
    )
    assert (
        slash_data_needs("/health").server_health is True
        and slash_data_needs("/health").server_gpu is False
        and slash_data_needs("/health").server_stats is False
        and slash_data_needs("/health").mic_capture is False
    )
    assert (
        slash_data_needs("/gpu").server_gpu is True
        and slash_data_needs("/gpu").server_health is False
        and slash_data_needs("/gpu").server_stats is False
        and slash_data_needs("/gpu").mic_capture is False
    )
    assert (
        slash_data_needs("/stats").server_stats is True
        and slash_data_needs("/stats").server_health is False
        and slash_data_needs("/stats").server_gpu is False
        and slash_data_needs("/stats").mic_capture is False
    )
    assert (
        slash_data_needs("/mic").mic_capture is True
        and slash_data_needs("/mic").server_health is False
        and slash_data_needs("/mic").server_gpu is False
        and slash_data_needs("/mic").server_stats is False
    )
    assert (
        slash_data_needs("/models").server_gpu is False
        and slash_data_needs("/models").server_health is False
        and slash_data_needs("/models").server_stats is False
        and slash_data_needs("/models").mic_capture is False
    )
    assert (
        slash_data_needs("/history").server_gpu is False
        and slash_data_needs("/history").server_health is False
        and slash_data_needs("/history").server_stats is False
        and slash_data_needs("/history").mic_capture is False
    )
    assert (
        slash_data_needs("/disk").server_gpu is False
        and slash_data_needs("/disk").server_health is False
        and slash_data_needs("/disk").server_stats is False
        and slash_data_needs("/disk").mic_capture is False
    )


def test_run_slash_stats_formats_persistent_and_server_totals() -> None:
    out = run_slash_line(
        "/stats",
        persistent_stats={
            "inference_requests_total": 3,
            "by_source": {"ptt": 1, "vox": 1, "retry": 1},
            "audio_seconds_total": 4.25,
            "input_bytes_total": 12345,
            "transcription_seconds_total": 1.5,
            "request_seconds_total": 2.0,
            "decoder_tokens_total": 42,
            "polish_prompt_tokens_total": 7,
            "polish_completion_tokens_total": 8,
            "polish_tokens_total": 15,
            "output_chars_total": 100,
            "output_words_total": 20,
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        server_stats={
            "request_count": 2,
            "total_audio_processed_seconds": 3.0,
            "input_bytes_processed": 456,
            "total_transcription_seconds": 0.75,
            "total_request_seconds": 1.25,
            "output_chars_generated": 50,
            "model_metrics": {
                "decoder_tokens_generated": 21,
                "output_words_generated": 9,
            },
        },
    )

    assert "local persisted totals" in out.text
    assert "requests: 3" in out.text
    assert "PTT 1" in out.text
    assert "VOX 1" in out.text
    assert "re-transmit 1" in out.text
    assert "decoder 42" in out.text
    assert "re-encode 15" in out.text
    assert "server-process totals" in out.text
    assert "reset when the /transcribe server restarts" in out.text


def test_run_slash_models_status_includes_lanes() -> None:
    out = run_slash_line("/models")
    assert "Transcribe:" in out.text
    assert "Shared polish/chatter:" in out.text
    assert "Backend: transcribe=faster-whisper" in out.text
    assert "Select: /models transcribe use <id>" in out.text
    assert out.result_rich is None


def test_run_slash_hotkeys_status_and_setters() -> None:
    out = run_slash_line("/hotkeys")
    assert "PTT: F9" in out.text
    assert "Replay: F8" in out.text

    set_ptt = run_slash_line("/hotkeys ptt f10")
    assert set_ptt.hotkeys == {"record": "f10"}
    assert "PTT hotkey set to F10" in set_ptt.text

    set_replay = run_slash_line(
        "/hotkeys replay 11",
        current_hotkeys={
            "record": "f10",
            "recovery": "f8",
            "retry": "f6",
            "mode": "f7",
        },
    )
    assert set_replay.hotkeys == {"recovery": "f11"}
    assert "replay hotkey set to F11" in set_replay.text


def test_run_slash_hotkeys_rejects_duplicates_and_bad_names() -> None:
    dup = run_slash_line("/hotkeys ptt f8")
    assert dup.hotkeys is None
    assert "already assigned to replay" in dup.text

    bad_action = run_slash_line("/hotkeys retry f10")
    assert bad_action.hotkeys is None
    assert "ptt or replay" in bad_action.text.lower()

    bad_key = run_slash_line("/hotkeys ptt ctrl+x")
    assert bad_key.hotkeys is None
    assert "F1 through F12" in bad_key.text


def test_run_slash_models_shows_transcribe_and_polish_in_status() -> None:
    out = run_slash_line(
        "/models",
        session_model="base",
        polish_model=DEFAULT_TRUSTED_POLISH_MODEL_ID,
        polish_enabled=True,
    )
    assert "this run: base" in out.text
    assert "product default: small.en" in out.text
    assert "Shared polish/chatter: re-encode on" in out.text
    assert DEFAULT_TRUSTED_POLISH_MODEL_ID in out.text


def test_run_slash_models_shows_pinned_config_vs_product_default() -> None:
    out = run_slash_line(
        "/models",
        session_model="tiny",
        file_config={"transcription": {"model": "tiny"}},
    )
    assert "this run: tiny" in out.text
    assert "config: tiny" in out.text
    assert "product default: small.en" in out.text
    assert "transcription.model" in out.text


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
    assert "use /models" in out.text.lower()


def test_run_slash_models_transcribe_list_returns_catalog() -> None:
    out = run_slash_line("/models transcribe list", session_model="base")
    assert "Transcribers" in out.text
    assert "[ACTIVE]" in out.text
    assert out.result_rich is not None


def test_run_slash_models_transcribe_use_sets_result() -> None:
    out = run_slash_line("/models transcribe use large-v3")
    assert out.selected_model == "large-v3"


def test_run_slash_models_polish_list_shows_trusted_and_local_models(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    trusted_path = tmp_path / "models" / "polish" / trusted.filename
    trusted_path.parent.mkdir(parents=True, exist_ok=True)
    trusted_path.write_bytes(b"gguf")
    custom_path = tmp_path / "models" / "polish" / "custom" / "shell.gguf"
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    custom_path.write_bytes(b"gguf")

    out = run_slash_line(
        "/models polish list",
        polish_enabled=True,
        polish_model=DEFAULT_TRUSTED_POLISH_MODEL_ID,
    )
    assert "Polish + UX chatter models" in out.text
    assert DEFAULT_TRUSTED_POLISH_MODEL_ID in out.text
    assert "local:custom/shell.gguf" in out.text
    assert out.result_rich is not None


def test_run_slash_models_polish_use_sets_model() -> None:
    out = run_slash_line("/models polish use qwen2.5-3b-q4km")
    assert out.polish_model == "qwen2.5-3b-q4km"
    assert "shared polish/chatter model set" in out.text.lower()
    assert "download automatically" in out.text.lower()


def test_run_slash_models_polish_toggle_sets_flag() -> None:
    out = run_slash_line("/models polish on")
    assert out.polish_enabled is True


def test_run_slash_polish_alias_supports_list_and_use(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    trusted_path = tmp_path / "models" / "polish" / trusted.filename
    trusted_path.parent.mkdir(parents=True, exist_ok=True)
    trusted_path.write_bytes(b"gguf")

    out = run_slash_line("/polish list", polish_model=DEFAULT_TRUSTED_POLISH_MODEL_ID)
    assert "Polish + UX chatter models" in out.text
    assert out.result_rich is not None

    out2 = run_slash_line("/polish use qwen2.5-3b-q4km")
    assert out2.polish_model == "qwen2.5-3b-q4km"


def test_run_slash_disk_matches_make_disk_usage_shape() -> None:
    out = run_slash_line("/disk")
    assert "=== Voxium local data (repository) ===" in out.text
    assert "--- models/ ---" in out.text
    assert "--- logs/ ---" in out.text
    assert "--- tools/llama.cpp/ ---" in out.text
    out_du = run_slash_line("/du")
    assert "=== Voxium local data (repository) ===" in out_du.text
    assert "--- tools/llama.cpp/ ---" in out_du.text


def test_run_slash_health_formats_server_readiness() -> None:
    health = {
        "status": "ok",
        "model": "base",
        "model_repo": "Systran/faster-whisper-base",
        "device": "cuda",
        "compute": "float16",
        "vad_enabled": True,
        "timeout_seconds": 120,
        "gpu_metrics_enabled": True,
        "gpu_metrics_provider": "pynvml",
        "polish_backend_default": "llama.cpp",
        "polish_enabled_default": False,
        "polish_default_model": "auto",
        "polish_timeout_seconds": 25,
        "polish_keep_alive_default": "-1",
        "polish_llama_cpp_reachable": True,
        "polish_loaded_model": DEFAULT_TRUSTED_POLISH_MODEL_ID,
        "polish_model_loaded": True,
        "faster_whisper": {"version": "1.1.1"},
    }
    out = run_slash_line("/health", server_health=health)
    assert "Loopback server health:" in out.text
    assert "server booted with base" in out.text
    assert "GPU metrics: on (pynvml)" in out.text
    assert "re-encode default: off" in out.text
    assert "llama.cpp: reachable yes" in out.text
    assert f"loaded {DEFAULT_TRUSTED_POLISH_MODEL_ID}" in out.text
    assert "faster-whisper: 1.1.1" in out.text


def test_run_slash_health_unavailable() -> None:
    out = run_slash_line("/health", server_health=None)
    assert "unavailable" in out.text.lower()


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


def test_run_slash_reencode_list_matches_polish_list() -> None:
    o_p = run_slash_line("/polish list", polish_model=DEFAULT_TRUSTED_POLISH_MODEL_ID)
    o_r = run_slash_line(
        "/re-encode list", polish_model=DEFAULT_TRUSTED_POLISH_MODEL_ID
    )
    assert o_p.text == o_r.text


def test_run_slash_reencode_on_off() -> None:
    o = run_slash_line("/re-encode on")
    assert o.polish_enabled is True
    o2 = run_slash_line("/reencode off")
    assert o2.polish_enabled is False


def test_first_cmd_empty_when_no_slash() -> None:
    from voxium.slash_commands import _first_cmd

    assert _first_cmd("not a slash line") == ""


def test_format_mic_report_includes_active_stream_json() -> None:
    from voxium.slash_commands import format_mic_report

    t = format_mic_report(
        {
            "device": {"name": "D"},
            "stream": {"latency": [0.01, 0.02]},
        }
    )
    assert "active stream" in t


def test_format_mic_report_includes_latency_and_portaudio() -> None:
    from voxium.slash_commands import format_mic_report

    mic = {
        "backend": {
            "api": "X",
            "library": "L",
            "sounddevice_version": "0.1",
            "portaudio_version_text": "v1-PortAudio",
        },
        "device": {
            "index": 0,
            "name": "D",
            "max_input_channels": 1,
            "default_samplerate_hz": 48000,
            "default_low_input_latency_seconds": 0.01,
            "default_high_input_latency_seconds": 0.2,
        },
        "host_api": {"name": "H"},
        "format": {"channels": 1, "dtype": "f32", "sample_rate_hz": 16000},
    }
    t = format_mic_report(mic)
    assert "low input latency" in t
    assert "PortAudio" in t


def test_format_health_report_polish_and_faster_whisper() -> None:
    from voxium.slash_commands import format_health_report

    h = {
        "status": "ok",
        "model": "m",
        "startup_model": "m",
        "device": "d",
        "compute": "c",
        "timeout_seconds": 5,
        "vad_enabled": True,
        "model_repo": "r/w",
        "startup_model_repo": "r/w",
        "loaded_transcribe_models": ["m", "small.en"],
        "warmed_transcribe_models": ["small.en"],
        "gpu_metrics_enabled": False,
        "gpu_metrics_unavailable_reason": "no cuda",
        "polish_backend_default": "llama.cpp",
        "polish_default_model": "gguf",
        "polish_enabled_default": True,
        "polish_llama_cpp_reachable": False,
        "polish_llama_cpp_reachable_reason": "sleeping",
        "polish_loaded_model": "L",
        "polish_timeout_seconds": 9.0,
        "polish_keep_alive_default": "-1",
        "faster_whisper": {"version": "1.2.3"},
    }
    t = format_health_report(h, session_model="small.en")
    assert "re-encode" in t
    assert "sleeping" in t
    assert "1.2.3" in t
    assert "no cuda" in t
    assert "r/w" in t
    assert "server booted with m" in t
    assert "transcribe loaded: m, small.en" in t
    assert "transcribe warmed: small.en" in t
    assert "transcribe this client: small.en" in t


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
