"""Morse/CW audio generation and playback control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading

import numpy as np

from voxium.morse_code import MORSE_ALPHABET

MorsePlayFunc = Callable[[np.ndarray, int, threading.Event], None]
MorseStopFunc = Callable[[], None]
MorseStateCallback = Callable[[bool], None]


@dataclass(frozen=True)
class MorseAudioConfig:
    sample_rate: int = 44_100
    wpm: float = 18.0
    frequency_hz: float = 700.0
    amplitude: float = 0.18
    max_chars: int = 160


def morse_timing_units_for_text(
    text: str, *, max_chars: int = 160
) -> list[tuple[bool, int]]:
    """Return ``(tone_on, unit_count)`` events for text encoded as Morse."""
    raw = (text or "").upper()
    if max_chars > 0:
        raw = raw[:max_chars]
    words: list[list[str]] = []
    for word in raw.split():
        codes = [MORSE_ALPHABET[ch] for ch in word if ch in MORSE_ALPHABET]
        if codes:
            words.append(codes)

    events: list[tuple[bool, int]] = []
    for word_idx, word_codes in enumerate(words):
        if word_idx:
            events.append((False, 7))
        for char_idx, code in enumerate(word_codes):
            if char_idx:
                events.append((False, 3))
            for symbol_idx, symbol in enumerate(code):
                if symbol_idx:
                    events.append((False, 1))
                events.append((True, 3 if symbol == "-" else 1))
    return events


def morse_signal_for_text(
    text: str,
    *,
    sample_rate: int = 44_100,
    wpm: float = 18.0,
    frequency_hz: float = 700.0,
    amplitude: float = 0.18,
    max_chars: int = 160,
) -> np.ndarray:
    """Generate a mono float32 sine-wave Morse signal for playback."""
    sr = max(1, int(sample_rate))
    unit_seconds = 1.2 / max(1.0, float(wpm))
    amp = float(max(0.0, min(1.0, amplitude)))
    freq = max(1.0, float(frequency_hz))
    parts: list[np.ndarray] = []
    for tone_on, units in morse_timing_units_for_text(text, max_chars=max_chars):
        frames = max(1, int(round(unit_seconds * units * sr)))
        if not tone_on:
            parts.append(np.zeros(frames, dtype=np.float32))
            continue
        t = np.arange(frames, dtype=np.float32) / float(sr)
        tone = (np.sin(2.0 * np.pi * freq * t) * amp).astype(np.float32)
        fade_frames = min(frames // 2, max(1, int(sr * 0.003)))
        if fade_frames > 1:
            ramp = np.linspace(0.0, 1.0, fade_frames, dtype=np.float32)
            tone[:fade_frames] *= ramp
            tone[-fade_frames:] *= ramp[::-1]
        parts.append(tone)
    if not parts:
        return np.empty(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


def _default_play_signal(
    signal: np.ndarray, sample_rate: int, stop_event: threading.Event
) -> None:
    import sounddevice as _sounddevice

    _sounddevice.play(signal, samplerate=sample_rate, blocking=False)
    try:
        while not stop_event.wait(0.05):
            try:
                stream = _sounddevice.get_stream()
            except Exception:
                stream = None
            if stream is None or not bool(getattr(stream, "active", False)):
                break
        if stop_event.is_set():
            _sounddevice.stop()
        else:
            _sounddevice.wait()
    finally:
        if stop_event.is_set():
            _sounddevice.stop()


class MorseAudioController:
    """Owns one background Morse playback thread."""

    def __init__(
        self,
        *,
        config: MorseAudioConfig | None = None,
        play_func: MorsePlayFunc | None = None,
        stop_func: MorseStopFunc | None = None,
        on_state_change: MorseStateCallback | None = None,
    ) -> None:
        self.config = config or MorseAudioConfig()
        self._play_func = play_func or _default_play_signal
        self._stop_func = stop_func
        self._on_state_change = on_state_change
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._playing = False

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def play_text(self, text: str) -> bool:
        signal = morse_signal_for_text(
            text,
            sample_rate=self.config.sample_rate,
            wpm=self.config.wpm,
            frequency_hz=self.config.frequency_hz,
            amplitude=self.config.amplitude,
            max_chars=self.config.max_chars,
        )
        if signal.size <= 0:
            return False
        self.stop()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run_playback,
            args=(signal, stop_event),
            name="voxium-morse-audio",
            daemon=True,
        )
        with self._lock:
            self._stop_event = stop_event
            self._thread = thread
            self._playing = True
        thread.start()
        self._notify_state(True)
        return True

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            was_playing = self._playing
            self._stop_event.set()
            self._playing = False
        if thread is not None and thread.is_alive():
            try:
                if self._stop_func is not None:
                    self._stop_func()
            except Exception:
                pass
            if thread is not threading.current_thread():
                thread.join(timeout=0.4)
        with self._lock:
            if self._thread is thread:
                self._thread = None
        if was_playing:
            self._notify_state(False)

    def toggle(self, text: str) -> bool:
        if self.is_playing():
            self.stop()
            return False
        return self.play_text(text)

    def _run_playback(self, signal: np.ndarray, stop_event: threading.Event) -> None:
        try:
            self._play_func(signal, self.config.sample_rate, stop_event)
        except Exception:
            pass
        finally:
            self._finish_playback(threading.current_thread())

    def _finish_playback(self, thread: threading.Thread) -> None:
        with self._lock:
            if self._thread is not thread:
                return
            self._thread = None
            was_playing = self._playing
            self._playing = False
        if was_playing:
            self._notify_state(False)

    def _notify_state(self, playing: bool) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(playing)
        except Exception:
            pass
