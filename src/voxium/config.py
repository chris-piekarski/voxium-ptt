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
    # ``limit`` / ``max_total_chars`` / ``pending_audio_max_mib`` — see ``voxium run --help`` (History group).
    history: dict[str, Any] = Field(default_factory=dict)
    # Client-only “UX chatter” (Gemma; see ``docs/ux-chatter-gemma.md``). ``enabled: false`` opts out.
    ux_chatter: dict[str, Any] = Field(default_factory=dict)
    # Microphone gain (0-10 scale, radio-style). Default auto mode with manual override via +/- keys.
    audio: dict[str, Any] = Field(default_factory=dict)
