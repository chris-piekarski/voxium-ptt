import threading

import numpy as np

from voxium.morse_audio import (
    MorseAudioConfig,
    MorseAudioController,
    morse_signal_for_text,
    morse_timing_units_for_text,
)


def test_morse_timing_units_words_and_letters() -> None:
    assert morse_timing_units_for_text("ET") == [
        (True, 1),
        (False, 3),
        (True, 3),
    ]
    assert morse_timing_units_for_text("E T") == [
        (True, 1),
        (False, 7),
        (True, 3),
    ]


def test_morse_signal_for_text_generates_bounded_float_audio() -> None:
    sig = morse_signal_for_text(
        "sos",
        sample_rate=8_000,
        wpm=20,
        frequency_hz=600,
        amplitude=0.25,
    )

    assert sig.dtype == np.float32
    assert sig.size > 0
    assert float(np.max(np.abs(sig))) <= 0.26
    assert morse_signal_for_text("☄", sample_rate=8_000).size == 0


def test_morse_audio_controller_starts_and_stops_playback() -> None:
    started = threading.Event()
    calls: list[tuple[int, int]] = []
    states: list[bool] = []

    def play_func(signal: np.ndarray, sample_rate: int, stop: threading.Event) -> None:
        calls.append((int(signal.size), sample_rate))
        started.set()
        stop.wait(1.0)

    controller = MorseAudioController(
        config=MorseAudioConfig(sample_rate=8_000, wpm=20),
        play_func=play_func,
        on_state_change=states.append,
    )

    assert controller.play_text("e")
    assert started.wait(0.5)
    assert controller.is_playing()
    controller.stop()

    assert not controller.is_playing()
    assert calls and calls[0][0] > 0 and calls[0][1] == 8_000
    assert states[0] is True
    assert states[-1] is False


def test_morse_audio_controller_rejects_empty_encoding() -> None:
    states: list[bool] = []
    controller = MorseAudioController(on_state_change=states.append)

    assert not controller.play_text("☄")
    assert not controller.toggle("☄")
    assert states == []
