"""Heuristics for “junk” transcriptions and energy-based speech detection (testable)."""

from __future__ import annotations

import numpy as np

HALLUCINATION_PHRASES: tuple[str, ...] = (
    "thanks for watching",
    "thank you for watching",
    "thanks for listening",
    "thank you for listening",
    "thank you",
    "thanks",
    "subscribe",
    "like and subscribe",
    "see you next time",
    "see you later",
    "the end",
    "silence",
    "no speech",
    "inaudible",
    "[music]",
    "(music)",
    "please subscribe",
    "don't forget to subscribe",
    "hit the bell",
    "leave a comment",
    "see you in the next",
    "bye bye",
    "good bye",
    "take care",
    "have a nice day",
    "have a good day",
    "peace out",
    "cheers",
    "ciao",
    "adios",
    "auf wiedersehen",
    "さようなら",
    "...",
    "♪",
    "music playing",
    "background noise",
    "applause",
)

HALLUCINATION_WORDS: frozenset[str] = frozenset(
    {
        "you",
        "i",
        "so",
        "uh",
        "um",
        "hmm",
        "huh",
        "ah",
        "oh",
        "bye",
        "goodbye",
        "thanks",
        "okay",
        "ok",
        "yes",
        "no",
        "yeah",
        "yep",
        "nope",
        "well",
        "right",
        "hey",
        "hi",
        "hello",
        "what",
        "hm",
    }
)


def is_hallucination(text: str) -> bool:
    t = text.lower().strip()
    if len(t) < 3:
        return True

    if t in HALLUCINATION_WORDS:
        return True

    if len(t) < 40:
        return any(phrase in t for phrase in HALLUCINATION_PHRASES)
    return False


def has_speech(
    audio: np.ndarray, sample_rate: int, threshold: float = 0.01, segment_ms: int = 50
) -> bool:
    segment_samples = int(sample_rate * segment_ms / 1000)

    for i in range(0, len(audio), segment_samples):
        segment = audio[i : i + segment_samples]
        if len(segment) < segment_samples // 2:
            continue
        energy = float(np.sqrt(np.mean(segment**2)))
        if energy > threshold:
            return True

    return False
