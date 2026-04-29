"""Provision repo-local llama.cpp runtime and trusted GGUF polish models."""

from __future__ import annotations

import io
import os
import platform
import sys
import re
import shutil
import tempfile
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from huggingface_hub import hf_hub_download
from tqdm import tqdm

from voxium.paths import llama_cpp_dir, polish_models_dir
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
    LocalPolishModel,
    resolve_polish_model,
    trusted_polish_model,
    validate_polish_model_name,
)

_GH_LATEST_RELEASE = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# HF hub tqdm: mirror a single status line to this sink (Rich Live / TUI) instead of stderr.
_polish_hf_line_sink: Callable[[str], None] | None = None


def _set_polish_hf_line_sink(
    sink: Callable[[str], None] | None,
) -> None:
    global _polish_hf_line_sink
    _polish_hf_line_sink = sink


class VoxiumPolishHubTqdm(tqdm):
    """Hugging Face file download: keep tqdm off stderr; push one line to the operator sink."""

    def __init__(self, *args, **kwargs):
        kwargs = dict(kwargs)
        if _polish_hf_line_sink is not None:
            kwargs.setdefault("file", io.StringIO())
            kwargs.setdefault("mininterval", 0.2)
        super().__init__(*args, **kwargs)

    def update(self, n=1) -> bool:
        r = bool(super().update(n))
        sink = _polish_hf_line_sink
        if sink is not None and getattr(self, "total", None):
            try:
                t = float(self.total)
                if t > 0:
                    n_done = float(self.n)
                    pct = 100.0 * n_done / t
                    desc = (self.desc or "file").strip()
                    if len(desc) > 52:
                        desc = desc[:49] + "…"
                    sink(
                        f"{desc}  {pct:.0f}%  "
                        f"({n_done / (1024**3):.2f} / {t / (1024**3):.2f} GiB)"
                    )
            except Exception:
                pass
        return r


def wrap_hf_download_progress(emit: Callable[[str], None]) -> Callable[[str], None]:
    """
    For :class:`VoxiumPolishHubTqdm` sink output: **GiB / %** status lines update a **single** stderr
    line (CR + clear). Other lines (e.g. “Fetching…”, “ready”) go through *emit*, with a newline
    after a prior bar so the next log is not joined to the progress line.

    Use with ``cli_log``-based *emit* so the telemetry buffer does not grow one line per tick.
    """
    in_bar = False

    def push(m: str) -> None:
        nonlocal in_bar
        s = (m or "").strip()
        if not s:
            return
        is_bar = bool(re.search(r"\d{1,3}%\s+\(\s*[\d.]+", s) and "GiB" in s)
        if is_bar:
            print(f"\r\033[2K{s}", end="", file=sys.stderr, flush=True)
            in_bar = True
            return
        if in_bar:
            print(file=sys.stderr)
            in_bar = False
        emit(s)

    return push


_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Voxium polish provisioner",
}


@dataclass(frozen=True)
class LlamaCppRuntimeRelease:
    tag: str
    asset_name: str
    download_url: str
    variant: str


@dataclass(frozen=True)
class PolishDownloadSpec:
    model_id: str
    repo_id: str
    filename: str
    local_path: Path


@dataclass(frozen=True)
class ProvisionedPolishAssets:
    runtime_dir: Path | None
    runtime_exe: Path | None
    runtime_variant: str | None
    runtime_tag: str | None
    model_path: Path
    model_repo_id: str
    model_filename: str


def default_polish_model_spec() -> PolishDownloadSpec:
    model = trusted_polish_model(DEFAULT_TRUSTED_POLISH_MODEL_ID)
    return PolishDownloadSpec(
        model_id=model.model_id,
        repo_id=model.repo_id,
        filename=model.filename,
        local_path=model.local_path(polish_models_dir()),
    )


def polish_model_download_spec(model_name: str | None) -> PolishDownloadSpec:
    requested = validate_polish_model_name(model_name)
    if requested == POLISH_DEFAULT_MODEL:
        return default_polish_model_spec()
    model = trusted_polish_model(requested)
    return PolishDownloadSpec(
        model_id=model.model_id,
        repo_id=model.repo_id,
        filename=model.filename,
        local_path=model.local_path(polish_models_dir()),
    )


