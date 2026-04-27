"""Validated user configuration (YAML). Kept separate from CLI defaults for a single source of truth."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VoxiumUserConfig(BaseModel):
    """
    Shape of ``~/.config/voxium/config.yaml``.
    Extra top-level keys are allowed for forward compatibility; unknown keys are not stripped
    if we use ``extra='allow'`` on a future nested model — for now we only parse known sections.
    """

    model_config = ConfigDict(extra="allow")

    hotkeys: dict[str, Any] = Field(default_factory=dict)
    transcription: dict[str, Any] = Field(default_factory=dict)
    server: dict[str, Any] = Field(default_factory=dict)
    ui: dict[str, Any] = Field(default_factory=dict)
    history: dict[str, Any] = Field(default_factory=dict)
