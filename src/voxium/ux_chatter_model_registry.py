"""Trusted UX-chatter (Gemma) GGUF metadata for optional console copy (llama.cpp, not STT or polish)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from voxium.paths import ux_models_dir

# HF repo: google/gemma-3-1b-it-qat-q4_0-gguf (Gemma access may be gated; accept the license on HF first).
_DEFAULT_FILENAME = "gemma-3-1b-it-q4_0.gguf"

# Public TheBloke build — used when Gemma is absent and Hugging Face blocks the gated download.
_FALLBACK_TINY_FILENAME = "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"


@dataclass(frozen=True)
class UxChatterModel:
    model_id: str
    repo_id: str
    filename: str
    description: str

    def local_path(self, root: Path | None = None) -> Path:
        base = Path(root) if root is not None else ux_models_dir()
        return (base / self.filename).resolve()


DEFAULT_UX_CHATTER: UxChatterModel = UxChatterModel(
    model_id="gemma-3-1b-it-q4_0",
    repo_id="google/gemma-3-1b-it-qat-q4_0-gguf",
    filename=_DEFAULT_FILENAME,
    description="Tiny instruct GGUF for short on-screen radio-flavor one-liners (UX only, default off).",
)

FALLBACK_UX_CHATTER: UxChatterModel = UxChatterModel(
    model_id="tinyllama-1.1b-chat-v1.0.Q4_K_M",
    repo_id="TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
    filename=_FALLBACK_TINY_FILENAME,
    description="TheBloke TinyLlama chat Q4_K_M — public fallback when the default Gemma GGUF is not available (HF gate).",
)

DEFAULT_UX_CHATTER_API_MODEL = DEFAULT_UX_CHATTER.model_id


def primary_ux_chatter_on_disk() -> tuple[Path, UxChatterModel] | None:
    p = DEFAULT_UX_CHATTER.local_path()
    if p.is_file():
        return (p, DEFAULT_UX_CHATTER)
    return None


def fallback_ux_chatter_on_disk() -> tuple[Path, UxChatterModel] | None:
    p = FALLBACK_UX_CHATTER.local_path()
    if p.is_file():
        return (p, FALLBACK_UX_CHATTER)
    return None


def resolve_ux_chatter_path_on_disk() -> tuple[Path, UxChatterModel] | None:
    """Prefer Gemma, then TheBloke TinyLlama, if a file is already under ``models/ux/``."""
    r = primary_ux_chatter_on_disk()
    if r is not None:
        return r
    return fallback_ux_chatter_on_disk()