def detect_windows_llama_cpp_variant(base_env: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    if base_env:
        env.update(base_env)
    forced = (env.get("VOXIUM_POLISH_RUNTIME") or "").strip().lower()
    if forced in {"cpu", "cuda12", "cuda13"}:
        return forced
    path = env.get("PATH")
    if shutil.which("nvidia-smi", path=path):
        return "cuda12"
    return "cpu"


def resolve_windows_llama_cpp_release(
    *,
    base_env: dict[str, str] | None = None,
    requests_get=requests.get,
) -> LlamaCppRuntimeRelease:
    variant = detect_windows_llama_cpp_variant(base_env)
    try:
        resp = requests_get(
            _GH_LATEST_RELEASE,
            timeout=20.0,
            headers=_GITHUB_HEADERS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not read latest llama.cpp release metadata: {exc}"
        ) from exc
    tag = str(payload.get("tag_name") or "").strip()
    assets = payload.get("assets")
    if not tag or not isinstance(assets, list):
        raise RuntimeError("Latest llama.cpp release metadata is missing tag/assets.")

    # ggml-org releases ship both "cudart-llama-bin-…" (CUDA runtime only) and
    # "llama-…-bin-…" (executables). The cudart zips also match the cuda filename
    # regex below but do not contain llama-server.exe — never select them.
    patterns = {
        "cpu": re.compile(r"win-cpu-x64\.zip$", re.IGNORECASE),
        "cuda12": re.compile(
            r"win-cuda-12(?:\.\d+)?-x64\.zip$",
            re.IGNORECASE,
        ),
        "cuda13": re.compile(
            r"win-cuda-13(?:\.\d+)?-x64\.zip$",
            re.IGNORECASE,
        ),
    }
    ordered_variants = [variant]
    if variant == "cuda13":
        ordered_variants.extend(["cuda12", "cpu"])
    elif variant == "cuda12":
        ordered_variants.append("cpu")

    for wanted in ordered_variants:
        pattern = patterns[wanted]
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            if name.lower().startswith("cudart-"):
                continue
            if not pattern.search(name):
                continue
            url = str(asset.get("browser_download_url") or "").strip()
            if not url:
                continue
            return LlamaCppRuntimeRelease(
                tag=tag,
                asset_name=name,
                download_url=url,
                variant=wanted,
            )
    raise RuntimeError(
        f"Could not find a Windows llama.cpp runtime asset for variants {ordered_variants} in release {tag}."
    )


def _download_stream(
    url: str,
    destination: Path,
    *,
    progress: Callable[[str], None] | None = None,
    requests_get=requests.get,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests_get(url, stream=True, timeout=(10.0, 120.0)) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        with destination.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress and total > 0:
                    progress(
                        f"Downloading {destination.name}: {done / (1024**2):.0f} / {total / (1024**2):.0f} MiB"
                    )


def _promote_llama_server_exe_to_repo_runtime_dir(
    runtime_dir: Path, runtime_exe: Path
) -> None:
    """If extraction left llama-server.exe in a subfolder, copy it to the repo path we probe."""
    if runtime_exe.is_file():
        return
    matches = sorted(
        runtime_dir.rglob("llama-server.exe"),
        key=lambda p: (len(p.relative_to(runtime_dir).parts), str(p)),
    )
    if not matches:
        return
    best = matches[0]
    if best != runtime_exe:
        shutil.copy2(best, runtime_exe)


def _extract_zip_flat(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if not members:
            raise RuntimeError(f"Runtime archive is empty: {zip_path}")
        prefixes = {
            Path(info.filename).parts[0]
            for info in members
            if Path(info.filename).parts
        }
        strip_root = len(prefixes) == 1
        for info in members:
            parts = Path(info.filename).parts
            rel_parts = parts[1:] if strip_root and len(parts) > 1 else parts
            if not rel_parts:
                continue
            out_path = target_dir.joinpath(*rel_parts)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def ensure_windows_llama_cpp_runtime(
    *,
    base_env: dict[str, str] | None = None,
    progress: Callable[[str], None] | None = None,
    requests_get=requests.get,
) -> tuple[Path, LlamaCppRuntimeRelease]:
    runtime_dir = llama_cpp_dir()
    runtime_exe = runtime_dir / "llama-server.exe"
    if runtime_exe.is_file():
        release = LlamaCppRuntimeRelease(
            tag="installed",
            asset_name=runtime_exe.name,
            download_url="",
            variant=detect_windows_llama_cpp_variant(base_env),
        )
        if progress:
            progress(
                f"llama.cpp runtime already present: {runtime_exe} ({release.variant})"
            )
        return runtime_exe, release
    release = resolve_windows_llama_cpp_release(
        base_env=base_env,
        requests_get=requests_get,
    )

    if progress:
        progress(
            f"Fetching llama.cpp runtime {release.tag} ({release.variant}) to {runtime_dir}."
        )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voxium-llama-cpp-") as tmpdir:
        archive_path = Path(tmpdir) / release.asset_name
        _download_stream(
            release.download_url,
            archive_path,
            progress=progress,
            requests_get=requests_get,
        )
        _extract_zip_flat(archive_path, runtime_dir)
        _promote_llama_server_exe_to_repo_runtime_dir(runtime_dir, runtime_exe)
    if not runtime_exe.is_file():
        raise RuntimeError(
            f"Downloaded llama.cpp archive did not produce {runtime_exe}."
        )
    if progress:
        progress(f"llama.cpp runtime ready: {runtime_exe}")
    return runtime_exe, release


def ensure_polish_model_downloaded(
    *,
    model_name: str | None = None,
    progress: Callable[[str], None] | None = None,
    hf_download=hf_hub_download,
) -> LocalPolishModel:
    requested = validate_polish_model_name(model_name)
    if requested.startswith("local:"):
        return resolve_polish_model(requested)

    spec = polish_model_download_spec(requested)
    spec.local_path.parent.mkdir(parents=True, exist_ok=True)
    if spec.local_path.is_file():
        if progress:
            progress(f"Polish model already present: {spec.local_path}")
        return resolve_polish_model(spec.model_id)
    if progress:
        progress(
            f"Fetching polish model {spec.model_id} from {spec.repo_id}:{spec.filename} to {spec.local_path.parent}."
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
                    repo_id=spec.repo_id,
                    filename=spec.filename,
                    local_dir=spec.local_path.parent,
                    local_dir_use_symlinks=False,
                    tqdm_class=tqdm_class,
                )
            )
    except Exception as exc:  # pragma: no cover - backend-specific exceptions
        raise RuntimeError(
            "Could not download the requested GGUF polish model from Hugging Face: "
            f"{exc}"
        ) from exc
    finally:
        if progress is not None:
            _set_polish_hf_line_sink(None)
    if out_path.resolve() != spec.local_path.resolve():
        raise RuntimeError(
            f"Hugging Face download landed at {out_path}, expected {spec.local_path}."
        )
    if progress:
        progress(f"Polish model ready: {spec.local_path}")
    return resolve_polish_model(spec.model_id)


def ensure_default_polish_model(
    *,
    progress: Callable[[str], None] | None = None,
    hf_download=hf_hub_download,
) -> LocalPolishModel:
    return ensure_polish_model_downloaded(
        model_name=POLISH_DEFAULT_MODEL,
        progress=progress,
        hf_download=hf_download,
    )


def repo_local_llama_server_binary(system_name: str | None = None) -> Path:
    system_now = system_name or platform.system()
    exe_name = "llama-server.exe" if system_now == "Windows" else "llama-server"
    return llama_cpp_dir() / exe_name


def ensure_default_polish_assets(
    *,
    model_name: str | None = POLISH_DEFAULT_MODEL,
    progress: Callable[[str], None] | None = None,
    system_name: str | None = None,
    base_env: dict[str, str] | None = None,
    requests_get=requests.get,
    hf_download=hf_hub_download,
) -> ProvisionedPolishAssets:
    system_now = system_name or platform.system()
    runtime_dir: Path | None = None
    runtime_exe: Path | None = None
    runtime_variant: str | None = None
    runtime_tag: str | None = None
    repo_local_runtime = repo_local_llama_server_binary(system_now)
    if repo_local_runtime.is_file():
        runtime_exe = repo_local_runtime.resolve()
        runtime_dir = runtime_exe.parent
        if progress:
            progress(f"llama.cpp runtime already present: {runtime_exe}")
    elif system_now == "Windows":
        runtime_exe, release = ensure_windows_llama_cpp_runtime(
            base_env=base_env,
            progress=progress,
            requests_get=requests_get,
        )
        runtime_dir = runtime_exe.parent
        runtime_variant = release.variant
        runtime_tag = release.tag
    else:
        if progress:
            progress(
                "Automatic llama.cpp runtime provisioning is only wired for Windows setup today. "
                "Place llama-server under tools/llama.cpp manually on this platform."
            )
    model = ensure_polish_model_downloaded(
        model_name=model_name,
        progress=progress,
        hf_download=hf_download,
    )
    return ProvisionedPolishAssets(
        runtime_dir=runtime_dir,
        runtime_exe=runtime_exe,
        runtime_variant=runtime_variant,
        runtime_tag=runtime_tag,
        model_path=model.path,
        model_repo_id=str(model.repo_id or ""),
        model_filename=str(model.filename or model.path.name),
    )
