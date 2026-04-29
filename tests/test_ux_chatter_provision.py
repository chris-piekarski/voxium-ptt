"""``models/ux/`` Gemma download helper."""

from __future__ import annotations

from pathlib import Path

from voxium.ux_chatter_provision import (
    format_ux_chatter_hf_error,
    ensure_ux_chatter_gguf_available,
    ensure_ux_chatter_model_downloaded,
    is_ux_gated_or_auth_hf_error,
)
from voxium.ux_chatter_model_registry import (
    DEFAULT_UX_CHATTER,
    FALLBACK_UX_CHATTER,
    resolve_ux_chatter_path_on_disk,
)


def test_format_ux_chatter_hf_error_401_gated() -> None:
    long_hf = (
        "401 Client Error. Request ID: Root=1-69f1a1b3;77fae25f \n"
        "Cannot access gated repo for url https://huggingface.co/google/…/gemma.gguf. "
        "Access to model google/gemma-3-1b-it-qat-q4_0-gguf is restricted."
    )
    out = format_ux_chatter_hf_error(ValueError(long_hf))
    assert "401" in out or "gated" in out.lower() or "huggingface-cli" in out
    assert "Request ID" not in out
    assert len(out) < 500


def test_format_ux_chatter_hf_error_truncates_generic() -> None:
    err = "X " * 300
    out = format_ux_chatter_hf_error(RuntimeError(err))
    assert len(out) <= 330
    assert out.endswith("…")


def test_ensure_ux_chatter_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    p = tmp_path / "models" / "ux" / DEFAULT_UX_CHATTER.filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"gguf")
    out = ensure_ux_chatter_model_downloaded()
    assert out.resolve() == p.resolve()


def test_is_ux_gated_or_auth_hf_error_detects_401() -> None:
    assert is_ux_gated_or_auth_hf_error(
        ValueError("401 Client Error. Cannot access gated repo for url")
    )
    assert not is_ux_gated_or_auth_hf_error(ConnectionError("network down"))


def test_resolve_ux_chatter_path_on_disk_prefers_gemma(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    g = tmp_path / "models" / "ux" / DEFAULT_UX_CHATTER.filename
    t = tmp_path / "models" / "ux" / FALLBACK_UX_CHATTER.filename
    g.parent.mkdir(parents=True, exist_ok=True)
    g.write_bytes(b"x")
    t.write_bytes(b"y")
    p, spec = resolve_ux_chatter_path_on_disk()
    assert spec is DEFAULT_UX_CHATTER
    assert p == g


def test_ensure_ux_chatter_gguf_falls_back_when_gemma_gated(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))

    def fake_hf(*, repo_id, filename, local_dir, **kwargs):
        if "google" in repo_id:
            raise OSError("401 Client Error. Cannot access gated repo")
        dest = Path(local_dir) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"gguf")
        return str(dest)

    path, spec = ensure_ux_chatter_gguf_available(
        progress=None,
        hf_download=fake_hf,
    )
    assert spec is FALLBACK_UX_CHATTER
    assert path.name == FALLBACK_UX_CHATTER.filename
    assert path.is_file()
