"""Repository-local paths (no platform ``~/.cache`` for Voxium data)."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """
    Project root: ``VOXIUM_REPO_ROOT`` if set, else the directory that contains
    ``pyproject.toml`` when walking up from this package, else :func:`os.getcwd`.
    """
    env = os.environ.get("VOXIUM_REPO_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for d in (here, *here.parents):
        if (d / "pyproject.toml").is_file():
            return d
    return Path.cwd().resolve()


def models_dir() -> Path:
    return repo_root() / "models"


def polish_models_dir() -> Path:
    """Repository-local GGUF polish models."""
    return models_dir() / "polish"


def ux_models_dir() -> Path:
    """Repository-local GGUF for optional console UX chatter (Gemma, llama.cpp)."""
    return models_dir() / "ux"


def ollama_models_dir() -> Path:
    """Backward-compatible alias; the polish path now uses local GGUF files."""
    return polish_models_dir()


def tools_dir() -> Path:
    return repo_root() / "tools"


def llama_cpp_dir() -> Path:
    """Repository-local llama.cpp runtime directory."""
    return tools_dir() / "llama.cpp"


def logs_dir() -> Path:
    return repo_root() / "logs"


def default_server_log_path() -> Path:
    return logs_dir() / "voxium_server.log"


def instance_lock_path() -> Path:
    return logs_dir() / "voxium.lock"


def ensure_runtime_dirs() -> None:
    for d in (
        models_dir(),
        polish_models_dir(),
        ux_models_dir(),
        llama_cpp_dir(),
        logs_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)
