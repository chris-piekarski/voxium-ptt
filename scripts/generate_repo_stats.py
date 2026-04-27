#!/usr/bin/env python3
"""
Generate basic repository stats (lines of code by area and voxium module) and
update ``docs/repository-stats.md`` between:

<!-- BEGIN: REPO-STATS -->
... generated ...
<!-- END: REPO-STATS -->

Run: ``python scripts/generate_repo_stats.py`` or ``make repo-stats``
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "docs" / "repository-stats.md"

# Directories to ignore when scanning
IGNORE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    "__pycache__",
    "htmlcov",
    ".eggs",
    "logs",
    "models",
    "history",
    "node_modules",
    ".codex",
}

# File extensions to consider and their comment markers (for code line counting)
COMMENT_PREFIX = {
    ".py": "#",
    ".sh": "#",
    ".bash": "#",
    ".yml": "#",
    ".yaml": "#",
    ".toml": "#",
    ".ini": ";",
    ".service": "#",
}

CONSIDER_EXTS = set(COMMENT_PREFIX.keys()) | {".md", ".txt", ".json"}

HEADER = """# Repository statistics

> Regenerate: `make repo-stats` (or: `python scripts/generate_repo_stats.py`).

"""


def _prune_dirname(name: str) -> bool:
    """True if *name* is a directory we should not descend into."""
    if name in IGNORE_DIRS or name == "__pycache__":
        return True
    if name.startswith(".venv"):
        return True
    return False


def count_file(path: Path) -> tuple[int, int]:
    """Return (total_lines, code_lines). Code excludes blanks and #/comment-prefixed lines when known."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0, 0

    total = 0
    code = 0
    prefix = COMMENT_PREFIX.get(path.suffix)

    for raw in text.splitlines():
        total += 1
        line = raw.strip()
        if not line:
            continue
        if prefix and line.startswith(prefix):
            continue
        code += 1
    return total, code


def iter_repo_files(root: Path) -> Iterable[Path]:
    """Walk the repo, skipping *entire* subtrees for IGNORE_DIRS (fast vs ``rglob``)."""
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, onerror=None, followlinks=False
    ):
        dirnames[:] = [d for d in dirnames if not _prune_dirname(d)]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            if p.suffix.lower() in CONSIDER_EXTS:
                yield p


def bump(bucket: dict[str, dict[str, int]], key: str, total: int, code: int) -> None:
    d = bucket.setdefault(key, {"total": 0, "code": 0})
    d["total"] += total
    d["code"] += code


def aggregate_stats() -> dict[str, Any]:
    """Stats by top-level *area* and by immediate package under src/voxium (Python)."""
    by_area: dict[str, dict[str, int]] = {}
    by_module_py: dict[str, dict[str, int]] = {}

    for f in iter_repo_files(REPO_ROOT):
        total, code = count_file(f)
        parts = f.relative_to(REPO_ROOT).parts
        area = parts[0] if parts else "other"
        if area not in {"src", "tests", "docs", "scripts"}:
            area = "other"
        bump(by_area, area, total, code)

        if f.suffix != ".py":
            continue
        try:
            rel = f.relative_to(REPO_ROOT / "src" / "voxium")
        except ValueError:
            continue
        if len(rel.parts) <= 1:
            bump(by_module_py, "root", total, code)
        else:
            bump(by_module_py, rel.parts[0], total, code)

    return {
        "by_area": by_area,
        "by_module_py": by_module_py,
        "overall": {
            "total": sum(v["total"] for v in by_area.values()),
            "code": sum(v["code"] for v in by_area.values()),
        },
    }


def format_number(n: int) -> str:
    return f"{n:,}"


def generate_markdown(stats: dict[str, Any]) -> str:
    by_area = stats["by_area"]
    by_module_py = stats["by_module_py"]
    overall = stats["overall"]

    pie_lines = ["```mermaid", "pie title Code LOC by area"]
    for name, data in sorted(by_area.items(), key=lambda kv: kv[1]["code"], reverse=True):
        if data["code"] == 0:
            continue
        pie_lines.append(f'  "{name}" : {data["code"]}')

    pie_lines.append("```")
    if len(pie_lines) == 3:
        pie_lines = [
            "_No code lines in tracked areas; expand CONSIDER_EXTS or run from repo root._"
        ]

    top_n = 8
    sorted_mods = sorted(by_module_py.items(), key=lambda kv: kv[1]["code"], reverse=True)
    module_pie = ["```mermaid", "pie title Code LOC by voxium package (Python)"]
    top = sorted_mods[:top_n]
    other_sum = sum(v["code"] for _, v in sorted_mods[top_n:])
    for name, data in top:
        if data["code"] == 0:
            continue
        module_pie.append(f'  "{name}" : {data["code"]}')
    if other_sum > 0:
        module_pie.append(f'  "other" : {other_sum}')
    module_pie.append("```")

    area_rows = [
        "| Area | Code LOC | Total lines |",
        "|------|----------|-------------|",
    ]
    for name, data in sorted(by_area.items(), key=lambda kv: kv[1]["code"], reverse=True):
        area_rows.append(
            f"| {name} | {format_number(data['code'])} | {format_number(data['total'])} |"
        )

    mod_rows = [
        "| Package (`src/voxium/`, `.py`) | Code LOC | Total lines |",
        "|----------------------------------|----------|-------------|",
    ]
    for name, data in sorted(by_module_py.items(), key=lambda kv: kv[1]["code"], reverse=True)[:12]:
        mod_rows.append(
            f"| {name} | {format_number(data['code'])} | {format_number(data['total'])} |"
        )

    parts_out = [
        "## Current stats",
        "",
        f"- **Code LOC (approx.):** {format_number(overall['code'])}",
        f"- **Total lines (tracked extensions):** {format_number(overall['total'])}",
        "",
    ]
    parts_out.extend(pie_lines)
    parts_out.extend(["", "### LOC by area", *area_rows, ""])
    parts_out.extend(module_pie)
    parts_out.extend(
        [
            "",
            "### Top Python packages",
            *mod_rows,
            "",
            "_Note: “Code LOC” is non-blank lines minus line-leading `#` comments (where applicable for that extension). "
            "Python virtualenvs (dirs named `venv/`, `env/`, and any `.venv*`) and runtime data dirs "
            "(`models/`, `history/`, `logs/`, …) are excluded from scans._",
        ]
    )
    return "\n".join(parts_out)


def update_doc(new_section: str) -> None:
    begin = "<!-- BEGIN: REPO-STATS -->"
    end = "<!-- END: REPO-STATS -->"
    block = f"{begin}\n\n{new_section.rstrip()}\n\n{end}\n"
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not OUT_PATH.exists():
        OUT_PATH.write_text(HEADER + block, encoding="utf-8")
        return
    content = OUT_PATH.read_text(encoding="utf-8")
    if begin in content and end in content:
        pre, rest = content.split(begin, 1)
        _mid, post = rest.split(end, 1)
        OUT_PATH.write_text(pre + block + post, encoding="utf-8")
    else:
        OUT_PATH.write_text(
            content.rstrip() + "\n\n---\n\n" + HEADER + block,
            encoding="utf-8",
        )


def main() -> None:
    update_doc(generate_markdown(aggregate_stats()))
    print(f"Wrote: {OUT_PATH}")


if __name__ == "__main__":
    main()
