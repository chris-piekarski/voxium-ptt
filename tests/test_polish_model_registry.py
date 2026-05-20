"""Tests for trusted polish model ids and local GGUF discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    LOCAL_POLISH_PREFIX,
    LocalPolishModel,
    POLISH_DEFAULT_MODEL,
    TrustedPolishModel,
    is_trusted_polish_model_on_disk,
    list_available_polish_models,
    list_installed_custom_polish_models,
    list_installed_trusted_polish_models,
    list_local_polish_models,
    resolve_polish_model,
    trusted_polish_model,
    trusted_polish_model_path,
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


def test_local_model_size_gib_text_thresholds() -> None:
    """size_gib_text — three branches: ≥10 GiB, ≥1 GiB, MiB fallback."""
    big = LocalPolishModel(name="x", path=Path("/x"), size_bytes=12 * (1024**3))
    assert big.size_gib_text == "12 GiB"

    med = LocalPolishModel(
        name="x", path=Path("/x"), size_bytes=2 * (1024**3) + (512 * 1024**2)
    )
    assert med.size_gib_text.endswith(" GiB") and "." in med.size_gib_text

    small = LocalPolishModel(name="x", path=Path("/x"), size_bytes=300 * (1024**2))
    assert small.size_gib_text.endswith(" MiB")


def test_local_model_local_selector_only_for_custom() -> None:
    """local_selector is None for trusted entries and for entries without a filename."""
    trusted = LocalPolishModel(
        name="qid", path=Path("/x"), size_bytes=10, trusted_id="qid", filename="x.gguf"
    )
    assert trusted.is_trusted is True
    assert trusted.local_selector is None

    no_filename = LocalPolishModel(name="x", path=Path("/x"), size_bytes=10)
    assert no_filename.local_selector is None

    custom = LocalPolishModel(
        name="x", path=Path("/x"), size_bytes=10, filename="custom/a.gguf"
    )
    assert custom.local_selector == f"{LOCAL_POLISH_PREFIX}custom/a.gguf"


def test_trusted_polish_model_rejects_unknown_id() -> None:
    with pytest.raises(ValueError, match="Unknown polish model"):
        trusted_polish_model("nope")


def test_is_trusted_polish_model_on_disk_false_for_missing(tmp_path) -> None:
    # Models dir exists but file is not there
    assert (
        is_trusted_polish_model_on_disk(DEFAULT_TRUSTED_POLISH_MODEL_ID, tmp_path)
        is False
    )


def test_trusted_polish_model_path_under_root(tmp_path) -> None:
    p = trusted_polish_model_path(DEFAULT_TRUSTED_POLISH_MODEL_ID, tmp_path)
    assert str(p).startswith(str(tmp_path.resolve()))


def test_list_local_polish_models_missing_dir_returns_empty(tmp_path) -> None:
    assert list_local_polish_models(tmp_path / "nope") == []


def test_list_installed_custom_polish_models_returns_only_custom(tmp_path) -> None:
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    _write_model(tmp_path / trusted.filename)
    _write_model(tmp_path / "side" / "extra.gguf")
    customs = list_installed_custom_polish_models(tmp_path)
    assert [m.name for m in customs] == ["local:side/extra.gguf"]


def test_list_available_polish_models_keys_match_registry_order() -> None:
    names = [m.model_id for m in list_available_polish_models()]
    assert DEFAULT_TRUSTED_POLISH_MODEL_ID in names
    assert all(
        isinstance(m, TrustedPolishModel) for m in list_available_polish_models()
    )


def test_resolve_polish_model_by_local_selector(tmp_path) -> None:
    _write_model(tmp_path / "custom" / "a.gguf")
    out = resolve_polish_model("local:custom/a.gguf", tmp_path)
    assert out.name == "local:custom/a.gguf"
