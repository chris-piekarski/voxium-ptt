"""Console entry: PTT / *vox* path (``[project.scripts] voxium = voxium.cli.main:main``). Brand: docs/brand.md."""

from __future__ import annotations


def main() -> int:
    from voxium.app import main as app_main

    return app_main()


__all__ = ["main"]
