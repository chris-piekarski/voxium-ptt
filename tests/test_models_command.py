"""Tests for `voxium models` command behavior."""

from __future__ import annotations

import json
from types import SimpleNamespace

import voxium.app as app
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    trusted_polish_model,
)
from voxium.polish_provision import ProvisionedPolishAssets


def _args(**overrides) -> SimpleNamespace:
    defaults = {
        "lane": None,
        "action": None,
        "model_id": None,
        "json": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_run_models_command_summary_json_has_both_lanes(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    model_path = tmp_path / "models" / "polish" / trusted.filename
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"gguf")

    assert app.run_models_command(_args(json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["transcribe"]["lane"] == "transcribe"
    assert payload["polish"]["lane"] == "polish"
    assert payload["polish"]["registry_default"] == DEFAULT_TRUSTED_POLISH_MODEL_ID
    trusted_rows = payload["polish"]["trusted_models"]
    default_row = next(row for row in trusted_rows if row["id"] == trusted.model_id)
    assert default_row["installed"] is True


def test_run_models_command_pull_polish_json_includes_provisioned_assets(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    model_path = tmp_path / "models" / "polish" / trusted.filename
    runtime_path = tmp_path / "tools" / "llama.cpp" / "llama-server.exe"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"gguf")
    runtime_path.write_bytes(b"exe")

    monkeypatch.setattr(
        app,
        "ensure_default_polish_assets",
        lambda model_name=None, progress=None: ProvisionedPolishAssets(
            runtime_dir=runtime_path.parent,
            runtime_exe=runtime_path,
            runtime_variant="cuda12",
            runtime_tag="b1234",
            model_path=model_path,
            model_repo_id=trusted.repo_id,
            model_filename=trusted.filename,
        ),
    )

    assert (
        app.run_models_command(
            _args(lane="polish", action="pull", model_id=trusted.model_id, json=True)
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "llama.cpp"
    assert payload["provisioned"]["ok"] is True
    assert payload["provisioned"]["runtime_exe"] == str(runtime_path.resolve())
    default_row = next(
        row for row in payload["trusted_models"] if row["id"] == trusted.model_id
    )
    assert default_row["installed"] is True


def test_run_models_command_pull_polish_json_reports_provision_failure(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        app,
        "ensure_default_polish_assets",
        lambda model_name=None, progress=None: (_ for _ in ()).throw(
            RuntimeError("download failed")
        ),
    )

    assert (
        app.run_models_command(
            _args(
                lane="polish",
                action="pull",
                model_id=DEFAULT_TRUSTED_POLISH_MODEL_ID,
                json=True,
            )
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "download failed"


def test_run_models_command_polish_pull_json_ok(monkeypatch, tmp_path, capsys) -> None:
    from pathlib import Path

    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model("gemma-2-2b-it-q5km")
    model_path = tmp_path / "models" / "polish" / trusted.filename
    runtime_path = tmp_path / "tools" / "llama.cpp" / "llama-server.exe"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"gguf")
    runtime_path.write_bytes(b"exe")

    monkeypatch.setattr(
        app,
        "ensure_default_polish_assets",
        lambda model_name=None, progress=None: ProvisionedPolishAssets(
            runtime_dir=runtime_path.parent,
            runtime_exe=runtime_path,
            runtime_variant="cuda12",
            runtime_tag="b1234",
            model_path=model_path,
            model_repo_id=trusted.repo_id,
            model_filename=trusted.filename,
        ),
    )
    assert (
        app.run_models_command(
            _args(
                lane="polish", action="pull", model_id="gemma-2-2b-it-q5km", json=True
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["lane"] == "polish"
    assert payload["provisioned"]["ok"] is True
    assert payload["provisioned"]["requested_model"] == "gemma-2-2b-it-q5km"
    assert Path(payload["provisioned"]["model_path"]).is_file()
