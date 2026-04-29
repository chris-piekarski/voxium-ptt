"""Pydantic config model."""

from pathlib import Path

import pytest

from voxium.config import VoxiumUserConfig
from voxium.model_registry import DEFAULT_MODEL_NAME


def test_voxium_user_config_parses_minimal_yaml() -> None:
    raw = {
        "hotkeys": {"record": "F13"},
        "transcription": {"server_url": "http://127.0.0.1:8002/v1/"},
    }
    cfg = VoxiumUserConfig.model_validate(raw)
    assert cfg.hotkeys["record"] == "F13"
    assert cfg.transcription["server_url"].endswith("8002/v1/")


def test_load_config_file_no_file_sets_default_stt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import voxium.app as app_mod

    monkeypatch.setattr(app_mod, "CONFIG_PATH", tmp_path / "no_config.yaml")
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    d = app_mod.load_config_file()
    assert d.get("transcription", {}).get("model") == DEFAULT_MODEL_NAME


def test_load_config_file_whisper_model_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import voxium.app as app_mod

    p = tmp_path / "config.yaml"
    p.write_text("transcription:\n  model: tiny\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", p)
    monkeypatch.setenv("WHISPER_MODEL", "small.en")
    d = app_mod.load_config_file()
    assert d["transcription"]["model"] == "small.en"


def test_load_config_file_invalid_model_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import voxium.app as app_mod

    p = tmp_path / "config.yaml"
    p.write_text("transcription:\n  model: not-a-real-voxium-model\n", encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", p)
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    d = app_mod.load_config_file()
    assert d["transcription"]["model"] == DEFAULT_MODEL_NAME
