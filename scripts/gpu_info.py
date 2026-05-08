#!/usr/bin/env python3
"""
Print GPU / accelerator details relevant to Voxium (NVIDIA, CTranslate2, optional pynvml, optional AMD).

Run via: make gpu-info  (uses the project venv) or: .venv/bin/python scripts/gpu_info.py
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _print_nvidia() -> None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        print(
            "NVIDIA: nvidia-smi not on PATH (driver/toolkit not installed, or not in this environment)."
        )
        return
    print(f"NVIDIA: using {exe}")
    print()
    code, out, err = _run([exe, "-L"])
    if out.strip():
        print("--- nvidia-smi -L (device list) ---")
        print(out.rstrip())
        print()
    code, out, err = _run([exe])
    print("--- nvidia-smi (full) ---")
    if code != 0 and not out.strip():
        print(f"(exit {code})", file=sys.stderr)
        if err.strip():
            print(err, file=sys.stderr)
    else:
        print(out.rstrip() if out else "(no stdout)")
    if err.strip() and code != 0:
        print(err, file=sys.stderr)
    print()


def _print_rocm() -> None:
    exe = shutil.which("rocm-smi")
    if not exe:
        return
    print(f"--- rocm-smi (AMD, {exe}) ---")
    code, out, err = _run([exe, "-i"])
    if not out.strip():
        code, out, err = _run([exe])
    if out.strip():
        print(out.rstrip())
    else:
        print(f"(no output, exit {code})")
    if err.strip() and code != 0:
        print(err, file=sys.stderr)
    print()


def _print_ctranslate2() -> None:
    print("--- CTranslate2 (Voxium / faster-whisper backend) ---")
    try:
        import ctranslate2 as ct
    except ImportError as e:
        print(f"  (import failed) {e}")
        print("  Install the project in a venv:  make install")
        return
    try:
        ver = getattr(ct, "__version__", "?")
        n = ct.get_cuda_device_count()
        print(f"  ctranslate2 version: {ver}")
        print(f"  get_cuda_device_count(): {n}")
    except Exception as e:
        print(f"  (query failed) {type(e).__name__}: {e}")


def _print_pynvml() -> None:
    print()
    print("--- pynvml (optional; python bindings to NVML) ---")
    try:
        import pynvml
    except ImportError:
        print("  (not installed)")
        return
    try:
        pynvml.nvmlInit()
        n = pynvml.nvmlDeviceGetCount()
        print(f"  device count: {n}")
        for i in range(n):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            raw = pynvml.nvmlDeviceGetName(h)
            name = (
                raw.decode("utf-8", errors="replace")
                if isinstance(raw, bytes)
                else str(raw)
            )
            print(f"  [{i}] {name}")
    except Exception as e:
        print(f"  (unavailable) {type(e).__name__}: {e}")
    else:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def main() -> int:
    print(
        "Voxium — GPU / accelerator readout (moon-and-back stack: humans + silicon + coding agents)"
    )
    print()
    try:
        print(f"Platform:  {platform.platform()}")
    except OSError:
        print(f"Platform:  {sys.platform!r}")
    print(f"Python:    {sys.version.split()[0]}\n  {sys.executable}")
    print()
    _print_nvidia()
    _print_rocm()
    _print_ctranslate2()
    _print_pynvml()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
