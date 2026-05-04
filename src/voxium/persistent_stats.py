"""Persistent local usage counters for Voxium operator stats."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SOURCE_KEYS: tuple[str, ...] = ("ptt", "vox", "retry")

DEFAULT_STATS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "started_at": None,
    "updated_at": None,
    "inference_requests_total": 0,
    "by_source": {"ptt": 0, "vox": 0, "retry": 0},
    "audio_seconds_total": 0.0,
    "input_bytes_total": 0,
    "transcription_seconds_total": 0.0,
    "request_seconds_total": 0.0,
    "decoder_tokens_total": 0,
    "polish_prompt_tokens_total": 0,
    "polish_completion_tokens_total": 0,
    "polish_tokens_total": 0,
    "output_chars_total": 0,
    "output_words_total": 0,
}


def default_stats() -> dict[str, Any]:
    return deepcopy(DEFAULT_STATS)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def config_stats_path() -> Path:
    return Path.home() / ".config" / "voxium" / "stats.json"


def _int_value(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_source(source: str | None) -> str:
    s = (source or "").strip().lower()
    return s if s in SOURCE_KEYS else "ptt"


def normalize_stats(raw: Any) -> dict[str, Any]:
    stats = default_stats()
    if not isinstance(raw, dict):
        return stats
    stats["schema_version"] = SCHEMA_VERSION
    for key in ("started_at", "updated_at"):
        value = raw.get(key)
        stats[key] = value if isinstance(value, str) and value.strip() else None
    stats["inference_requests_total"] = _int_value(raw.get("inference_requests_total"))
    by_source = raw.get("by_source")
    if isinstance(by_source, dict):
        for source in SOURCE_KEYS:
            stats["by_source"][source] = _int_value(by_source.get(source))
    for key in (
        "audio_seconds_total",
        "transcription_seconds_total",
        "request_seconds_total",
    ):
        stats[key] = _float_value(raw.get(key))
    for key in (
        "input_bytes_total",
        "decoder_tokens_total",
        "polish_prompt_tokens_total",
        "polish_completion_tokens_total",
        "polish_tokens_total",
        "output_chars_total",
        "output_words_total",
    ):
        stats[key] = _int_value(raw.get(key))
    return stats


def load_stats(path: Path | None = None) -> dict[str, Any]:
    p = path or config_stats_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_stats()
    return normalize_stats(raw)


def save_stats(stats: dict[str, Any], path: Path | None = None) -> None:
    p = path or config_stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_stats(stats)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".stats.",
        suffix=".tmp",
        dir=str(p.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, p)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _polish_token_counts(polish: Any) -> tuple[int, int, int]:
    if not isinstance(polish, dict):
        return (0, 0, 0)
    prompt = polish.get("tokens_in")
    if prompt is None:
        prompt = polish.get("prompt_tokens")
    completion = polish.get("tokens_out")
    if completion is None:
        completion = polish.get("completion_tokens")
    total = polish.get("total_tokens")
    prompt_i = _int_value(prompt)
    completion_i = _int_value(completion)
    total_i = _int_value(total)
    if total_i == 0:
        total_i = prompt_i + completion_i
    return (prompt_i, completion_i, total_i)


def accumulate_stats(
    stats: dict[str, Any],
    metrics: dict[str, Any] | None,
    *,
    source: str = "ptt",
    now: str | None = None,
) -> dict[str, Any]:
    data = normalize_stats(stats)
    metrics = metrics if isinstance(metrics, dict) else {}
    stamp = now or utc_now_iso()
    if not data.get("started_at"):
        data["started_at"] = stamp
    data["updated_at"] = stamp

    src = normalize_source(source)
    data["inference_requests_total"] += 1
    data["by_source"][src] += 1
    data["audio_seconds_total"] += _float_value(metrics.get("audio_seconds"))
    data["input_bytes_total"] += _int_value(metrics.get("input_bytes"))
    data["transcription_seconds_total"] += _float_value(
        metrics.get("transcription_seconds")
    )
    data["request_seconds_total"] += _float_value(metrics.get("total_request_seconds"))
    data["output_chars_total"] += _int_value(metrics.get("output_chars"))

    model = metrics.get("model")
    if isinstance(model, dict):
        data["decoder_tokens_total"] += _int_value(model.get("decoder_tokens"))
        data["output_words_total"] += _int_value(model.get("output_words"))

    prompt, completion, total = _polish_token_counts(metrics.get("polish"))
    data["polish_prompt_tokens_total"] += prompt
    data["polish_completion_tokens_total"] += completion
    data["polish_tokens_total"] += total
    return data


def record_stats(
    metrics: dict[str, Any] | None,
    *,
    source: str,
    path: Path | None = None,
) -> dict[str, Any]:
    stats = accumulate_stats(load_stats(path), metrics, source=source)
    save_stats(stats, path)
    return stats
