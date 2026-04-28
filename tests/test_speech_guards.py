"""Tests for voxium.speech_guards heuristics."""

import numpy as np

from voxium.speech_guards import has_speech, is_hallucination


def test_is_hallucination_short():
    assert is_hallucination("ab") is True


def test_is_hallucination_single_word_filler():
    assert is_hallucination("uh") is True
    assert is_hallucination(" no ") is True  # HALLUCINATION_WORDS


def test_is_hallucination_whole_token_len_at_least_three() -> None:
    """``t in HALLUCINATION_WORDS`` after length check (e.g. ``yes`` / ``yep`` are 3+ chars)."""
    assert is_hallucination("yes") is True
    assert is_hallucination("yep") is True


def test_is_hallucination_phrase_in_short_text():
    assert is_hallucination("  Thanks for watching! ") is True


def test_is_hallucination_long_text_not_phrase_match():
    long = "a" * 100 + " unique_content_that_is_not_in_list_zzzz_1234"
    assert is_hallucination(long) is False


def test_has_speech_silent_audio():
    a = np.zeros(1600, dtype=np.float32)  # 0.1s at 16kHz, segment 50ms
    assert has_speech(a, 16000, threshold=0.01) is False


def test_has_speech_skips_tiny_trailing_segment():
    # One full segment, then a fragment shorter than half a segment
    a = np.concatenate(
        [np.zeros(800, dtype=np.float32), np.ones(20, dtype=np.float32) * 0.2]
    )
    _ = has_speech(a, 16000, threshold=0.01)  # exercises continue branch


def test_has_speech_with_energy():
    a = np.ones(2000, dtype=np.float32) * 0.1
    assert has_speech(a, 16000, threshold=0.01) is True
