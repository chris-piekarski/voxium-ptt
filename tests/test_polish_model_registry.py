"""Tests for trusted polish model ids and local GGUF discovery."""

from __future__ import annotations

import pytest

from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
    list_installed_trusted_polish_models,
    list_local_polish_models,
    resolve_polish_model,
    trusted_polish_model,
    validate_polish_model_name,
)


def _write_model(path, size: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


def test_list_local_polish_models_reports_trusted_ids_and_local_selectors(
    tmp_path,
) -> None:
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    _write_model(tmp_path / trusted.filename)
    _write_model(tmp_path / "custom" / "shell.gguf")

    models = list_local_polish_models(tmp_path)

    assert [model.name for model in models] == [
        DEFAULT_TRUSTED_POLISH_MODEL_ID,
        "local:custom/shell.gguf",
    ]


def test_list_installed_trusted_polish_models_filters_custom_locals(tmp_path) -> None:
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    _write_model(tmp_path / trusted.filename)
    _write_model(tmp_path / "custom.gguf")

    models = list_installed_trusted_polish_models(tmp_path)

    assert [model.name for model in models] == [DEFAULT_TRUSTED_POLISH_MODEL_ID]


def test_validate_polish_model_name_accepts_auto_and_trusted_ids() -> None:
    assert validate_polish_model_name(None) == POLISH_DEFAULT_MODEL
    assert validate_polish_model_name("auto") == POLISH_DEFAULT_MODEL
    assert (
        validate_polish_model_name(DEFAULT_TRUSTED_POLISH_MODEL_ID)
        == DEFAULT_TRUSTED_POLISH_MODEL_ID
    )


def test_validate_polish_model_name_accepts_trusted_filename_alias() -> None:
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)

    assert (
        validate_polish_model_name(trusted.filename) == DEFAULT_TRUSTED_POLISH_MODEL_ID
    )


def test_validate_polish_model_name_accepts_local_selector_and_relative_name(
    tmp_path,
) -> None:
    _write_model(tmp_path / "coder" / "shell.gguf")

    assert (
        validate_polish_model_name("local:coder/shell.gguf", tmp_path)
        == "local:coder/shell.gguf"
    )
    assert (
        validate_polish_model_name("coder/shell.gguf", tmp_path)
        == "local:coder/shell.gguf"
    )


def test_resolve_polish_model_auto_uses_registry_default_when_installed(
    tmp_path,
) -> None:
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    _write_model(tmp_path / trusted.filename)

    resolved = resolve_polish_model(POLISH_DEFAULT_MODEL, tmp_path)

    assert resolved.name == DEFAULT_TRUSTED_POLISH_MODEL_ID
    assert resolved.path == (tmp_path / trusted.filename).resolve()


def test_resolve_polish_model_auto_rejects_missing_registry_default(tmp_path) -> None:
    _write_model(tmp_path / "custom.gguf")

    with pytest.raises(ValueError, match="Registry default polish model"):
        resolve_polish_model(POLISH_DEFAULT_MODEL, tmp_path)


def test_resolve_polish_model_rejects_ambiguous_custom_basename(tmp_path) -> None:
    _write_model(tmp_path / "a" / "shared.gguf")
    _write_model(tmp_path / "b" / "shared.gguf")

    with pytest.raises(ValueError, match="ambiguous"):
        resolve_polish_model("shared.gguf", tmp_path)


def test_resolve_polish_model_rejects_missing_model(tmp_path) -> None:
    with pytest.raises(ValueError, match="Unknown polish model"):
        resolve_polish_model("missing.gguf", tmp_path)
