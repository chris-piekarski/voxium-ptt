from pathlib import Path

import pytest

import voxium.persistent_stats as ps_mod
from voxium.persistent_stats import (
    _float_value,
    _int_value,
    accumulate_stats,
    config_stats_path,
    default_stats,
    load_stats,
    normalize_stats,
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


def test_int_value_handles_none_and_bad_types() -> None:
    assert _int_value(None) == 0
    assert _int_value("abc") == 0
    assert _int_value([1, 2]) == 0
    assert _int_value("17") == 17
    assert _int_value(3.7) == 3


def test_float_value_handles_none_and_bad_types() -> None:
    assert _float_value(None) == 0.0
    assert _float_value("nope") == 0.0
    assert _float_value({}) == 0.0
    assert _float_value("1.5") == 1.5


def test_normalize_stats_non_dict_returns_default() -> None:
    out = normalize_stats("not a dict")
    assert out["inference_requests_total"] == 0
    assert out["by_source"] == {"ptt": 0, "vox": 0, "retry": 0}


def test_config_stats_path_under_home() -> None:
    p = config_stats_path()
    assert p.name == "stats.json"
    assert "voxium" in p.parts


def test_save_stats_cleans_up_temp_on_write_failure(tmp_path, monkeypatch) -> None:
    """If the atomic replace fails, the .tmp scratch file must not linger."""
    path = tmp_path / "stats.json"

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ps_mod.os, "replace", boom)
    with pytest.raises(OSError):
        save_stats({"inference_requests_total": 1}, path)
    # The tmp file created by mkstemp must have been cleaned up by the
    # finally/except branch in save_stats.
    assert not list(tmp_path.glob(".stats.*.tmp"))


def test_save_stats_swallows_unlink_oserror(tmp_path, monkeypatch) -> None:
    """save_stats must still raise the original error even if cleanup fails."""
    path = tmp_path / "stats.json"

    def replace_boom(*_args, **_kwargs):
        raise OSError("replace failed")

    real_unlink = Path.unlink

    def unlink_boom(self, *args, **kwargs):
        if self.name.startswith(".stats."):
            raise OSError("unlink failed")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(ps_mod.os, "replace", replace_boom)
    monkeypatch.setattr(Path, "unlink", unlink_boom)
    with pytest.raises(OSError, match="replace failed"):
        save_stats({"inference_requests_total": 1}, path)
