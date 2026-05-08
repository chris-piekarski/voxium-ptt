"""Unit tests for the runtime profile ring buffer."""

from __future__ import annotations

import pytest

from voxium import polish_profile
from voxium.llama_cpp_client import LlamaCppChatResult


@pytest.fixture(autouse=True)
def _clear_profile_buffer() -> None:
    polish_profile.reset()
    yield
    polish_profile.reset()


def _make_result(
    *,
    seconds: float = 0.5,
    prompt_tokens: int | None = 100,
    completion_tokens: int | None = 20,
    prompt_n: int | None = 100,
    prompt_ms: float | None = 250.0,
    predicted_n: int | None = 20,
    predicted_ms: float | None = 200.0,
    ok: bool = True,
    error: str | None = None,
) -> LlamaCppChatResult:
    return LlamaCppChatResult(
        ok=ok,
        text="hi" if ok else "",
        error=error,
        seconds=seconds,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=(prompt_tokens or 0) + (completion_tokens or 0) if ok else None,
        raw_status=200 if ok else 500,
        prompt_n=prompt_n,
        prompt_ms=prompt_ms,
        predicted_n=predicted_n,
        predicted_ms=predicted_ms,
        cache_n=None,
    )


def test_record_appends_to_named_slot() -> None:
    polish_profile.record("polish", model="m.gguf", result=_make_result())
    snap = polish_profile.snapshot()
    assert list(snap.keys()) == ["polish"]
    assert len(snap["polish"]) == 1
    sample = snap["polish"][0]
    assert sample.slot == "polish"
    assert sample.model == "m.gguf"
    assert sample.ok is True
    assert sample.prompt_n == 100
    assert sample.predicted_ms == 200.0


def test_aggregate_computes_prefill_and_decode_rates() -> None:
    # 100 tokens / 0.25s = 400 t/s prefill; 20 tokens / 0.2s = 100 t/s decode.
    polish_profile.record("polish", model="m", result=_make_result())
    polish_profile.record("polish", model="m", result=_make_result())

    stats = polish_profile.aggregate()
    assert "polish" in stats
    s = stats["polish"]
    assert s.n == 2
    assert s.n_ok == 2 and s.n_fail == 0
    assert s.prefill_tok_per_s == pytest.approx(400.0, rel=1e-6)
    assert s.decode_tok_per_s == pytest.approx(100.0, rel=1e-6)
    assert s.wall_p50 == pytest.approx(0.5, rel=1e-6)
    assert s.avg_prompt_tokens == pytest.approx(100.0)
    assert s.avg_completion_tokens == pytest.approx(20.0)


def test_failed_calls_recorded_but_excluded_from_rates() -> None:
    polish_profile.record(
        "chatter_copy",
        model="m",
        result=_make_result(
            ok=False,
            error="HTTP 500: nope",
            prompt_n=None,
            prompt_ms=None,
            predicted_n=None,
            predicted_ms=None,
        ),
    )
    polish_profile.record("chatter_copy", model="m", result=_make_result())
    stats = polish_profile.aggregate()
    s = stats["chatter_copy"]
    assert s.n == 2
    assert s.n_fail == 1 and s.n_ok == 1
    # Only the ok sample contributes to prefill/decode rates.
    assert s.prefill_tok_per_s == pytest.approx(400.0, rel=1e-6)
    assert s.decode_tok_per_s == pytest.approx(100.0, rel=1e-6)


def test_reset_clears_buffers() -> None:
    polish_profile.record("polish", model="m", result=_make_result())
    polish_profile.reset()
    assert polish_profile.snapshot() == {}
    assert polish_profile.aggregate() == {}


def test_format_report_with_no_data_is_friendly() -> None:
    out = polish_profile.format_profile_report()
    assert "no /transcribe or llama-server calls" in out


def test_format_report_lists_each_slot_with_summary() -> None:
    polish_profile.record("polish", model="polish.gguf", result=_make_result())
    polish_profile.record(
        "chatter_copy", model="copy.gguf", result=_make_result(seconds=0.3)
    )
    out = polish_profile.format_profile_report()
    assert "polish" in out
    assert "chatter_copy" in out
    assert "prefill:" in out
    assert "decode:" in out


