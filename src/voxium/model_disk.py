"""Detect whether a trusted model's Hugging Face snapshot exists under :func:`voxium.paths.models_dir` (no imports)."""

from __future__ import annotations

from pathlib import Path

from voxium.model_registry import TRUSTED_MODELS, resolve_model_repo
from voxium.paths import models_dir


def _repo_cache_base(root: Path, repo_id: str) -> Path:
    org, name = repo_id.split("/", 1)
    return root / f"models--{org}--{name}"


def hf_snapshot_has_model_bin(root: Path | None, repo_id: str) -> bool:
    """
    True if a ``snapshots/<ref>/model.bin`` exists under the Hub-style tree for ``repo_id``.

    Layout matches ``huggingface_hub`` with ``cache_dir`` = Voxium's ``models/`` (see
    :func:`voxium.whisper_server._download_hf_snapshot_to_models_dir`).
    """
    r = (root or models_dir()).resolve()
    snap = _repo_cache_base(r, repo_id) / "snapshots"
    if not snap.is_dir():
        return False
    try:
        for d in snap.iterdir():
            if d.is_dir() and (d / "model.bin").is_file():
                return True
    except OSError:
        return False
    return False


def is_trusted_model_on_disk(model_name: str, *, root: Path | None = None) -> bool:
    """True if the allow-listed model's repo snapshot is present on disk (``model.bin`` in a snapshot)."""
    if model_name not in TRUSTED_MODELS:
        return False
    return hf_snapshot_has_model_bin(root, resolve_model_repo(model_name))
