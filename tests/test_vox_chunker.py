"""Unit tests for VOX utterance chunker."""

import numpy as np
import pytest

from voxium.vox_chunker import UtteranceChunker, _rms


def test_rms() -> None:
    assert _rms(np.zeros(10, dtype=np.float32)) < 1e-9


def test_chunker_rejects_invalid_uncertain_weight() -> None:
    with pytest.raises(ValueError, match="uncertain_silence_weight"):
        UtteranceChunker(16_000, uncertain_silence_weight=1.01)
    t = 0.4 * np.ones(480, dtype=np.float32)
    assert _rms(t) > 0.3


def test_chunker_emits_speech_then_silence() -> None:
    sr = 16_000
    ch = UtteranceChunker(sr, pre_roll_ms=0.0)
    s = 0.25  # “speech”
    t_sp = np.sin(np.linspace(0, 2 * np.pi, int(sr * 0.4))).astype(np.float32) * s
    t_sil = np.zeros(int(sr * 2.8), dtype=np.float32)  # must exceed default hangover
    t = np.concatenate([t_sp, t_sil])
    out: list = []
    step = 480
    for i in range(0, t.size, step):
        out.extend(ch.feed(t[i : i + step]))
    assert ch.chunks_emitted >= 1
    assert all(x.size > 0 for x in out)


def test_silence_only_nothing() -> None:
    ch = UtteranceChunker(16_000, pre_roll_ms=0.0)
    z = np.zeros(16_000 * 2, dtype=np.float32)
    out: list = []
    for i in range(0, z.size, 4000):
        out.extend(ch.feed(z[i : i + 4000]))
    assert ch.chunks_emitted == 0
    assert out == []


def test_utterance_ends_when_rms_between_silence_and_speech_thresholds() -> None:
    """RMS at or below the silence floor ends quickly; mid band uses uncertain weighting."""
    sr = 16_000
    ch = UtteranceChunker(
        sr,
        pre_roll_ms=0.0,
        speech_rms=0.012,
        silence_rms=0.007,
    )
    t_sp = np.sin(np.linspace(0, 2 * np.pi, int(sr * 0.4))).astype(np.float32) * 0.25
    t_tail = np.full(int(sr * 2.8), 0.005, dtype=np.float32)
    t = np.concatenate([t_sp, t_tail])
    out: list = []
    step = 480
    for i in range(0, t.size, step):
        out.extend(ch.feed(t[i : i + step]))
    assert ch.chunks_emitted >= 1
    assert all(x.size > 0 for x in out)


def test_rms_fewer_than_two_samples() -> None:
    assert _rms(np.array([0.5], dtype=np.float32)) == 0.0
    assert _rms(np.array([], dtype=np.float32)) == 0.0


def test_state_label_listening_utterance_gating() -> None:
    ch = UtteranceChunker(16_000, pre_roll_ms=0.0)
    assert ch.state_label() == "listening"
    loud = (
        np.sin(np.linspace(0, 2 * np.pi, 480)).astype(np.float32) * 0.25
    )  # one frame @ 16k/30ms
    ch.feed(loud)
    assert ch.state_label() == "utterance"
    ch.feed(np.zeros(480, dtype=np.float32))
    assert ch.state_label() == "gating"


def test_feed_empty_buffer_no_op() -> None:
    ch = UtteranceChunker(16_000, pre_roll_ms=0.0)
    assert ch.feed(np.array([], dtype=np.float32)) == []


def test_emit_trim_tail_too_short_returns_none() -> None:
    ch = UtteranceChunker(16_000, pre_roll_ms=0.0, min_utterance_ms=300.0)
    out = ch._emit_trim_tail(np.zeros(200, dtype=np.float32))  # noqa: SLF001
    assert out is None


def test_mid_utterance_1_5s_gap_stays_one_chunk_until_final_pause() -> None:
    """A 1.5s silent gap between two speech bursts does not end the take (default hang > 2s)."""
    sr = 16_000
    ch = UtteranceChunker(sr, pre_roll_ms=0.0)
    s = 0.25
    t1 = np.sin(np.linspace(0, 2 * np.pi, int(sr * 0.3))).astype(np.float32) * s
    gap = np.zeros(int(sr * 1.5), dtype=np.float32)
    t2 = np.sin(np.linspace(0, 2 * np.pi, int(sr * 0.2))).astype(np.float32) * s
    tail = np.zeros(int(sr * 2.8), dtype=np.float32)
    t = np.concatenate([t1, gap, t2, tail])
    out: list = []
    for i in range(0, t.size, 480):
        out.extend(ch.feed(t[i : i + 480]))
    assert ch.chunks_emitted == 1
    assert len(out) == 1


def test_max_utterance_splits_with_mid_stream_emit() -> None:
    # max_n=800 (50ms @ 16k), min_n small; long loud segment triggers split in feed()
    ch = UtteranceChunker(
        16_000,
        pre_roll_ms=0.0,
        min_utterance_ms=20.0,
        max_utterance_ms=50.0,
        hangover_ms=2_000.0,
    )
    n = 3 * 480  # 1440 > 800
    loud = (
        np.sin(np.linspace(0, 8 * np.pi, n)).astype(np.float32) * 0.2
    )
    done: list = []
    for i in range(0, n, 480):
        done.extend(ch.feed(loud[i : i + 480]))
    assert ch.chunks_emitted >= 1
    assert done and all(x.size > 0 for x in done)
