"""Download the optional UX-chatter Gemma GGUF (``models/ux/``) from Hugging Face."""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from pathlib import Path

from huggingface_hub import hf_hub_download
from tqdm import tqdm

from voxium.paths import ux_models_dir
from voxium.polish_provision import VoxiumPolishHubTqdm, _set_polish_hf_line_sink
from voxium.ux_chatter_model_registry import (
    DEFAULT_UX_CHATTER,
    FALLBACK_UX_CHATTER,
    UxChatterModel,
    resolve_ux_chatter_path_on_disk,
)

_UX_HF_REPO = "google/gemma-3-1b-it-qat-q4_0-gguf"


def format_ux_chatter_hf_error(exc: BaseException) -> str:
    """
    One or two short lines for logs — no request IDs, no long HF stack traces, no duplicate
    *copy* suffix (callers add brand endings when needed).
    """
    raw = " ".join(str(exc).split())
    low = raw.lower()
    if "401" in raw or "unauthorized" in low or "cannot access gated repo" in low:
        return (
            f"HF 401 or gated: sign in (huggingface-cli login), open the model card for "
            f"{_UX_HF_REPO} and accept the Gemma terms, then retry. To skip: "
            f"--no-ux-chatter-auto-pull"
        )
    if "403" in raw or "forbidden" in low:
        return (
            f"HF 403: account may not have access—confirm license acceptance for {_UX_HF_REPO}, then retry. "
            f"To skip: --no-ux-chatter-auto-pull"
        )
    # Drop verbose HF / CDN trace fragments (request IDs, root traces)
    idx = raw.lower().find("request id:")
    if idx != -1:
        raw = raw[:idx].strip(" ·|")
    cleaned = re.sub(r"Root=1-[A-Za-z0-9-;]+", "", raw)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if len(cleaned) > 320:
        cleaned = cleaned[:317] + "…"
    return (
        cleaned
        or "Hugging Face download failed (see huggingface-cli and model license)."
    )


def is_ux_gated_or_auth_hf_error(exc: BaseException) -> bool:
    """
    True when the failure is consistent with **gated** or **unauthorized** Hugging Face access
    (Gemma), so a **public** fallback model may work instead.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        s = " ".join(str(current).lower().split())
        raw = str(current)
        if "401" in raw or "403" in raw:
            return True
        if "unauthorized" in s or "forbidden" in s:
            return True
        if "gated" in s and ("repo" in s or "access" in s or "restrict" in s):
            return True
        if "cannot access" in s and "gated" in s:
            return True
        current = current.__cause__
    return False


def ensure_ux_chatter_gguf_available(
    *,
    progress: Callable[[str], None] | None = None,
    try_fallback: bool = True,
    hf_download=hf_hub_download,
) -> tuple[Path, UxChatterModel]:
    """
    Ensure a UX chatter GGUF under ``models/ux/``:

    - If Gemma or fallback already on disk, return that path (Gemma first).
    - Otherwise download **Gemma**; on gated/auth HF errors, download **TheBloke TinyLlama** if
      ``try_fallback`` is true.
    """
    resolved = resolve_ux_chatter_path_on_disk()
    if resolved is not None:
        p, spec = resolved
        if progress:
            progress(f"UX chatter model already present: {p} ({spec.model_id})")
        return (p, spec)
    try:
        p = ensure_ux_chatter_model_downloaded(
            model=DEFAULT_UX_CHATTER,
            progress=progress,
            hf_download=hf_download,
        )
        return (p, DEFAULT_UX_CHATTER)
    except RuntimeError as e:
        if not try_fallback or not is_ux_gated_or_auth_hf_error(e):
            raise
        if progress:
            progress(
                "Gemma download blocked or unauthorized on Hugging Face; "
                "pulling public TinyLlama UX chatter model (TheBloke), copy."
            )
        p2 = ensure_ux_chatter_model_downloaded(
            model=FALLBACK_UX_CHATTER,
            progress=progress,
            hf_download=hf_download,
        )
        return (p2, FALLBACK_UX_CHATTER)


def ensure_ux_chatter_model_downloaded(
    *,
    model: UxChatterModel | None = None,
    progress: Callable[[str], None] | None = None,
    hf_download=hf_hub_download,
) -> Path:
    """
    Idempotent: ensure ``DEFAULT_UX_CHATTER`` (Gemma) GGUF is under ``models/ux/``.

    The upstream repo may be **gated** — log in to Hugging Face and accept the Gemma terms first.
    """
    m = model or DEFAULT_UX_CHATTER
    dest = m.local_path(ux_models_dir())
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        if progress:
            progress(f"UX chatter model already present: {dest}")
        return dest
    if progress:
        progress(
            f"Fetching UX chatter model from {m.repo_id}:{m.filename} to {dest.parent} (HF gate may apply)."
        )
    tqdm_class = VoxiumPolishHubTqdm if progress is not None else tqdm
    if progress is not None:
        _set_polish_hf_line_sink(progress)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*local_dir_use_symlinks.*",
                category=UserWarning,
            )
            out_path = Path(
                hf_download(
                    repo_id=m.repo_id,
                    filename=m.filename,
                    local_dir=dest.parent,
                    local_dir_use_symlinks=False,
                    tqdm_class=tqdm_class,
                )
            )
    except Exception as exc:
        raise RuntimeError(format_ux_chatter_hf_error(exc)) from exc
    finally:
        if progress is not None:
            _set_polish_hf_line_sink(None)
    if out_path.resolve() != dest.resolve():
        raise RuntimeError(
            f"Hugging Face download landed at {out_path}, expected {dest}."
        )
    if progress:
        progress(f"UX chatter model ready: {dest}")
    return dest
