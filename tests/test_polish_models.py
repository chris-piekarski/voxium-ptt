"""Tests for voxium.polish_models wrapper."""

import pytest

from voxium.polish_models import validate_polish_model_tag


def test_validate_polish_model_tag_rejects_empty() -> None:
    with pytest.raises(ValueError, match="required"):
        validate_polish_model_tag("   ")


def test_validate_polish_model_tag_strips() -> None:
    out = validate_polish_model_tag("  qwen2.5-3b-q4km  ")
    assert "qwen" in out