def test_window_caps_per_slot_at_42() -> None:
    for _ in range(80):
        polish_profile.record("polish", model="m", result=_make_result())
    snap = polish_profile.snapshot()
    assert len(snap["polish"]) == 42


def test_missing_timings_are_tolerated() -> None:
    polish_profile.record(
        "banner",
        model="m",
        result=_make_result(
            prompt_n=None, prompt_ms=None, predicted_n=None, predicted_ms=None
        ),
    )
    s = polish_profile.aggregate()["banner"]
    assert s.prefill_tok_per_s is None
    assert s.decode_tok_per_s is None
    out = polish_profile.format_profile_report()
    assert "banner" in out


def _stt_metrics(
    *,
    transcription: float = 1.20,
    total: float = 1.40,
    audio: float = 2.50,
    rtf: float = 0.48,
    decoder_tokens: int = 64,
) -> dict:
    return {
        "transcription_seconds": transcription,
        "total_request_seconds": total,
        "audio_seconds": audio,
        "realtime_factor": rtf,
        "model": {"name": "medium.en", "decoder_tokens": decoder_tokens},
    }


def test_record_stt_appends_and_extracts_fields() -> None:
    polish_profile.record_stt(
        model="medium.en",
        client_wall_seconds=1.50,
        metrics=_stt_metrics(),
        ok=True,
    )
    samples = polish_profile.snapshot_stt()
    assert len(samples) == 1
    s = samples[0]
    assert s.model == "medium.en"
    assert s.ok is True
    assert s.client_wall_seconds == pytest.approx(1.50)
    assert s.transcription_seconds == pytest.approx(1.20)
    assert s.server_total_seconds == pytest.approx(1.40)
    assert s.audio_seconds == pytest.approx(2.50)
    assert s.realtime_factor == pytest.approx(0.48)
    assert s.decoder_tokens == 64


def test_aggregate_stt_computes_network_overhead_and_averages() -> None:
    polish_profile.record_stt(
        model="medium.en",
        client_wall_seconds=1.50,
        metrics=_stt_metrics(total=1.40, transcription=1.20),
        ok=True,
    )
    polish_profile.record_stt(
        model="medium.en",
        client_wall_seconds=1.60,
        metrics=_stt_metrics(total=1.50, transcription=1.30),
        ok=True,
    )
    st = polish_profile.aggregate_stt()
    assert st.n == 2 and st.n_ok == 2 and st.n_fail == 0
    assert st.last_model == "medium.en"
    assert st.avg_client_wall == pytest.approx(1.55)
    assert st.avg_server_total == pytest.approx(1.45)
    assert st.avg_transcription == pytest.approx(1.25)
    # Network overhead: avg of (1.50-1.40, 1.60-1.50) = 0.10
    assert st.avg_network_overhead == pytest.approx(0.10, rel=1e-6)


def test_record_stt_tolerates_missing_metrics() -> None:
    polish_profile.record_stt(
        model="—",
        client_wall_seconds=2.0,
        metrics=None,
        ok=False,
        error="HTTP 500",
    )
    st = polish_profile.aggregate_stt()
    assert st.n == 1 and st.n_ok == 0 and st.n_fail == 1
    assert st.avg_transcription is None
    assert st.avg_server_total is None
    assert st.avg_network_overhead is None


def test_format_report_includes_stt_section_first() -> None:
    polish_profile.record_stt(
        model="medium.en",
        client_wall_seconds=1.50,
        metrics=_stt_metrics(),
        ok=True,
    )
    polish_profile.record("polish", model="m", result=_make_result())
    out = polish_profile.format_profile_report()
    # STT header should appear before any LLM slot.
    assert "transcribe (STT)" in out
    assert out.index("transcribe (STT)") < out.index("polish")
    assert "STT 1.20s" in out
    assert "audio 2.50s" in out


def test_reset_clears_stt_buffer_too() -> None:
    polish_profile.record_stt(
        model="m",
        client_wall_seconds=1.0,
        metrics=_stt_metrics(),
        ok=True,
    )
    polish_profile.reset()
    assert polish_profile.snapshot_stt() == []
    assert polish_profile.aggregate_stt().n == 0
