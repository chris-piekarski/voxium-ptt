"""
Sliding-window streaming decoder for the ``/transcribe-stream`` WebSocket route.

This module is intentionally pure: no FastAPI, no async, no app-level globals. It owns
one rolling audio buffer and runs ``model.transcribe`` over it under a caller-supplied
lock. The WebSocket plumbing in :mod:`voxium.whisper_server` constructs one decoder per
session and drives :meth:`SlidingWindowDecoder.push` from each incoming audio frame.

Wire contract:

- Audio frames are float32 little-endian, 16 kHz mono. Same dtype as the rest of the
  capture pipeline (see :mod:`voxium.app`); no int16 round-trip.
- Each :meth:`push` re-decodes the **entire current 5 s window** with ``beam_size=1`` and
  ``condition_on_previous_text=False``. Emits a :class:`StreamPartial` with the full
  window text, plus timing / suppression flags.
- :meth:`finalize` runs one last pass over whatever audio is in the buffer and tags the
  result ``is_final=True``. Idempotent on empty buffers.

Plan: see ``docs/plans/live-transcribe-stream.md`` §3.3.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from voxium.constants import SAMPLE_RATE
from voxium.speech_guards import is_hallucination

DEFAULT_WINDOW_SECONDS: float = 5.0
DEFAULT_BEAM_SIZE: int = 1
PROTOCOL_VERSION: int = 1


@dataclass(frozen=True)
class StreamPartial:
    """One re-decode result. Server emits this as a JSON ``partial`` frame."""

    seq: int
    text: str
    audio_seconds: float
    decode_ms: float
    is_final: bool
    suppressed: bool


class SlidingWindowDecoder:
    """
    Per-session re-decode of a rolling audio buffer using a shared faster-whisper
    ``WhisperModel`` instance.

    The model is shared with the batch ``/transcribe`` path (and possibly other
    streaming sessions), so every call into ``model.transcribe`` is serialized via
    ``model_lock``. Holding the lock for ~50–80 ms per re-decode is acceptable on
    loopback; the alternative would be one model per session, which is unaffordable
    on VRAM.

    Pinned defaults match ``docs/plans/live-transcribe-stream.md`` §3.3:
      * 5-second rolling window
      * beam_size=1 (streaming-class throughput; batch path keeps the higher beam)
      * condition_on_previous_text=False
      * vad_filter=True (Silero, server-side; reduces silence-induced hallucinations)
      * is_hallucination filter on output text (overridable via ``suppress_hallucinations``)
    """

    SAMPLE_RATE: int = SAMPLE_RATE

    def __init__(
        self,
        model: Any,
        model_lock: threading.Lock,
        *,
        language: str | None = None,
        vad_filter: bool = True,
        suppress_hallucinations: bool = True,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        beam_size: int = DEFAULT_BEAM_SIZE,
    ) -> None:
        self._model = model
        self._model_lock = model_lock
        self.language = language
        self.vad_filter = vad_filter
        self.suppress_hallucinations = suppress_hallucinations
        self.window_seconds = float(window_seconds)
        self.beam_size = int(beam_size)
        self._max_samples = int(self.window_seconds * self.SAMPLE_RATE)
        self._buffer: np.ndarray = np.empty(0, dtype=np.float32)
        self._seq: int = 0

    @property
    def buffer_seconds(self) -> float:
        return self._buffer.size / float(self.SAMPLE_RATE)

    def reset(self) -> None:
        """Clear the buffer and reset the sequence counter. Use between sessions."""
        self._buffer = np.empty(0, dtype=np.float32)
        self._seq = 0

    def push(self, pcm_float32: np.ndarray) -> StreamPartial:
        """
        Append samples, trim to ``window_seconds``, and re-decode.

        Samples must be float32 mono at :data:`SAMPLE_RATE`. Wrong dtype or shape raises
        :class:`ValueError` so callers can return a clean wire error before processing.
        """
        if not isinstance(pcm_float32, np.ndarray):
            raise ValueError("pcm_float32 must be a numpy.ndarray")
        if pcm_float32.dtype != np.float32:
            raise ValueError(
                f"pcm_float32 must be dtype float32, got {pcm_float32.dtype!s}"
            )
        if pcm_float32.ndim != 1:
            raise ValueError(
                f"pcm_float32 must be 1-D mono samples, got shape {pcm_float32.shape!r}"
            )
        # Append and trim. Copy on append so the wire buffer can be reused by the caller.
        if pcm_float32.size:
            if self._buffer.size:
                self._buffer = np.concatenate(
                    (self._buffer, pcm_float32.astype(np.float32, copy=False))
                )
            else:
                self._buffer = pcm_float32.astype(np.float32, copy=True)
            if self._buffer.size > self._max_samples:
                self._buffer = self._buffer[-self._max_samples :].copy()
        return self._decode(is_final=False)

    def finalize(self) -> StreamPartial:
        """One last decode pass over the residual buffer. Tags the partial as final."""
        partial = self._decode(is_final=True)
        return partial

    def _decode(self, *, is_final: bool) -> StreamPartial:
        self._seq += 1
        seq = self._seq
        audio_seconds = self.buffer_seconds
        if self._buffer.size == 0:
            return StreamPartial(
                seq=seq,
                text="",
                audio_seconds=0.0,
                decode_ms=0.0,
                is_final=is_final,
                suppressed=False,
            )
        t0 = time.perf_counter()
        with self._model_lock:
            try:
                segments, _info = self._model.transcribe(
                    self._buffer,
                    language=self.language,
                    beam_size=self.beam_size,
                    condition_on_previous_text=False,
                    vad_filter=self.vad_filter,
                )
                # ``segments`` is a generator in faster-whisper; force materialization
                # while the lock is held so downstream timing reflects the full decode.
                text_parts = [seg.text for seg in segments]
            except (
                Exception
            ) as exc:  # pragma: no cover - defensive; surfaces as wire error
                raise StreamingDecodeError(str(exc)) from exc
        decode_ms = (time.perf_counter() - t0) * 1000.0
        text = "".join(text_parts).strip()
        suppressed = False
        if text and self.suppress_hallucinations and is_hallucination(text):
            suppressed = True
            text = ""
        return StreamPartial(
            seq=seq,
            text=text,
            audio_seconds=audio_seconds,
            decode_ms=decode_ms,
            is_final=is_final,
            suppressed=suppressed,
        )


class StreamingDecodeError(RuntimeError):
    """Raised by the decoder when ``model.transcribe`` fails. Surfaced as a wire error."""
