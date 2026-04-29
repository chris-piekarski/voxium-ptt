"""`voxium.cli.main` → `voxium.app` delegate without loading the real client stack."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def test_cli_main_delegates_to_injected_app_main() -> None:
    app_mod = ModuleType("voxium.app")

    def app_main() -> int:
        return 77

    setattr(app_mod, "main", app_main)
    saved = sys.modules.get("voxium.app")
    try:
        sys.modules["voxium.app"] = app_mod
        sys.modules.pop("voxium.cli.main", None)
        cli = importlib.import_module("voxium.cli.main")
        assert cli.main() == 77
    finally:
        if saved is not None:
            sys.modules["voxium.app"] = saved
        else:
            sys.modules.pop("voxium.app", None)
        sys.modules.pop("voxium.cli.main", None)
