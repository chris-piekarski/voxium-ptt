"""
Runtime latency profile for the shared local ``llama-server`` lane plus the Whisper
``/transcribe`` lane.

Records the most recent ``llama_cpp_chat_completions`` calls (polish + every UX chatter
slot) and the most recent ``/transcribe`` HTTP round-trips into thread-safe ring buffers,
then renders a compact report for ``/profile``. Lets the operator see prefill vs decode
cost on the LLM side, and STT vs network-overhead vs server-handler cost on the Whisper
side, without restarting the app.

This is **runtime** profile state — in-memory only, reset on process restart. Lifetime
totals live in :mod:`voxium.persistent_stats`.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable

from voxium.llama_cpp_client import LlamaCppChatResult

_LOG = logging.getLogger(__name__)

_WINDOW_PER_SLOT = 42
# Display order in :func:`format_profile_report`. Slots not in the order list still
# render, but appended at the end.
_SLOT_ORDER: tuple[str, ...] = (
    "polish",
    "chatter_copy",
    "chatter_standby",
    "edge_inference",
    "log_subtitle",
    "rig_subtitle",
    "banner",
    "shutdown",
)


@dataclass(frozen=True)
class ProfileSample:
    slot: str
    model: str
    ok: bool
    wall_seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    prompt_n: int | None
    prompt_ms: float | None
    predicted_n: int | None
    predicted_ms: float | None
    cache_n: int | None
    error: str | None


_lock = threading.Lock()
_buffers: dict[str, Deque[ProfileSample]] = {}


@dataclass(frozen=True)
class STTSample:
    """One ``/transcribe`` round-trip: client-side wall plus server-reported breakdown."""

    model: str
    ok: bool
    client_wall_seconds: (
        float  # measured around the POST in :func:`voxium.app.transcribe`
    )
    server_total_seconds: float | None  # ``metrics.total_request_seconds``
    transcription_seconds: (
        float | None
    )  # ``metrics.transcription_seconds`` (model decode)
    audio_seconds: float | None  # clip length the server actually decoded
    realtime_factor: float | None  # ``metrics.realtime_factor`` (transcribe / audio)
    decoder_tokens: int | None  # ``metrics.model.decoder_tokens``
    error: str | None


_stt_buffer: Deque[STTSample] = deque(maxlen=_WINDOW_PER_SLOT)


def record(slot: str, *, model: str, result: LlamaCppChatResult) -> None:
    """
    Append one ``llama-server`` call sample. Failures are recorded with ok=False.

    Observability is fire-and-forget: any unexpected error is logged and swallowed so
    callers documented as "Never raises" keep that contract.
    """
    try:
        sample = ProfileSample(
            slot=slot,
            model=(model or "").strip() or "—",
            ok=bool(result.ok),
            wall_seconds=float(result.seconds or 0.0),
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            prompt_n=result.prompt_n,
            prompt_ms=result.prompt_ms,
            predicted_n=result.predicted_n,
            predicted_ms=result.predicted_ms,
            cache_n=result.cache_n,
            error=result.error,
        )
        with _lock:
            buf = _buffers.get(slot)
            if buf is None:
                buf = deque(maxlen=_WINDOW_PER_SLOT)
                _buffers[slot] = buf
            buf.append(sample)
    except Exception:  # pragma: no cover - defensive, profile must not break callers
        _LOG.debug("polish_profile.record failed", exc_info=True)


def reset() -> None:
    """Clear every slot's buffer plus the STT buffer (used by ``/profile reset``)."""
    with _lock:
        _buffers.clear()
        _stt_buffer.clear()


def snapshot() -> dict[str, list[ProfileSample]]:
    """Return a frozen copy of the per-slot buffers (for tests and the report)."""
    with _lock:
        return {slot: list(buf) for slot, buf in _buffers.items()}


def record_stt(
    *,
    model: str,
    client_wall_seconds: float,
    metrics: dict | None,
    ok: bool,
    error: str | None = None,
) -> None:
    """
    Append one ``/transcribe`` round-trip sample. ``metrics`` is the server's metrics dict.

    Same fire-and-forget contract as :func:`record`: errors are logged and swallowed.
    """
    try:
        server_total = _safe_float(metrics, "total_request_seconds")
        transcription = _safe_float(metrics, "transcription_seconds")
        audio = _safe_float(metrics, "audio_seconds")
        rtf = _safe_float(metrics, "realtime_factor")
        model_dict = metrics.get("model") if isinstance(metrics, dict) else None
        decoder_tokens = _safe_int(model_dict, "decoder_tokens")
        sample = STTSample(
            model=(model or "").strip() or "—",
            ok=bool(ok),
            client_wall_seconds=float(client_wall_seconds or 0.0),
            server_total_seconds=server_total,
            transcription_seconds=transcription,
            audio_seconds=audio,
            realtime_factor=rtf,
            decoder_tokens=decoder_tokens,
            error=error,
        )
        with _lock:
            _stt_buffer.append(sample)
    except Exception:  # pragma: no cover - defensive, profile must not break callers
        _LOG.debug("polish_profile.record_stt failed", exc_info=True)


