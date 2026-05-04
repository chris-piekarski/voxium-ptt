from voxium.persistent_stats import (
    accumulate_stats,
    default_stats,
    load_stats,
    record_stats,
    save_stats,
)


def test_load_stats_missing_and_corrupt_files(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert load_stats(missing)["inference_requests_total"] == 0

    corrupt = tmp_path / "stats.json"
    corrupt.write_text("{not json", encoding="utf-8")
    loaded = load_stats(corrupt)
    assert loaded["by_source"] == {"ptt": 0, "vox": 0, "retry": 0}
    assert loaded["audio_seconds_total"] == 0.0


def test_accumulate_stats_counts_sources_and_metric_fields() -> None:
    stats = default_stats()
    metrics = {
        "audio_seconds": 1.25,
        "input_bytes": 2048,
        "transcription_seconds": 0.5,
        "total_request_seconds": 0.75,
        "output_chars": 24,
        "model": {"decoder_tokens": 12, "output_words": 4},
        "polish": {"tokens_in": 8, "tokens_out": 6, "total_tokens": 14},
    }

    stats = accumulate_stats(
        stats, metrics, source="ptt", now="2026-01-01T00:00:00+00:00"
    )
    stats = accumulate_stats(
        stats,
        {
            **metrics,
            "polish": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
        source="vox",
        now="2026-01-01T00:01:00+00:00",
    )
    stats = accumulate_stats(stats, {}, source="retry", now="2026-01-01T00:02:00+00:00")

    assert stats["started_at"] == "2026-01-01T00:00:00+00:00"
    assert stats["updated_at"] == "2026-01-01T00:02:00+00:00"
    assert stats["inference_requests_total"] == 3
    assert stats["by_source"] == {"ptt": 1, "vox": 1, "retry": 1}
    assert stats["audio_seconds_total"] == 2.5
    assert stats["input_bytes_total"] == 4096
    assert stats["decoder_tokens_total"] == 24
    assert stats["polish_prompt_tokens_total"] == 18
    assert stats["polish_completion_tokens_total"] == 11
    assert stats["polish_tokens_total"] == 29
    assert stats["output_chars_total"] == 48
    assert stats["output_words_total"] == 8


def test_save_stats_writes_via_atomic_replace(tmp_path) -> None:
    path = tmp_path / "stats.json"
    save_stats({"inference_requests_total": 1}, path)
    assert path.is_file()
    assert load_stats(path)["inference_requests_total"] == 1
    # No stray temp files left beside the final JSON name.
    assert not list(tmp_path.glob(".stats.*.tmp"))


def test_save_and_record_stats_round_trip(tmp_path) -> None:
    path = tmp_path / "voxium" / "stats.json"
    save_stats({"inference_requests_total": "2", "by_source": {"ptt": "2"}}, path)

    out = record_stats(
        {
            "audio_seconds": 2,
            "input_bytes": 100,
            "model": {"decoder_tokens": 3, "output_words": 2},
            "polish": {"tokens_in": 4, "tokens_out": 5},
        },
        source="retry",
        path=path,
    )

    assert out["inference_requests_total"] == 3
    assert out["by_source"]["ptt"] == 2
    assert out["by_source"]["retry"] == 1
    assert load_stats(path)["polish_tokens_total"] == 9
