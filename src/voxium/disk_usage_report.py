"""Human-readable disk use for repository data dirs (``make disk-usage``, ``/disk``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from voxium.paths import repo_root

# Under :func:`repo_root`: model snapshots and client/server logs (transcript history is RAM-only).
VOXIUM_DATA_DIR_NAMES: tuple[str, ...] = ("models", "logs")


def _human_size(n: int) -> str:
    n = max(0, n)
    for u, d in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if n >= d:
            return f"{(n / d):.1f}{u[0]}"
    return f"{n}" if n else "0"


def _iter_files(p: Path) -> Iterator[Path]:
    if p.is_file():
        yield p
        return
    if not p.is_dir():
        return
    try:
        for sub in p.rglob("*"):
            if sub.is_file():
                yield sub
    except OSError:
        return


def _dir_size(p: Path) -> int:
    total = 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    for f in _iter_files(p):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def _line_for_path(p: Path) -> str:
    if not p.exists():
        return f"  (absent) {p}"
    h = _human_size(_dir_size(p))
    return f"{h}\t{p}"


def format_repo_disk_usage_text(root: Path | None = None) -> str:
    """
    Same layout as ``make disk-usage``: per-directory summary under the repo.

    *root* defaults to :func:`voxium.paths.repo_root` (``VOXIUM_REPO_ROOT`` or
    directory containing ``pyproject.toml``).
    """
    base = (root or repo_root()).resolve()
    lines: list[str] = ["=== Voxium local data (repository) ==="]
    for name in VOXIUM_DATA_DIR_NAMES:
        p = base / name
        lines.append(f"--- {name}/ ---")
        lines.append(_line_for_path(p))
    return "\n".join(lines) + "\n"