def snapshot_stt() -> list[STTSample]:
    """Frozen copy of the STT buffer."""
    with _lock:
        return list(_stt_buffer)


def _safe_float(d: object, key: str) -> float | None:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(d: object, key: str) -> int | None:
    if not isinstance(d, dict):
        return None
    v = d.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SlotStats:
    slot: str
    n: int
    n_ok: int
    n_fail: int
    last_model: str
    wall_p50: float | None
    wall_p95: float | None
    prefill_tok_per_s: float | None  # avg over samples that have prompt_n + prompt_ms
    decode_tok_per_s: (
        float | None
    )  # avg over samples that have predicted_n + predicted_ms
    avg_prompt_ms: float | None
    avg_predicted_ms: float | None
    avg_prompt_tokens: float | None
    avg_completion_tokens: float | None


def aggregate() -> dict[str, SlotStats]:
    """Per-slot summary stats over the current window."""
    snap = snapshot()
    out: dict[str, SlotStats] = {}
    for slot, samples in snap.items():
        out[slot] = _aggregate_slot(slot, samples)
    return out


@dataclass(frozen=True)
class STTStats:
    n: int
    n_ok: int
    n_fail: int
    last_model: str
    client_wall_p50: float | None
    client_wall_p95: float | None
    avg_client_wall: float | None
    avg_server_total: float | None
    avg_transcription: float | None
    avg_audio: float | None
    avg_realtime_factor: float | None
    avg_network_overhead: float | None  # client_wall - server_total when both present


def aggregate_stt() -> STTStats:
    """Summary stats over the current STT window."""
    samples = snapshot_stt()
    n = len(samples)
    if n == 0:
        return STTStats(0, 0, 0, "—", None, None, None, None, None, None, None, None)
    ok_samples = [s for s in samples if s.ok]
    walls = sorted(s.client_wall_seconds for s in ok_samples)
    last_model = samples[-1].model
    overheads: list[float] = []
    for s in ok_samples:
        if s.server_total_seconds is not None and s.client_wall_seconds is not None:
            overheads.append(max(0.0, s.client_wall_seconds - s.server_total_seconds))
    return STTStats(
        n=n,
        n_ok=len(ok_samples),
        n_fail=n - len(ok_samples),
        last_model=last_model,
        client_wall_p50=_percentile(walls, 0.5),
        client_wall_p95=_percentile(walls, 0.95),
        avg_client_wall=_mean(s.client_wall_seconds for s in ok_samples),
        avg_server_total=_mean(
            s.server_total_seconds
            for s in ok_samples
            if s.server_total_seconds is not None
        ),
        avg_transcription=_mean(
            s.transcription_seconds
            for s in ok_samples
            if s.transcription_seconds is not None
        ),
        avg_audio=_mean(
            s.audio_seconds for s in ok_samples if s.audio_seconds is not None
        ),
        avg_realtime_factor=_mean(
            s.realtime_factor for s in ok_samples if s.realtime_factor is not None
        ),
        avg_network_overhead=_mean(overheads),
    )


def _aggregate_slot(slot: str, samples: list[ProfileSample]) -> SlotStats:
    n = len(samples)
    if n == 0:
        return SlotStats(
            slot, 0, 0, 0, "—", None, None, None, None, None, None, None, None
        )
    ok_samples = [s for s in samples if s.ok]
    walls = sorted(s.wall_seconds for s in ok_samples)
    last_model = samples[-1].model

    # Prefill rate: tokens / second using the timings block.
    prefill_rates: list[float] = []
    for s in ok_samples:
        if s.prompt_n and s.prompt_ms and s.prompt_ms > 0 and s.prompt_n > 0:
            prefill_rates.append(s.prompt_n / (s.prompt_ms / 1000.0))
    decode_rates: list[float] = []
    for s in ok_samples:
        if (
            s.predicted_n
            and s.predicted_ms
            and s.predicted_ms > 0
            and s.predicted_n > 0
        ):
            decode_rates.append(s.predicted_n / (s.predicted_ms / 1000.0))

    prompt_ms_vals = [s.prompt_ms for s in ok_samples if s.prompt_ms is not None]
    predicted_ms_vals = [
        s.predicted_ms for s in ok_samples if s.predicted_ms is not None
    ]
    pt_vals = [s.prompt_tokens for s in ok_samples if s.prompt_tokens is not None]
    ct_vals = [
        s.completion_tokens for s in ok_samples if s.completion_tokens is not None
    ]

    return SlotStats(
        slot=slot,
        n=n,
        n_ok=len(ok_samples),
        n_fail=n - len(ok_samples),
        last_model=last_model,
        wall_p50=_percentile(walls, 0.5),
        wall_p95=_percentile(walls, 0.95),
        prefill_tok_per_s=_mean(prefill_rates),
        decode_tok_per_s=_mean(decode_rates),
        avg_prompt_ms=_mean(prompt_ms_vals),
        avg_predicted_ms=_mean(predicted_ms_vals),
        avg_prompt_tokens=_mean([float(v) for v in pt_vals]),
        avg_completion_tokens=_mean([float(v) for v in ct_vals]),
    )


