"""Pydantic config model."""

from voxium.config import VoxiumUserConfig


def test_voxium_user_config_parses_minimal_yaml() -> None:
    raw = {
        "hotkeys": {"record": "F13"},
        "transcription": {"server_url": "http://127.0.0.1:8002/v1/"},
    }
    cfg = VoxiumUserConfig.model_validate(raw)
    assert cfg.hotkeys["record"] == "F13"
    assert cfg.transcription["server_url"].endswith("8002/v1/")
