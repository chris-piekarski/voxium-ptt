"""Lightweight import and registry checks (no audio, no server)."""

import pytest

from voxium.model_registry import (
    TRUSTED_MODELS,
    resolve_model_repo,
    validate_model_name,
)


def test_validate_model_name_accepts_default():
    name = next(iter(TRUSTED_MODELS))
    assert validate_model_name(name) == name


def test_validate_model_name_rejects_untrusted():
    with pytest.raises(ValueError, match="unsupported model"):
        validate_model_name("not-a-real-allowed-voxium-id")


def test_resolve_model_repo_uses_trusted_key():
    name = next(iter(TRUSTED_MODELS))
    repo = resolve_model_repo(name)
    assert repo == TRUSTED_MODELS[name]["repo"]


def test_import_voxium_module():
    import voxium

    assert callable(voxium.main)


def test_voxium_getattr_unknown():
    import voxium

    with pytest.raises(AttributeError, match="has no attribute"):
        _ = voxium.nope_likely_missing
