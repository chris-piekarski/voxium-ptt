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


def test_morse_controller_stop_swallows_stop_func_exception() -> None:
    """stop_func raising mid-playback must not bubble out."""
    started = threading.Event()

    def play_func(signal, sample_rate, stop):
        started.set()
        stop.wait(1.0)

    def bad_stop():
        raise RuntimeError("stop fail")

    controller = MorseAudioController(
        config=MorseAudioConfig(sample_rate=8_000, wpm=20),
        play_func=play_func,
        stop_func=bad_stop,
    )
    assert controller.play_text("e")
    assert started.wait(0.5)
    controller.stop()  # must not raise
    assert not controller.is_playing()


def test_morse_controller_swallows_state_callback_exception() -> None:
    """on_state_change raising must not crash play/stop."""
    started = threading.Event()

    def play_func(signal, sample_rate, stop):
        started.set()
        stop.wait(1.0)

    def bad_state(_playing: bool) -> None:
        raise RuntimeError("state cb fail")

    controller = MorseAudioController(
        config=MorseAudioConfig(sample_rate=8_000, wpm=20),
        play_func=play_func,
        on_state_change=bad_state,
    )
    assert controller.play_text("e")
    assert started.wait(0.5)
    controller.stop()  # must not raise


def test_morse_controller_run_playback_catches_play_func_exception() -> None:
    """_run_playback must swallow exceptions from the play_func."""
    raised = threading.Event()
    states: list[bool] = []

    def angry_play(signal, sample_rate, stop):
        raised.set()
        raise RuntimeError("boom")

    controller = MorseAudioController(
        config=MorseAudioConfig(sample_rate=8_000, wpm=20),
        play_func=angry_play,
        on_state_change=states.append,
    )
    assert controller.play_text("e") is True
    assert raised.wait(0.5)
    # The background thread should mark not-playing after the exception.
    deadline_ticks = 0
    while controller.is_playing() and deadline_ticks < 20:
        threading.Event().wait(0.02)
        deadline_ticks += 1
    assert not controller.is_playing()
    # state callback should have fired True then False eventually
    assert True in states
    assert False in states


def test_morse_controller_play_text_replaces_previous_playback() -> None:
    """A second play_text while playing should stop the first cleanly."""
    started1 = threading.Event()
    started2 = threading.Event()

    calls = []

    def play_func(signal, sample_rate, stop):
        calls.append(signal.size)
        if len(calls) == 1:
            started1.set()
        else:
            started2.set()
        stop.wait(1.0)

    controller = MorseAudioController(
        config=MorseAudioConfig(sample_rate=8_000, wpm=20),
        play_func=play_func,
    )
    assert controller.play_text("e")
    assert started1.wait(0.5)
    assert controller.play_text("t")
    assert started2.wait(0.5)
    controller.stop()
    assert len(calls) == 2
