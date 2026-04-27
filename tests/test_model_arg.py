"""Tests for voxium.model_arg."""

import argparse

import pytest

from voxium.model_arg import trusted_model_arg
from voxium.model_registry import TRUSTED_MODELS, validate_model_name


def test_trusted_model_arg_ok():
    name = next(iter(TRUSTED_MODELS))
    assert trusted_model_arg(name) == validate_model_name(name)


def test_trusted_model_arg_type_error():
    with pytest.raises(argparse.ArgumentTypeError, match="unsupported model"):
        trusted_model_arg("___not_in_registry___")
