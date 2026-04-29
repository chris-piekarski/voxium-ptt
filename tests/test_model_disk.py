"""Local Hugging Face cache layout under ``models/`` (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxium.model_disk import hf_snapshot_has_model_bin, is_trusted_model_on_disk


def test_hf_snapshot_has_model_bin_true(tmp_path: Path) -> None:
    snap = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / "ref1"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"01")
    assert hf_snapshot_has_model_bin(tmp_path, "Systran/faster-whisper-tiny") is True


def test_hf_snapshot_has_model_bin_false_missing(tmp_path: Path) -> None:
    assert hf_snapshot_has_model_bin(tmp_path, "Systran/faster-whisper-tiny") is False


def test_hf_snapshot_iterdir_fails_unreadable_dir(tmp_path: Path) -> None:
    import stat

    tree = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / "a"
    tree.mkdir(parents=True)
    (tree / "model.bin").write_bytes(b"1")
    p_snap = tree.parent
    p_snap.chmod(0)
    try:
        assert (
            hf_snapshot_has_model_bin(tmp_path, "Systran/faster-whisper-tiny") is False
        )
    finally:
        p_snap.chmod(stat.S_IRWXU)


def test_is_trusted_rejects_unknown_name() -> None:
    assert is_trusted_model_on_disk("not-a-known-model", root=Path("/")) is False


def test_is_trusted_model_on_disk_uses_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("voxium.model_disk.models_dir", lambda: tmp_path)
    snap = tmp_path / "models--Systran--faster-whisper-base" / "snapshots" / "z"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    assert is_trusted_model_on_disk("base") is True
    assert is_trusted_model_on_disk("tiny") is False
