"""Tests for voxium.json_sanitize."""

import numpy as np
import pytest

from voxium.json_sanitize import json_safe_audio_value, round_audio_float


def test_json_safe_scalars():
    assert json_safe_audio_value(1) == 1
    assert json_safe_audio_value(1.5) == 1.5
    assert json_safe_audio_value("x") == "x"
    assert json_safe_audio_value(None) is None


def test_json_safe_numpy():
    assert json_safe_audio_value(np.float32(2.5)) == pytest.approx(2.5)
    assert json_safe_audio_value([np.int64(3), 1.0]) == [3, 1.0]


def test_json_safe_fallback_str():
    class Weird:
        def __str__(self):
            return "z"

    assert json_safe_audio_value(Weird()) == "z"


def test_round_audio_float():
    assert round_audio_float(1.23456789) == 1.234568
    assert round_audio_float(None) is None
    assert round_audio_float("nope") is None
