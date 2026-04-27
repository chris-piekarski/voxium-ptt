#!/usr/bin/env python3
"""
GNU Make target implementations (stdlib only). The Makefile is written for
Ubuntu / Linux (including WSL). On Windows, use the `voxium` CLI from a venv;
see the project README.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _vpaths(venvd: Path) -> tuple[Path, Path, Path]:
    b = venvd / "bin"
    return (
        b / "pip",
        b / "python",
        b / "voxium",
    )


# --- subcommands

# 24-bit RGB section titles (rotate). Distinct, readable on dark backgrounds.
_HELP_SECTION_RGB: tuple[tuple[int, int, int], ...] = (
    (96, 165, 250),  # blue
    (52, 211, 153),  # green
    (251, 191, 36),  # amber
    (196, 181, 253),  # lavender
    (244, 114, 182),  # pink
    (45, 212, 191),  # teal
    (250, 204, 21),  # gold
    (168, 85, 247),  # purple
)

def _help_section_sgr(n: int) -> str:
    r, g, b = _HELP_SECTION_RGB[n % len(_HELP_SECTION_RGB)]
    return f"\033[1m\033[38;2;{r};{g};{b}m"


def cmd_help(mkfile: Path) -> int:
    # Parse ##@ section headers in order, and attach `target: … ## desc` lines to the
    # *current* section (GNU make convention: help text on the first recipe line only).
    print(
        "\033[1mVoxium\033[0m — \033[0;90m"
        "ground support · make <target> (PTT & VOX in app; see docs/brand.md)\033[0m"
    )
    text = mkfile.read_text(encoding="utf-8", errors="replace")
    current_title: str | None = None
    pending: list[tuple[str, str]] = []
    blocks: list[tuple[str, list[tuple[str, str]]]] = []
    orphan: list[tuple[str, str]] = []

    target_line = re.compile(
        r"^([a-zA-Z0-9][a-zA-Z0-9_.-]*):.*##\s*(.*)$",
    )
    for line in text.splitlines():
        msec = re.match(r"^##@\s*(.*)$", line)
        if msec:
            if current_title is not None:
                blocks.append((current_title, pending))
            current_title = msec.group(1).strip() or "Uncategorized"
            pending = []
            continue
        mt = target_line.match(line)
        if mt and "##@" not in line:
            name, desc = mt.group(1), mt.group(2).strip()
            if not desc:
                continue
            if current_title is None:
                orphan.append((name, desc))
            else:
                pending.append((name, desc))

    if current_title is not None:
        blocks.append((current_title, pending))
    if orphan:
        blocks.insert(0, ("Other", orphan))

    shown = 0
    for title, items in blocks:
        if not items:
            continue
        print(f"\n{_help_section_sgr(shown)}{title}\033[0m")
        shown += 1
        for name, desc in items:
            print("  \033[0;90m%-18s\033[0m %s" % (name, desc))
    print()
    return 0


def _check_python(python: str) -> str | None:
    if shutil.which(python):
        return None
    return f"Need '{python}' on PATH (override: make install PYTHON=...)."


def cmd_install(root: Path, venvd: Path, python: str) -> int:
    err = _check_python(python)
    if err:
        print(err, file=sys.stderr)
        return 1
    pip, _, voxium_bin = _vpaths(venvd)
    if not pip.exists():
        if venvd.is_dir():
            print(
                f"Virtualenv exists but {pip} is missing. "
                "Run: make uninstall  then make install  to recreate the venv.",
                file=sys.stderr,
            )
            return 1
        subprocess.run(
            [python, "-m", "venv", str(venvd)],
            check=True,
        )
    subprocess.run(
        [str(pip), "install", "-U", "pip"],
        check=True,
    )
    subprocess.run(
        [str(pip), "install", "-e", str(root)],
        check=True,
    )
    print(f"Install complete. Run: make start  (or: {voxium_bin} run)")
    return 0


def _rmtree(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.is_file():
        try:
            p.unlink()
        except OSError:
            pass


def _path_under_venv(p: Path, venvd: Path) -> bool:
    try:
        pr = p.resolve()
        vr = venvd.resolve()
    except OSError:
        return False
    try:
        return pr == vr or pr.is_relative_to(vr)
    except (ValueError, OSError):
        return False


# During cache sweeps, avoid traversing heavyweight control/env trees.
_CLEAN_PRUNE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
)


def _is_venv_like_dirname(name: str) -> bool:
    return name.startswith(".venv")


# Project-local temp / tool output (see `make disk-usage` for Voxium data dirs).
_CLEAN_ROOT_DIRS: tuple[str, ...] = (
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    "build",
    "dist",
    ".mypy_cache",
    ".tox",
    ".eggs",
    ".cache",  # e.g. tool caches under the repo, not XDG
    ".hypothesis",
)
_CLEAN_ROOT_FILES: tuple[str, ...] = (".coverage", "coverage.xml")


def cmd_clean_artifacts(root: Path, venvd: Path) -> int:
    """
    Remove well-known tool caches at repo root, coverage files, every
    __pycache__ outside the venv, and top-level ``*.egg-info`` (when not under
    the venv). The venv tree is not traversed and is not removed here.
    """
    for name in _CLEAN_ROOT_DIRS:
        _rmtree(root / name)
    for name in _CLEAN_ROOT_FILES:
        p = root / name
        if p.is_file():
            _rmtree(p)
    root = root.absolute()
    venvd = venvd.absolute()

    for dirpath, dirnames, _ in os.walk(root, topdown=True, followlinks=False):
        cur = Path(dirpath)

        # Prune large trees we never need to scan for __pycache__.
        kept_dirnames: list[str] = []
        for name in dirnames:
            if name in _CLEAN_PRUNE_DIRS or _is_venv_like_dirname(name):
                continue
            candidate = cur / name
            try:
                if candidate == venvd or candidate.is_relative_to(venvd):
                    continue
            except ValueError:
                pass
            kept_dirnames.append(name)
        dirnames[:] = kept_dirnames

        if cur.name == "__pycache__" and cur.is_dir():
            # Clear dirnames before rmtree: avoid os.walk following into a
            # directory that is about to be removed.
            dirnames.clear()
            _rmtree(cur)
    for p in root.glob("*.egg-info"):
        if p.is_dir() and not _path_under_venv(p, venvd):
            _rmtree(p)
    return 0


def cmd_uninstall(root: Path, venvd: Path, dev_stamp: Path) -> int:
    to_remove: list[Path] = [venvd, dev_stamp, *root.glob("*.egg-info")]
    for p in to_remove:
        _rmtree(p)
    return 0


def cmd_clean(root: Path, venvd: Path, dev_stamp: Path) -> int:
    """Project caches/temp files only; leaves the venv and dev stamp in place."""
    cmd_clean_artifacts(root, venvd)
    return 0


def cmd_disk_usage(root: Path, venvd: Path, dev_stamp: Path) -> int:
    del venvd, dev_stamp
    root = root.resolve()
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from voxium.disk_usage_report import format_repo_disk_usage_text

    print(format_repo_disk_usage_text(root), end="")
    return 0


def cmd_dev_stamp(
    root: Path,
    venvd: Path,
    python: str,
    dev_stamp: Path,
) -> int:
    err = _check_python(python)
    if err:
        print(err, file=sys.stderr)
        return 1
    pip, _, _ = _vpaths(venvd)
    if not pip.exists():
        if venvd.is_dir():
            print(
                f"Virtualenv exists but {pip} is missing. "
                "Run: make uninstall  then make install  to recreate the venv.",
                file=sys.stderr,
            )
            return 1
        subprocess.run([python, "-m", "venv", str(venvd)], check=True)
    subprocess.run(
        [str(pip), "install", "-U", "pip"],
        check=True,
    )
    subprocess.run(
        [str(pip), "install", "-e", f"{str(root)}[dev]"],
        check=True,
    )
    dev_stamp.parent.mkdir(parents=True, exist_ok=True)
    dev_stamp.touch()
    return 0


def cmd_lint(root: Path, venv_py: Path) -> int:
    p = venv_py
    if not p.is_file():
        print(f"Missing {p}. Run: make install", file=sys.stderr)
        return 1
    r = subprocess.run(
        [str(p), "-m", "ruff", "check", str(root)],
    )
    return r.returncode


def _pytest_extra() -> list[str]:
    extra = os.environ.get("PYTEST_ARGS", "")
    if not extra.strip():
        return []
    return shlex.split(extra, posix=True)


def cmd_test(root: Path, venv_py: Path) -> int:
    p = venv_py
    if not p.is_file():
        print(f"Missing {p}. Run: make install", file=sys.stderr)
        return 1
    r = subprocess.run(
        [str(p), "-m", "pytest", str(root / "tests"), *_pytest_extra()],
        cwd=root,
    )
    return r.returncode


def cmd_test_cov(root: Path, venv_py: Path) -> int:
    """Run pytest with coverage: term-missing report; gate is ``fail_under`` in ``[tool.coverage.report]``."""
    p = venv_py
    if not p.is_file():
        print(f"Missing {p}. Run: make install", file=sys.stderr)
        return 1
    extra = _pytest_extra()
    cfg = root / "pyproject.toml"
    r = subprocess.run(
        [
            str(p),
            "-m",
            "pytest",
            "tests",
            f"--rootdir={root}",
            "--cov",
            ".",
            "--cov-config",
            str(cfg),
            # fail_under: [tool.coverage.report] in pyproject.toml (single source of truth)
            "--cov-report=term-missing:skip-covered",
            *extra,
        ],
        cwd=root,
    )
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Voxium make targets (Python backend for GNU make).",
    )
    sp = ap.add_subparsers(dest="cmd", required=True)

    ph = sp.add_parser("help", help="List make targets (parses the Makefile ## markers)")
    ph.add_argument("--makefile", type=Path, required=True)

    pi = sp.add_parser("install", help="Create venv and pip install -e .")
    pi.add_argument("--root", type=Path, required=True)
    pi.add_argument("--venvd", type=Path, required=True)
    pi.add_argument("--python", type=str, required=True)

    pun = sp.add_parser("uninstall", help="Remove .venv, all *.egg-info, dev stamp")
    pun.add_argument("--root", type=Path, required=True)
    pun.add_argument("--venvd", type=Path, required=True)
    pun.add_argument("--dev-stamp", type=Path, required=True)

    pclean = sp.add_parser(
        "clean",
        help="Project caches, __pycache__, then uninstall (.venv, egg-info, dev stamp)",
    )
    pclean.add_argument("--root", type=Path, required=True)
    pclean.add_argument("--venvd", type=Path, required=True)
    pclean.add_argument("--dev-stamp", type=Path, required=True)

    pd = sp.add_parser(
        "disk-usage",
        help="Show disk usage for models/ and logs/ under the repo",
    )
    pd.add_argument("--root", type=Path, required=True)
    pd.add_argument("--venvd", type=Path, required=True)
    pd.add_argument("--dev-stamp", type=Path, required=True)

    pds = sp.add_parser("dev-stamp", help="Install [dev] and touch the stamp file")
    pds.add_argument("--root", type=Path, required=True)
    pds.add_argument("--venvd", type=Path, required=True)
    pds.add_argument("--dev-stamp", type=Path, required=True)
    pds.add_argument("--python", type=str, required=True)

    pl = sp.add_parser("lint", help="Run ruff in the venv")
    pl.add_argument("--root", type=Path, required=True)
    pl.add_argument("--venv-python", type=Path, required=True)

    pt = sp.add_parser("test", help="Run pytest (extra args: PYTEST_ARGS in env)")
    pt.add_argument("--root", type=Path, required=True)
    pt.add_argument("--venv-python", type=Path, required=True)

    ptc = sp.add_parser(
        "test-cov",
        help="Pytest + coverage (terminal per-file report, fail-under from pyproject); use PYTEST_ARGS= for more pytest flags",
    )
    ptc.add_argument("--root", type=Path, required=True)
    ptc.add_argument("--venv-python", type=Path, required=True)

    ns = ap.parse_args()

    if ns.cmd == "help":
        if not ns.makefile.is_file():
            print(f"help: not a file: {ns.makefile}", file=sys.stderr)
            return 2
        return cmd_help(ns.makefile)
    if ns.cmd == "install":
        return cmd_install(ns.root, ns.venvd, ns.python)
    if ns.cmd == "uninstall":
        return cmd_uninstall(ns.root, ns.venvd, ns.dev_stamp)
    if ns.cmd == "clean":
        return cmd_clean(ns.root, ns.venvd, ns.dev_stamp)
    if ns.cmd == "disk-usage":
        return cmd_disk_usage(ns.root, ns.venvd, ns.dev_stamp)
    if ns.cmd == "dev-stamp":
        return cmd_dev_stamp(ns.root, ns.venvd, ns.python, ns.dev_stamp)
    if ns.cmd == "lint":
        return cmd_lint(ns.root, ns.venv_python)
    if ns.cmd == "test":
        return cmd_test(ns.root, ns.venv_python)
    if ns.cmd == "test-cov":
        return cmd_test_cov(ns.root, ns.venv_python)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
