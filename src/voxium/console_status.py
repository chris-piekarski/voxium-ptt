"""PTT status strip (green, Live) + agent/telemetry downlink panel (violet). Brand: docs/brand.md."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

# Single pipeline border: one "Voxium" shell; inner lines = VOX/PTT / PTT ACTIVE / EDGE INFERENCE, etc.
_PTT_STATUS_BORDER = "#16a34a"
_PTT_BOX_NAME_STYLE = "bold #86efac"
_HUD_METER_STYLE = "dim #86efac"
_DETAIL_STYLE = "dim #cbd5e1"
_PTT_BRAND = "Voxium"

# Cap so a long session does not grow the live region without bound.
PTT_SESSION_MAX_STEPS = 20


@dataclass
class PttStatusStep:
    """One status line group in the session log (optionally + live HUD for the take)."""

    head: str
    detail: str = ""
    # None = two-line step; str = recording step (third line updates in place)
    live_hud: str | None = None


def _group_from_status_steps(steps: list[PttStatusStep]) -> RenderableType:
    if not steps:
        return Text("—", style="dim")
    parts: list[Text] = []
    for i, st in enumerate(steps):
        if i:
            parts.append(Text(" ", style="dim"))  # spacer between steps
        parts.append(Text(st.head, style="bold"))
        if st.detail:
            parts.append(Text(st.detail, style=_DETAIL_STYLE))
        if st.live_hud is not None:
            parts.append(Text(st.live_hud if st.live_hud else " ", style=_HUD_METER_STYLE))
    return Group(*parts)


def build_voxium_session_panel(steps: list[PttStatusStep], box_width: int) -> Panel:
    """Green Voxium panel from an ordered list of session steps (tests / inspection)."""
    return Panel(
        _group_from_status_steps(steps),
        title=Text(_PTT_BRAND, style=_PTT_BOX_NAME_STYLE),
        title_align="left",
        border_style=_PTT_STATUS_BORDER,
        padding=(0, 1),
        width=box_width,
    )


def build_status_box_panel(
    title: str,
    detail: str = "",
    *,
    recording_hud: str | None = None,
    box_width: int | None = None,
) -> Panel:
    """Pure builder for the green Voxium session panel (used by Live and for tests)."""
    top = Text(title, style="bold")
    if recording_hud is not None:
        de = Text(detail, style=_DETAIL_STYLE) if detail else Text("")
        hud = Text(recording_hud, style=_HUD_METER_STYLE) if recording_hud else Text(" ", style=_HUD_METER_STYLE)
        body: RenderableType = Group(top, de, hud)
    elif detail:
        body = Group(top, Text(detail, style=_DETAIL_STYLE))
    else:
        body = top
    brand = Text(_PTT_BRAND, style=_PTT_BOX_NAME_STYLE)
    if box_width is not None:
        return Panel(
            body,
            title=brand,
            title_align="left",
            border_style=_PTT_STATUS_BORDER,
            padding=(0, 1),
            width=box_width,
        )
    return Panel(
        body,
        title=brand,
        title_align="left",
        border_style=_PTT_STATUS_BORDER,
        padding=(0, 1),
        expand=False,
    )


def status_uses_recording_hud_line(status: str) -> bool:
    u = status.upper()
    return "📻" in status and "PTT" in u and "ACTIVE" in u


def standing_by_ready_starts_new_panel(title: str, recording_hud: str | None) -> bool:
    """True when returning to on-station VOX/PTT — ends prior panel, opens a fresh one."""
    return (
        recording_hud is None
        and "VOX/PTT" in title
        and "ON STATION" in title.upper()
    )


_TELEMETRY_LINE_STYLES: dict[str, str] = {
    "info": "dim #ddd6fe",
    "debug": "dim #a78bfa",
    "warning": "bold #fbbf24",
    "error": "bold #f87171",
}
_TELEMETRY_PANEL_BORDER = "#6d28d9"
_TELEMETRY_PANEL_TITLE = "[bold #a78bfa]Downlink[/] [dim]· agent / telemetry[/]"


def _telemetry_style_for_level(level: str) -> str:
    return _TELEMETRY_LINE_STYLES.get(level, _TELEMETRY_LINE_STYLES["info"])


def print_agent_telemetry_panel(console: Console, entries: list[tuple[str, str]]) -> None:
    """
    Batched client startup / stack lines: read as ground telemetry, not PTT state
    (violet border — distinct from the green on-air status strip).
    """
    if not entries:
        return
    t = Text()
    for i, (msg, level) in enumerate(entries):
        if i:
            t.append("\n")
        t.append(msg, style=_telemetry_style_for_level(level))
    w = min(100, max(72, (console.width or 88)))
    console.print()
    console.print(
        Panel(
            t,
            title=_TELEMETRY_PANEL_TITLE,
            border_style=_TELEMETRY_PANEL_BORDER,
            padding=(0, 1),
            width=w,
        )
    )


class PttSessionStatusBox:
    """
    Green "Voxium" panel(s): each PTT *cycle* is one panel — steps append until ◉ VOX/PTT · ON STATION,
    which stops the Live (previous panel stays in scrollback) and starts a new panel with
    only that line. PTT ACTIVE HUD updates in place on the current step. Suspend Live for
    long external Rich (transcription) output.
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._lock = threading.Lock()
        self._session_steps: list[PttStatusStep] = []
        self._live: Live | None = None
        self._live_running = False
        self._suspended = False

    def set_status(
        self,
        title: str,
        detail: str = "",
        *,
        recording_hud: str | None = None,
    ) -> None:
        with self._lock:
            if standing_by_ready_starts_new_panel(title, recording_hud):
                if self._live and self._live_running:
                    try:
                        self._live.stop()
                    except Exception:
                        pass
                    self._live_running = False
                self._session_steps = [
                    PttStatusStep(head=title, detail=detail, live_hud=None),
                ]
            else:
                self._session_steps.append(
                    PttStatusStep(head=title, detail=detail, live_hud=recording_hud),
                )
                self._trim_session_steps()
            self._rerender_unsafe()

    def update_recording_hud(self, line: str) -> None:
        with self._lock:
            if self._suspended or not self._live_running or not self._live or not self._session_steps:
                return
            if self._session_steps[-1].live_hud is None:
                return
            self._session_steps[-1].live_hud = line
            p = self._build_panel()
            self._live.update(p)

    def freeze_before_external_output(self) -> None:
        with self._lock:
            if self._live and self._live_running:
                try:
                    self._live.stop()
                except Exception:
                    pass
            self._live_running = False
            self._suspended = True

    def close(self) -> None:
        with self._lock:
            if self._live and self._live_running:
                try:
                    self._live.stop()
                except Exception:
                    pass
            self._live_running = False
            self._suspended = False
            self._live = None

    def _box_width(self) -> int:
        w = int(self._console.width or 88)
        return min(120, max(72, w))

    def _trim_session_steps(self) -> None:
        while len(self._session_steps) > PTT_SESSION_MAX_STEPS:
            self._session_steps.pop(0)

    def _build_panel(self) -> Panel:
        return build_voxium_session_panel(
            self._session_steps,
            self._box_width(),
        )

    def _rerender_unsafe(self) -> None:
        p = self._build_panel()
        # Only (re)create Live on first draw or after freeze — never on 2/3 line phase changes
        # (that was stacking multiple boxes in the scrollback).
        need_new_live = not self._live_running or self._suspended
        if need_new_live:
            if self._live and self._live_running:
                try:
                    self._live.stop()
                except Exception:
                    pass
            self._suspended = False
            self._live_running = True
            self._live = Live(
                p,
                console=self._console,
                auto_refresh=True,
                transient=False,
                refresh_per_second=12,
            )
            self._live.start()
        elif self._live is not None:
            self._live.update(p)