def _mean(values: Iterable[float]) -> float | None:
    vs = [v for v in values if v is not None]
    if not vs:
        return None
    return sum(vs) / len(vs)


def _percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = int(round(p * (len(sorted_values) - 1)))
    return float(sorted_values[max(0, min(idx, len(sorted_values) - 1))])


def _ordered_slots(stats: dict[str, SlotStats]) -> list[str]:
    seen = set(stats.keys())
    ordered = [s for s in _SLOT_ORDER if s in seen]
    extras = sorted(seen - set(ordered))
    return ordered + extras


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 10:
        return f"{value:.1f}ms"
    return f"{value:.0f}ms"


def _fmt_s(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}s"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 100:
        return f"{value:.0f} t/s"
    return f"{value:.1f} t/s"


def _fmt_tokens(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}"


def format_profile_report(*, recent_per_slot: int = 3) -> str:
    """Plain-text ``/profile`` readout: STT lane first, then per-slot LLM samples."""
    stats = aggregate()
    stt_stats = aggregate_stt()
    if not stats and stt_stats.n == 0:
        return (
            "Runtime profile: no /transcribe or llama-server calls recorded yet, copy. "
            "Trigger a take or UX chatter line first, then run /profile again."
        )

    lines: list[str] = []
    lines.append(
        "Runtime profile (last 42 samples per lane, in-memory; resets on restart):"
    )
    lines.append("")

    if stt_stats.n > 0:
        lines.extend(
            _format_stt_section(
                stt_stats, snapshot_stt(), recent_per_slot=recent_per_slot
            )
        )
        lines.append("")

    snap = snapshot()
    for slot in _ordered_slots(stats):
        st = stats[slot]
        lines.append(
            f"  {slot}  ·  n={st.n} ({st.n_ok} ok / {st.n_fail} fail)  ·  model {st.last_model}"
        )
        lines.append(
            f"    wall p50/p95: {_fmt_s(st.wall_p50)} / {_fmt_s(st.wall_p95)}"
            f"    prefill: {_fmt_rate(st.prefill_tok_per_s)} ({_fmt_ms(st.avg_prompt_ms)} avg)"
            f"    decode: {_fmt_rate(st.decode_tok_per_s)} ({_fmt_ms(st.avg_predicted_ms)} avg)"
        )
        lines.append(
            f"    tokens avg: prompt {_fmt_tokens(st.avg_prompt_tokens)}"
            f" / completion {_fmt_tokens(st.avg_completion_tokens)}"
        )
        n_recent = max(0, int(recent_per_slot))
        recents = (snap.get(slot) or [])[-n_recent:] if n_recent else []
        for s in recents:
            mark = "ok" if s.ok else "FAIL"
            extra = (
                f" prefill {_fmt_ms(s.prompt_ms)}/{s.prompt_n or 0}t"
                f" · decode {_fmt_ms(s.predicted_ms)}/{s.predicted_n or 0}t"
            )
            err = f" · err {(s.error or '')[:60]}" if not s.ok else ""
            lines.append(f"    last {mark}: {_fmt_s(s.wall_seconds)}{extra}{err}")
        lines.append("")

    lines.append(
        "Notes: STT 'network' is client wall minus server-reported total; LLM prefill rate "
        "measures system+user prompt evaluation (cache_prompt keeps the prefix warm). "
        "/profile reset clears the window."
    )
    return "\n".join(lines).rstrip() + "\n"


def _format_stt_section(
    st: STTStats, samples: list[STTSample], *, recent_per_slot: int
) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"  transcribe (STT)  ·  n={st.n} ({st.n_ok} ok / {st.n_fail} fail)"
        f"  ·  model {st.last_model}"
    )
    lines.append(
        f"    client wall p50/p95: {_fmt_s(st.client_wall_p50)} / {_fmt_s(st.client_wall_p95)}"
        f"    server total avg: {_fmt_s(st.avg_server_total)}"
        f"    network avg: {_fmt_s_or_ms(st.avg_network_overhead)}"
    )
    lines.append(
        f"    stages avg: STT {_fmt_s(st.avg_transcription)}"
        f"  ·  audio {_fmt_s(st.avg_audio)}"
        f"  ·  realtime factor {_fmt_rtf(st.avg_realtime_factor)}"
    )
    n_recent = max(0, int(recent_per_slot))
    recents = samples[-n_recent:] if n_recent else []
    for s in recents:
        mark = "ok" if s.ok else "FAIL"
        extra = (
            f" wall {_fmt_s(s.client_wall_seconds)}"
            f" · STT {_fmt_s(s.transcription_seconds)}"
            f" · audio {_fmt_s(s.audio_seconds)}"
            f" · rtf {_fmt_rtf(s.realtime_factor)}"
        )
        err = f" · err {(s.error or '')[:60]}" if not s.ok else ""
        lines.append(f"    last {mark}:{extra}{err}")
    return lines


def _fmt_s_or_ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 0.5:
        return f"{value * 1000.0:.0f}ms"
    return f"{value:.2f}s"


def _fmt_rtf(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"
