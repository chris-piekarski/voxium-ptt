"""Managed `llama-server` startup for the local polish path."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from voxium.llama_cpp_client import llama_cpp_loaded_model, llama_cpp_reachable
from voxium.paths import llama_cpp_dir

IS_WINDOWS = os.name == "nt"


@dataclass
class ManagedLlamaCpp:
    process: subprocess.Popen | None
    log_handle: TextIO | None = None
    started_by_voxium: bool = False


def default_llama_server_path(base_env: Mapping[str, str] | None = None) -> Path:
    _ = base_env
    exe = "llama-server.exe" if IS_WINDOWS else "llama-server"
    return llama_cpp_dir() / exe


def llama_server_cli_path(
    configured_path: str | None = None,
    *,
    base_env: Mapping[str, str] | None = None,
) -> str | None:
    configured = (configured_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
    repo_local = default_llama_server_path(base_env)
    if repo_local.is_file():
        return str(repo_local.resolve())
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


def ensure_llama_cpp_daemon(
    *,
    base_url: str,
    cmd_path: str | None,
    model_path: Path,
    model_alias: str,
    log_path: Path,
    startup_timeout: float = 20.0,
    parallel: int = 2,
    ctx_size: int = 0,
    gpu_layers: str | int | None = "auto",
    sleep_idle_seconds: int | None = None,
    popen=subprocess.Popen,
    sleep=time.sleep,
) -> tuple[ManagedLlamaCpp | None, list[tuple[str, str]]]:
    """
    Ensure `llama-server` is reachable. Start it only when the probe fails.

    Returns `(managed_process_or_none, telemetry_entries)`. Existing daemons are
    never owned by Voxium, so shutdown should only stop a non-None returned process.
    """
    entries: list[tuple[str, str]] = []
    ok, reason = llama_cpp_reachable(base_url, timeout=1.0)
    if ok:
        loaded = llama_cpp_loaded_model(base_url, timeout=1.0)
        entries.append(("llama.cpp already on station for polish.", "info"))
        if loaded and loaded != model_alias:
            entries.append(
                (
                    f"llama.cpp is already serving model {loaded!r}, not {model_alias!r}. "
                    "Voxium will reuse the running server; switch the local runtime if polish replies do not match the selected model.",
                    "warning",
                )
            )
        return None, entries

    cli = llama_server_cli_path(cmd_path)
    if not cli:
        entries.append(
            (
                "Re-encode is enabled, but Voxium could not find `llama-server`. "
                "Run `voxium models --polish --pull-polish` (or `scripts\\windows\\Setup-Voxium.cmd` on Windows) "
                "to provision the repo-local runtime, or add `llama-server` to PATH; Voxium will paste raw STT until then.",
                "warning",
            )
        )
        return None, entries
    if not model_path.is_file():
        entries.append(
            (
                f"Re-encoder model file is missing: {model_path}. "
                "Run `voxium models --polish --pull-polish` to provision the default GGUF model, then retry.",
                "warning",
            )
        )
        return None, entries

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11435

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(  # pylint: disable=consider-using-with
        log_path, "a", encoding="utf-8"
    )
    cmd = [
        cli,
        "-m",
        str(model_path),
        "--host",
        host,
        "--port",
        str(port),
        "--alias",
        model_alias,
        "--parallel",
        str(max(1, int(parallel))),
        "--jinja",
        "--warmup",
    ]
    if ctx_size > 0:
        cmd.extend(["--ctx-size", str(int(ctx_size))])
    if gpu_layers is not None and str(gpu_layers).strip():
        cmd.extend(["--n-gpu-layers", str(gpu_layers)])
    if sleep_idle_seconds is not None:
        cmd.extend(["--sleep-idle-seconds", str(int(sleep_idle_seconds))])

    try:
        argv_line = shlex.join(str(x) for x in cmd)
    except (TypeError, ValueError):
        argv_line = " ".join(str(x) for x in cmd)
    log_handle.write(
        f"Voxium: llama-server argv (check --sleep-idle-seconds): {argv_line}\n"
    )
    log_handle.flush()

    kwargs: dict[str, object] = {
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        proc = popen(cmd, **kwargs)
    except OSError as exc:
        try:
            log_handle.close()
        except Exception:
            pass
        entries.append((f"Could not start llama.cpp for polish: {exc}", "warning"))
        return None, entries

    managed = ManagedLlamaCpp(proc, log_handle, started_by_voxium=True)
    entries.append(
        (f"Starting llama.cpp for polish: {base_url} (log: {log_path})", "info")
    )
    deadline = time.time() + max(1.0, startup_timeout)
    last_reason = reason
    while time.time() < deadline:
        if proc.poll() is not None:
            entries.append(
                (
                    f"llama.cpp exited during startup (code {proc.returncode}); see {log_path}. "
                    "Voxium will paste raw STT if polish cannot run.",
                    "warning",
                )
            )
            return managed, entries
        ok, last_reason = llama_cpp_reachable(base_url, timeout=1.0)
        if ok:
            loaded = llama_cpp_loaded_model(base_url, timeout=1.0)
            if loaded and loaded != model_alias:
                entries.append(
                    (
                        f"llama.cpp came up serving model {loaded!r}; expected {model_alias!r}.",
                        "warning",
                    )
                )
            else:
                entries.append(("llama.cpp ready for local polish, copy.", "info"))
            return managed, entries
        sleep(0.3)

    entries.append(
        (
            f"llama.cpp did not answer at {base_url}: {last_reason}. "
            f"Leaving managed process running; see {log_path}.",
            "warning",
        )
    )
    return managed, entries


def stop_managed_llama_cpp(
    managed: ManagedLlamaCpp | None, *, timeout: float = 5.0
) -> None:
    if not managed or not managed.started_by_voxium or not managed.process:
        return
    proc = managed.process
    if proc.poll() is None:
        try:
            if IS_WINDOWS:
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if IS_WINDOWS:
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    if managed.log_handle is not None:
        try:
            managed.log_handle.close()
        except Exception:
            pass
