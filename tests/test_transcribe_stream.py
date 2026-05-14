"""Unit tests for the SlidingWindowDecoder (pure decoder, no FastAPI)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from voxium.transcribe_stream import (
    DEFAULT_WINDOW_SECONDS,
    PROTOCOL_VERSION,
    SlidingWindowDecoder,
    StreamPartial,
    StreamingDecodeError,
)

SAMPLE_RATE = 16_000


@dataclass
class _FakeSegment:
    text: str


class _FakeWhisperModel:
    """
    Stand-in for ``faster_whisper.WhisperModel``.

    Records each transcribe() invocation: the audio length and the kwargs we care about
    (language, beam_size, vad_filter). Returns a configurable text via ``text_fn``.
    """

    def __init__(
        self,
        text_fn=lambda buf, **_: "rolling text",
        raise_exc: Exception | None = None,
    ) -> None:
        self._text_fn = text_fn
        self._raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self, audio, **kwargs
    ):  # noqa: D401 - mimics faster-whisper signature
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append(
            {
                "audio_samples": (
                    int(audio.size) if isinstance(audio, np.ndarray) else None
                ),
                "kwargs": dict(kwargs),
            }
        )
        text = self._text_fn(audio, **kwargs)
        return ([_FakeSegment(text=text)], object())


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def test_protocol_version_is_pinned() -> None:
    assert PROTOCOL_VERSION == 1


def test_default_window_seconds_is_5() -> None:
    assert DEFAULT_WINDOW_SECONDS == pytest.approx(5.0)


def test_push_returns_monotonic_seq() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    seqs = [decoder.push(_silence(0.25)).seq for _ in range(4)]
    assert seqs == [1, 2, 3, 4]


def test_push_appends_and_decodes() -> None:
    fake = _FakeWhisperModel(text_fn=lambda buf, **_: f"len={buf.size}")
    decoder = SlidingWindowDecoder(fake, threading.Lock())
    out = decoder.push(_silence(0.5))
    assert out.text == f"len={int(0.5 * SAMPLE_RATE)}"
    assert out.audio_seconds == pytest.approx(0.5)
    assert out.is_final is False
    assert out.suppressed is False


def test_window_trims_to_max_seconds() -> None:
    fake = _FakeWhisperModel(text_fn=lambda buf, **_: f"len={buf.size}")
    decoder = SlidingWindowDecoder(fake, threading.Lock(), window_seconds=2.0)
    # Push 1s twice (total 2s) — should not trim.
    decoder.push(_silence(1.0))
    decoder.push(_silence(1.0))
    assert decoder.buffer_seconds == pytest.approx(2.0)
    # Push another 1s → buffer should slide to last 2s, dropping the oldest 1s.
    out = decoder.push(_silence(1.0))
    assert decoder.buffer_seconds == pytest.approx(2.0)
    # Sanity: model saw an audio array equal to the window length.
    assert fake.calls[-1]["audio_samples"] == int(2.0 * SAMPLE_RATE)
    assert out.audio_seconds == pytest.approx(2.0)


def test_finalize_emits_is_final_true() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    decoder.push(_silence(0.5))
    final = decoder.finalize()
    assert final.is_final is True


def test_finalize_on_empty_buffer_is_safe() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    final = decoder.finalize()
    assert final.is_final is True
    assert final.text == ""
    assert final.audio_seconds == 0.0


def test_reset_clears_buffer_and_seq() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    decoder.push(_silence(0.5))
    decoder.push(_silence(0.5))
    decoder.reset()
    assert decoder.buffer_seconds == 0.0
    out = decoder.push(_silence(0.25))
    assert out.seq == 1


def test_push_rejects_wrong_dtype() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    bad = np.zeros(SAMPLE_RATE, dtype=np.int16)
    with pytest.raises(ValueError, match="float32"):
        decoder.push(bad)


def test_push_rejects_non_array() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    with pytest.raises(ValueError, match="ndarray"):
        decoder.push(b"\x00\x00\x00\x00")  # type: ignore[arg-type]


def test_push_rejects_non_1d() -> None:
    decoder = SlidingWindowDecoder(_FakeWhisperModel(), threading.Lock())
    bad = np.zeros((2, SAMPLE_RATE), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D"):
        decoder.push(bad)


def test_hallucination_filter_suppresses_known_yt_string() -> None:
    fake = _FakeWhisperModel(text_fn=lambda buf, **_: "Thanks for watching!")
    decoder = SlidingWindowDecoder(fake, threading.Lock(), suppress_hallucinations=True)
    out = decoder.push(_silence(0.5))
    assert out.suppressed is True
    assert out.text == ""


def test_hallucination_filter_can_be_disabled() -> None:
    fake = _FakeWhisperModel(text_fn=lambda buf, **_: "Thanks for watching!")
    decoder = SlidingWindowDecoder(
        fake, threading.Lock(), suppress_hallucinations=False
    )
    out = decoder.push(_silence(0.5))
    assert out.suppressed is False
    assert out.text == "Thanks for watching!"


def test_decode_passes_pinned_kwargs() -> None:
    fake = _FakeWhisperModel()
    decoder = SlidingWindowDecoder(
        fake,
        threading.Lock(),
        language="en",
        vad_filter=False,
        beam_size=3,
    )
    decoder.push(_silence(0.5))
    kwargs = fake.calls[-1]["kwargs"]
    assert kwargs["language"] == "en"
    assert kwargs["beam_size"] == 3
    assert kwargs["vad_filter"] is False
    assert kwargs["condition_on_previous_text"] is False


def test_decode_acquires_model_lock() -> None:
    """If two threads push simultaneously, the lock serializes the model.transcribe."""
    barrier = threading.Barrier(2)
    inside_count = {"value": 0}
    inside_max = {"value": 0}
    inside_lock = threading.Lock()

    def text_fn(buf, **_):
        with inside_lock:
            inside_count["value"] += 1
            inside_max["value"] = max(inside_max["value"], inside_count["value"])
        try:
            # Brief overlap window so contention shows up if the lock is missing.
            barrier.wait(timeout=0.5)
        finally:
            with inside_lock:
                inside_count["value"] -= 1
        return "ok"

    # NOTE: barrier is set to 2; if both threads actually entered transcribe()
    # concurrently they would both reach barrier.wait and complete. With the
    # lock held, only one enters at a time and barrier.wait times out (raises
    # BrokenBarrierError), which is what we expect.
    fake = _FakeWhisperModel(text_fn=text_fn)
    lock = threading.Lock()
    d1 = SlidingWindowDecoder(fake, lock)
    d2 = SlidingWindowDecoder(fake, lock)

    errors: list[BaseException] = []

    def runner(decoder):
        try:
            decoder.push(_silence(0.25))
        except StreamingDecodeError:
            # Expected: the lock serializes entry, so barrier.wait times out
            # for whichever thread reaches it first; that becomes BrokenBarrierError
            # which the decoder wraps as StreamingDecodeError.
            pass
        except threading.BrokenBarrierError:
            pass
        except BaseException as exc:  # pragma: no cover - surface unexpected
            errors.append(exc)

    t1 = threading.Thread(target=runner, args=(d1,))
    t2 = threading.Thread(target=runner, args=(d2,))
    t1.start()
    t2.start()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not errors
    # Critical assertion: the lock kept concurrent decoder calls from overlapping.
    assert inside_max["value"] == 1


def test_decode_failure_raises_streaming_decode_error() -> None:
    fake = _FakeWhisperModel(raise_exc=RuntimeError("model crashed"))
    decoder = SlidingWindowDecoder(fake, threading.Lock())
    with pytest.raises(StreamingDecodeError, match="model crashed"):
        decoder.push(_silence(0.5))


def test_stream_partial_is_frozen() -> None:
    p = StreamPartial(
        seq=1,
        text="hi",
        audio_seconds=1.0,
        decode_ms=10.0,
        is_final=False,
        suppressed=False,
    )
    with pytest.raises(Exception):
        p.text = "no"  # type: ignore[misc]
