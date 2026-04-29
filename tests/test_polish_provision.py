"""Tests for repo-local llama.cpp + trusted GGUF polish provisioning."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import requests

import voxium.polish_provision as pp
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    trusted_polish_model,
)


class _FakeResponse:
    def __init__(self, payload: dict, *, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def iter_content(self, chunk_size: int = 1024 * 1024):
        _ = chunk_size
        yield b"archive-bytes"


def test_detect_windows_llama_cpp_variant_respects_env_override() -> None:
    assert (
        pp.detect_windows_llama_cpp_variant({"VOXIUM_POLISH_RUNTIME": "cuda13"})
        == "cuda13"
    )


def test_detect_windows_llama_cpp_variant_uses_nvidia_smi(monkeypatch) -> None:
    monkeypatch.setattr(pp.shutil, "which", lambda name, path=None: "/bin/nvidia-smi")
    assert pp.detect_windows_llama_cpp_variant({"PATH": "/bin"}) == "cuda12"


def test_resolve_windows_llama_cpp_release_skips_cudart_only_uses_llama_bin_zip() -> (
    None
):
    # Like GitHub order today: cudart-only matches the cuda regex but has no llama-server.exe
    # inside; the prebuilt llama-…-bin-… asset must be chosen for cuda12.
    payload = {
        "tag_name": "b1234",
        "assets": [
            {
                "name": "cudart-llama-bin-win-cuda-12.8-x64.zip",
                "browser_download_url": "https://example.test/cudart-only.zip",
            },
            {
                "name": "llama-b1234-bin-win-cuda-12.8-x64.zip",
                "browser_download_url": "https://example.test/llama-cuda12.zip",
            },
        ],
    }

    release = pp.resolve_windows_llama_cpp_release(
        base_env={"VOXIUM_POLISH_RUNTIME": "cuda12"},
        requests_get=lambda *args, **kwargs: _FakeResponse(payload),
    )

    assert release.tag == "b1234"
    assert release.variant == "cuda12"
    assert release.asset_name == "llama-b1234-bin-win-cuda-12.8-x64.zip"
    assert release.download_url == "https://example.test/llama-cuda12.zip"


def test_resolve_windows_llama_cpp_release_falls_back_to_cpu() -> None:
    payload = {
        "tag_name": "b1234",
        "assets": [
            {
                "name": "llama-b1234-bin-win-cpu-x64.zip",
                "browser_download_url": "https://example.test/cpu.zip",
            }
        ],
    }

    release = pp.resolve_windows_llama_cpp_release(
        base_env={"VOXIUM_POLISH_RUNTIME": "cuda12"},
        requests_get=lambda *args, **kwargs: _FakeResponse(payload),
    )

    assert release.variant == "cpu"
    assert release.download_url == "https://example.test/cpu.zip"


def test_promote_llama_server_exe_copies_from_nested_folder(tmp_path) -> None:
    runtime_dir = tmp_path / "tools" / "llama.cpp"
    runtime_dir.mkdir(parents=True)
    nested = runtime_dir / "nested" / "bin"
    nested.mkdir(parents=True)
    nested_exe = nested / "llama-server.exe"
    nested_exe.write_bytes(b"ok")
    target = runtime_dir / "llama-server.exe"
    assert not target.is_file()
    pp._promote_llama_server_exe_to_repo_runtime_dir(runtime_dir, target)
    assert target.read_bytes() == b"ok"


def test_extract_zip_flat_strips_single_top_level_directory(tmp_path) -> None:
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("llama-bin/llama-server.exe", b"exe")
        zf.writestr("llama-bin/README.txt", b"readme")

    out_dir = tmp_path / "out"
    pp._extract_zip_flat(archive, out_dir)

    assert (out_dir / "llama-server.exe").read_bytes() == b"exe"
    assert (out_dir / "README.txt").read_bytes() == b"readme"


def test_ensure_windows_llama_cpp_runtime_skips_network_when_runtime_exists(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    runtime = tmp_path / "tools" / "llama.cpp" / "llama-server.exe"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"exe")

    def _boom(*args, **kwargs):
        raise AssertionError("network should not be used when runtime already exists")

    runtime_exe, release = pp.ensure_windows_llama_cpp_runtime(
        base_env={"VOXIUM_POLISH_RUNTIME": "cpu"},
        requests_get=_boom,
    )

    assert runtime_exe == runtime
    assert release.tag == "installed"
    assert release.variant == "cpu"


def test_ensure_default_polish_model_returns_existing_trusted_file(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    local_path = tmp_path / "models" / "polish" / trusted.filename
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b"gguf")

    def _boom(**kwargs):
        raise AssertionError("hf_hub_download should not run when the model exists")

    model = pp.ensure_default_polish_model(hf_download=_boom)

    assert model.name == DEFAULT_TRUSTED_POLISH_MODEL_ID
    assert model.path == local_path.resolve()


def test_ensure_polish_model_downloaded_fetches_requested_trusted_id(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    trusted = trusted_polish_model("qwen2.5-3b-q4km")

    def _fake_hf_download(**kwargs) -> str:
        assert kwargs["repo_id"] == trusted.repo_id
        assert kwargs["filename"] == trusted.filename
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"gguf")
        return str(target)

    model = pp.ensure_polish_model_downloaded(
        model_name="qwen2.5-3b-q4km",
        hf_download=_fake_hf_download,
    )

    assert model.name == "qwen2.5-3b-q4km"
    assert model.path.name == trusted.filename


def test_ensure_default_polish_assets_detects_repo_local_runtime_on_non_windows(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))
    runtime = tmp_path / "tools" / "llama.cpp" / "llama-server"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"exe")

    def _fake_hf_download(**kwargs) -> str:
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"gguf")
        return str(target)

    assets = pp.ensure_default_polish_assets(
        system_name="Linux",
        hf_download=_fake_hf_download,
    )

    assert assets.runtime_exe == runtime.resolve()
    assert assets.runtime_dir == runtime.resolve().parent
    assert assets.model_path.name.endswith(".gguf")


def test_ensure_default_polish_model_wraps_download_errors(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOXIUM_REPO_ROOT", str(tmp_path))

    def _fail(**kwargs):
        _ = kwargs
        raise ValueError("bad download")

    with pytest.raises(
        RuntimeError,
        match="Could not download the requested GGUF polish model from Hugging Face",
    ):
        pp.ensure_default_polish_model(hf_download=_fail)


def test_resolve_windows_release_network_error_raises() -> None:
    def _fail(*_a, **_k):
        raise requests.RequestException("offline")

    with pytest.raises(RuntimeError, match="Could not read latest llama.cpp"):
        pp.resolve_windows_llama_cpp_release(
            base_env={"VOXIUM_POLISH_RUNTIME": "cpu"},
            requests_get=_fail,
        )


def test_resolve_windows_release_missing_tag_raises() -> None:
    bad = {"tag_name": "", "assets": []}
    with pytest.raises(RuntimeError, match="missing tag"):
        pp.resolve_windows_llama_cpp_release(
            base_env={"VOXIUM_POLISH_RUNTIME": "cpu"},
            requests_get=lambda *a, **k: _FakeResponse(bad),
        )


def test_detect_windows_llama_cpp_variant_cpu_without_nvidia_smi(monkeypatch) -> None:
    monkeypatch.setattr(pp.shutil, "which", lambda name, path=None: None)
    assert pp.detect_windows_llama_cpp_variant({"PATH": ""}) == "cpu"


def test_voxium_polish_hub_tqdm_pushes_line_to_sink() -> None:
    lines: list[str] = []
    pp._set_polish_hf_line_sink(lines.append)
    try:
        hub = pp.VoxiumPolishHubTqdm(
            total=2 * 1024**3,
            desc="a" * 60,
            mininterval=0.0,
            file=__import__("io").StringIO(),
        )
        hub.update(1024**3)
    finally:
        pp._set_polish_hf_line_sink(None)
    assert lines and "50" in lines[0]
