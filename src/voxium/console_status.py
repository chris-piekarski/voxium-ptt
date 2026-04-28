"""PTT status strip (green, Live) + violet downlink panel (title varies: telemetry, /history, /gpu, etc.). Brand: docs/brand.md."""
from __future__ import annotations

import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from voxium.standby_telemetry import build_standby_detail_line

# Single pipeline border: one "Voxium" shell; inner lines = PTT & VOX, PTT ACTIVE, EDGE INFERENCE, etc.
_PTT_STATUS_BORDER = "#16a34a"
_PTT_BOX_NAME_STYLE = "bold #86efac"
_HUD_METER_STYLE = "dim #86efac"
_DETAIL_STYLE = "dim #cbd5e1"
_PTT_BRAND = "Voxium"

# Cap so a long session does not grow the live region without bound.
PTT_SESSION_MAX_STEPS = 20

# Matches :data:`voxium.app.STATUS_VOX_ON_STATION` (avoid import cycle).
ON_STATION_HEAD = "◉ PTT/VOX · ON STATION"

# On-station standby: FFT strip animation (see :mod:`voxium.standby_fft`); faster refresh = clearer motion.
_STANDBY_INTERVAL_S = 0.14


def voxium_panel_width(console: Console) -> int:
    """Width for every framed Voxium log panel (transcribe, PTT, downlink) — full terminal, one pipeline."""
    w = int(getattr(console, "width", None) or 0)
    return w if w > 0 else 80


@dataclass
class PttStatusStep:
    """One status line group in the session log (optionally + live HUD for the take)."""

    head: str
    detail: str | RenderableType = ""
    # None = two-line step; str = recording stats (plain); Rich = live PTT (e.g. stats + waveform).
    live_hud: str | RenderableType | None = None


def _head_style_for_status_title(head: str) -> str:
    """
    Head line color: VOX (open mic) = violet, on-station / PTT-ready = green,
    on-air PTT = amber-leaning, else default bold in green box.
    """
    u = head.upper()
    if "VOX" in u and "OPEN" in u:
        return "bold #c4b5fd"  # violet-300, distinct from the green on-air path
    if "ON STATION" in u:
        return "bold #86efac"  # green-200
    if "PTT" in u and "ACTIVE" in u:
        return "bold #fbbf24"
    if "INFERENCE" in u or "DECOD" in u:
        return "bold #a5b4fc"
    return "bold #ecfccb"  # lime-tint, readable in green box


def _group_from_status_steps(steps: list[PttStatusStep]) -> RenderableType:
    if not steps:
        return Text("—", style="dim")
    parts: list[RenderableType] = []
    for i, st in enumerate(steps):
        if i:
            parts.append(Text(" ", style="dim"))  # spacer between steps
        parts.append(Text(st.head, style=_head_style_for_status_title(st.head)))
        if st.detail:
            if isinstance(st.detail, str):
                parts.append(Text(st.detail, style=_DETAIL_STYLE))
            else:
                parts.append(st.detail)
        if st.live_hud is not None:
            if isinstance(st.live_hud, str):
                parts.append(
                    Text(st.live_hud if st.live_hud else " ", style=_HUD_METER_STYLE)
                )
            else:
                parts.append(st.live_hud)
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
    if "📻" in status and "PTT" in u and "ACTIVE" in u:
        return True
    return "VOX" in u and "OPEN" in u


def standing_by_ready_starts_new_panel(title: str, recording_hud: str | None) -> bool:
    """True when returning to on-station PTT/VOX — ends prior panel, opens a fresh one."""
    return (
        recording_hud is None
        and "PTT/VOX" in title
        and "ON STATION" in title.upper()
    )


def vox_open_listening_starts_fresh_panel(
    title: str,
    recording_hud: str | RenderableType | None,
) -> bool:
    """
    True when re-arming **VOX (open mic)** with a live meter line (``str``/Rich, including
    a spacer ``\" \"``). Same reset semantics as on-station: the current :class:`Live` stops,
    the prior content scrolls, and a **new** one-step session begins — so each VOX listen
    + transcribe + listen cycle is one readable green block (1:1 with PTT’s
    *new panel* after a take) instead of appending endless ``VOX OPEN`` rows in the same
    on-screen log.
    """
    if recording_hud is None:
        return False
    u = (title or "").upper()
    return "VOX" in u and "OPEN" in u


def _detail_is_standby_base(detail: str | RenderableType) -> bool:
    """True when the client’s on-station line should use the rotating standby detail (not e.g. PTT hint)."""
    if isinstance(detail, str):
        s = (detail or "").strip().lower()
    elif isinstance(detail, Text):
        s = (detail.plain or "").strip().lower()
    else:
        return False
    return s.startswith("standing by")


_TELEMETRY_LINE_STYLES: dict[str, str] = {
    "info": "dim #ddd6fe",
    "debug": "dim #a78bfa",
    "warning": "bold #fbbf24",
    "error": "bold #f87171",
}
_TELEMETRY_PANEL_BORDER = "#6d28d9"
# Default startup / batched client lines: ground telemetry (not a slash sub-channel).
DEFAULT_DOWNLINK_SUBTITLE = "agent / telemetry"
# Model fetch (HF) live progress — reads as comms / downlink, not the green PTT strip.
DOWNLINK_MODEL_FETCH_TITLE = "[bold #a78bfa]🛰️ Downlink[/] [dim]· model download[/]"


def format_downlink_title(subtitle: str) -> str:
    """Rich title for violet downlink :class:`rich.panel.Panel` instances: ``Downlink · <subtitle>``."""
    return f"[bold #a78bfa]Downlink[/] [dim]· {subtitle}[/]"


def downlink_subtitle_for_slash_line(line: str) -> str:
    """
    Subtitle token for the downlink frame after a committed ``/...`` line
    (aligned with :mod:`voxium.slash_commands` command names).
    """
    s = (line or "").strip()
    if not s.startswith("/"):
        return "command"
    parts = s.split()
    if not parts:
        return "command"
    first = parts[0].lstrip("/").lower()
    if not first:
        return "command"
    if first in ("help", "?", "h"):
        return "help"
    if first in ("history", "hist", "transcripts"):
        return "history"
    if first in ("mic", "m", "microphone", "input", "audio"):
        return "mic / capture"
    if first in ("gpu", "g", "cuda"):
        return "GPU"
    if first in ("models", "model"):
        return "models"
    if first in ("disk", "du", "usage"):
        return "disk / storage"
    return "session command"


def wrap_telemetry_block(text: str, width: int) -> str:
    """Wrap plain text to a readable width, preserving blank lines where present."""
    w = max(20, min(width, 200))
    s = text or ""
    if not s.strip():
        return s
    out: list[str] = []
    for line in s.splitlines():
        flat = line.rstrip()
        if not flat.strip():
            if not out or out[-1] != "":
                out.append("")
            continue
        out.extend(textwrap.wrap(flat, width=w) or [""])
    return "\n".join(out)


def _telemetry_style_for_level(level: str) -> str:
    return _TELEMETRY_LINE_STYLES.get(level, _TELEMETRY_LINE_STYLES["info"])


def build_downlink_telemetry_panel(
    console: Console,
    body: RenderableType,
    *,
    title: str | None = None,
) -> Panel:
    """One violet Downlink shell (same frame as :func:`print_agent_telemetry_panel`)."""
    w = voxium_panel_width(console)
    t = format_downlink_title(DEFAULT_DOWNLINK_SUBTITLE) if title is None else title
    return Panel(
        body,
        title=t,
        title_align="left",
        border_style=_TELEMETRY_PANEL_BORDER,
        padding=(0, 1),
        width=w,
    )


def print_input_mode_downlink(
    console: Console,
    *,
    mode: str,
    mode_hotkey_label: str,
    ptt_hotkey_label: str,
) -> None:
    """
    Violet downlink: input mode PTT↔VOX. Use this for mode hotkey events — not the green PTT/VOX
    on-air panel (``set_status``), so mode switches do not share one green log entry.
    """
    m = (mode or "").lower()
    if m == "vox":
        msg = (
            f"🎤 Input mode: VOX — open mic, utterance gating. "
            f"{mode_hotkey_label} → PTT (push-to-talk, {ptt_hotkey_label} to transmit when on PTT)."
        )
    else:
        msg = (
            f"📻 Input mode: PTT — push-to-talk, {ptt_hotkey_label} to transmit. "
            f"{mode_hotkey_label} → VOX (open mic) when you are ready."
        )
    print_agent_telemetry_panel(
        console,
        [(msg, "info")],
        downlink_subtitle="input mode",
    )


def print_agent_telemetry_panel(
    console: Console,
    entries: list[tuple[str, str]],
    *,
    downlink_subtitle: str | None = None,
) -> None:
    """
    Batched client startup / stack lines: read as ground telemetry, not PTT state
    (violet border — distinct from the green on-air status strip).
    """
    if not entries:
        return
    w = voxium_panel_width(console)
    inner = max(20, w - 4)
    t = Text()
    for i, (msg, level) in enumerate(entries):
        if i:
            t.append("\n\n")
        block = wrap_telemetry_block(msg, inner)
        t.append(block, style=_telemetry_style_for_level(level))
    sub = downlink_subtitle or DEFAULT_DOWNLINK_SUBTITLE
    ptitle = format_downlink_title(sub)
    console.print()
    console.print(build_downlink_telemetry_panel(console, t, title=ptitle))


def print_slash_command_downlink(
    console: Console,
    line: str,
    result: str,
    *,
    result_rich: Text | None = None,
) -> None:
    """One committed ``/...`` line + answer into the downlink (scrollback), not the PTT + footer Live area."""
    sub = downlink_subtitle_for_slash_line(line)
    ptitle = format_downlink_title(sub)
    if result_rich is not None:
        body = Text()
        body.append(f"Command: {line.rstrip()}\n\n", style=_TELEMETRY_LINE_STYLES["info"])
        body.append(result_rich)
        console.print()
        console.print(build_downlink_telemetry_panel(console, body, title=ptitle))
        return
    print_agent_telemetry_panel(
        console,
        [
            (f"Command: {line.rstrip()}", "info"),
            (result, "info"),
        ],
        downlink_subtitle=sub,
    )


class PttSessionStatusBox:
    """
    Green "Voxium" panel: each PTT *cycle* ends with on-station (or VOX re-arm) as a **fresh**
    one-step :class:`Live` so prior lines scroll and the log stays 1:1 readable. VOX (open mic) with
    a live meter uses the same *new green box* reset as on-station so each listen → transcribe
    → listen block is not an endless append in one on-screen panel. The **footer** (``/`` command
    line or idle hint) uses a **second**, nested :class:`Live` with ``transient=True`` so it is not
    duplicated when the root Live **stops** and the green panel is written to the log. Rich stacks
    nested lives as one on-screen region (see :meth:`rich.live.Live.get_renderable` for the first
    live in the console stack).
    """

    def __init__(
        self,
        console: Console,
        *,
        standby_context: Callable[[], dict] | None = None,
    ) -> None:
        self._console = console
        self._lock = threading.Lock()
        self._session_steps: list[PttStatusStep] = []
        self._main_live: Live | None = None
        self._footer_live: Live | None = None
        self._main_running = False
        self._suspended = False
        self._live_unavailable = False
        self._command_line_active = False
        self._command_text = ""
        self._command_hints: str = ""
        self._ptt_hint: str = "F9"
        self._mode_hint: str = "F7"
        # "ptt" = push-to-talk at rest; "vox" = open-mic (footer text + tint).
        self._footer_input_mode: str = "ptt"
        # On-station line: :func:`voxium.standby_telemetry.build_standby_detail_line` + real metrics.
        self._standby_context_fn: Callable[[], dict] | None = standby_context
        self._standby_tick: int = 0
        self._standby_stop = threading.Event()
        self._standby_thread: threading.Thread | None = None

    def set_command_line(
        self,
        text: str,
        active: bool,
        *,
        hints: str = "",
    ) -> None:
        """Footer: idle hint vs ``▶ /…`` when the operator is composing a slash line; optional hint line (Tab), copy."""
        with self._lock:
            self._command_text = text
            self._command_line_active = active
            self._command_hints = hints or ""
            if self._live_unavailable:
                return
            self._refresh_footer_only_unsafe()

    def set_ptt_hotkey_hint(self, key_label: str) -> None:
        """E.g. ``F9`` for the PTT / cancel hint in the idle footer (non-command mode)."""
        with self._lock:
            self._ptt_hint = (key_label or "PTT").upper()
            self._refresh_footer_only_unsafe()

    def set_mode_hotkey_hint(self, key_label: str) -> None:
        """E.g. ``F7`` for the PTT↔VOX hint in the idle footer."""
        with self._lock:
            self._mode_hint = (key_label or "F7").upper()
            self._refresh_footer_only_unsafe()

    def set_input_mode_for_footer(self, mode: str) -> None:
        """``ptt`` or ``vox`` — colors the idle footer so VOX is visually distinct from PTT."""
        with self._lock:
            self._footer_input_mode = "vox" if (mode or "").lower() == "vox" else "ptt"
            self._refresh_footer_only_unsafe()

    def set_status(
        self,
        title: str,
        detail: str | RenderableType = "",
        *,
        recording_hud: str | RenderableType | None = None,
    ) -> None:
        with self._lock:
            is_on_station_fresh = standing_by_ready_starts_new_panel(title, recording_hud)
            is_vox_fresh = vox_open_listening_starts_fresh_panel(title, recording_hud)
            if is_on_station_fresh or is_vox_fresh:
                if self._main_live and self._main_live.is_started:
                    try:
                        self._stop_footer_unsafe()
                        self._main_live.stop()
                    except Exception:
                        pass
                    self._main_live = None
                    self._main_running = False
                # On-station: no live meter. VOX open: keep meter (spacer) on the new sole step
                # so 1:1 with each listen cycle (PTT: new panel after a take, same idea).
                live = None if is_on_station_fresh else recording_hud
                self._session_steps = [
                    PttStatusStep(head=title, detail=detail, live_hud=live),
                ]
            else:
                self._session_steps.append(
                    PttStatusStep(head=title, detail=detail, live_hud=recording_hud),
                )
                self._trim_session_steps()
            self._rerender_unsafe()
        self._sync_standby_anim_thread()

    def update_recording_hud(self, content: str | RenderableType) -> None:
        with self._lock:
            if self._live_unavailable:
                return
            if self._suspended or not self._main_running or not self._main_live or not self._session_steps:
                return
            if self._session_steps[-1].live_hud is None:
                return
            self._session_steps[-1].live_hud = content
            if self._main_live.is_started:
                self._main_live.update(self._build_main_panel(), refresh=True)

    def freeze_before_external_output(self) -> None:
        self._stop_standby_anim()
        with self._lock:
            self._stop_footer_unsafe()
            if self._main_live and self._main_live.is_started:
                try:
                    self._main_live.stop()
                except Exception:
                    pass
            self._main_live = None
            self._main_running = False
            self._suspended = True

    def restore_live_after_scrollback_output(self) -> None:
        """Rebuild the green strip and nested slash footer after scrollback ``console.print`` output.

        If :meth:`freeze_before_external_output` already ran, the nested main ``Live`` is stopped
        and we only need a single full rerender. Do **not** call ``freeze`` from here: that would
        stop the current ``Live`` and leave a second copy of the green Voxium panel in the
        scrollback, then the rerender would add another — two identical on-screen boxes.

        When no freeze happened first (e.g. a violet downlink or ``/`` reply), ``_rerender_unsafe``
        updates or, if the UI was already suspended, restarts the ``Live`` from ``session_steps``.
        """
        self._stop_standby_anim()
        with self._lock:
            if self._live_unavailable or not self._session_steps:
                return
            self._rerender_unsafe()
        self._sync_standby_anim_thread()

    def close(self) -> None:
        self._stop_standby_anim()
        with self._lock:
            self._stop_footer_unsafe()
            if self._main_live and self._main_live.is_started:
                try:
                    self._main_live.stop()
                except Exception:
                    pass
            self._main_live = None
            self._main_running = False
            self._suspended = False
            self._live_unavailable = False
            self._command_line_active = False
            self._command_text = ""
            self._command_hints = ""

    def _box_width(self) -> int:
        return voxium_panel_width(self._console)

    def _trim_session_steps(self) -> None:
        while len(self._session_steps) > PTT_SESSION_MAX_STEPS:
            self._session_steps.pop(0)

    def _standby_row_active_unsafe(self) -> bool:
        if not self._session_steps or self._suspended or self._live_unavailable:
            return False
        last = self._session_steps[-1]
        if last.head != ON_STATION_HEAD or last.live_hud is not None:
            return False
        return _detail_is_standby_base(last.detail)

    def _build_main_panel(self) -> Panel:
        w = self._box_width()
        if self._standby_row_active_unsafe():
            last = self._session_steps[-1]
            ctx = self._standby_context_fn() if self._standby_context_fn else {}
            detail = build_standby_detail_line(self._standby_tick, ctx)
            steps = list(self._session_steps)
            steps[-1] = PttStatusStep(last.head, detail, last.live_hud)
            return build_voxium_session_panel(steps, w)
        return build_voxium_session_panel(self._session_steps, w)

    def _standby_anim_wanted_unsafe(self) -> bool:
        return (
            self._standby_row_active_unsafe()
            and self._main_running
            and not self._suspended
            and self._main_live is not None
        )

    def _stop_standby_anim(self) -> None:
        self._standby_stop.set()
        t = self._standby_thread
        if t is not None and t.is_alive():
            t.join(timeout=0.3)
        self._standby_thread = None
        self._standby_stop.clear()

    def _standby_loop(self) -> None:
        while not self._standby_stop.wait(_STANDBY_INTERVAL_S):
            with self._lock:
                if not self._standby_anim_wanted_unsafe() or not self._main_live:
                    break
                self._standby_tick = (self._standby_tick + 1) % 1_000_000
                if not self._standby_stop.is_set():
                    try:
                        self._main_live.update(self._build_main_panel(), refresh=True)
                    except Exception:
                        break

    def _sync_standby_anim_thread(self) -> None:
        with self._lock:
            want = self._standby_anim_wanted_unsafe()
        if not want:
            self._stop_standby_anim()
            return
        t = self._standby_thread
        if t is not None and t.is_alive():
            return
        self._stop_standby_anim()
        with self._lock:
            self._standby_tick = 0
        self._standby_thread = threading.Thread(
            target=self._standby_loop,
            name="voxium-standby-anim",
            daemon=True,
        )
        self._standby_thread.start()

    def _build_footer(self) -> Panel:
        w = self._box_width()
        inner = max(16, w - 6)
        if self._command_line_active:
            wrapped = wrap_telemetry_block(self._command_text, inner)
            if self._command_hints:
                hwrap = wrap_telemetry_block(self._command_hints, inner)
                line_block = Text(wrapped or " ", style="white") + Text(" ▎", style="dim #38bdf8")
                body = Group(
                    Text("▶ ", style="bold #38bdf8") + line_block,
                    Text(hwrap or " ", style="dim #6b7b96"),
                )
            else:
                body = (
                    Text("▶ ", style="bold #38bdf8")
                    + Text(wrapped or " ", style="white")
                    + Text(" ▎", style="dim #38bdf8")
                )
        else:
            if self._footer_input_mode == "vox":
                body = (
                    Text("  🎤  ", style="bold #a78bfa")
                    + Text("VOX on  ", style="bold #c4b5fd")
                    + Text("·  ", style="dim #334155")
                    + Text("open mic  ", style="dim #a5b4fc")
                    + Text("·  ", style="dim #334155")
                    + Text(f"{self._mode_hint} → PTT  ", style="dim #94a3b8")
                    + Text("·  ", style="dim #334155")
                    + Text(f"{self._ptt_hint} idle  ", style="dim #7dd3fc")
                    + Text("·  ", style="dim #334155")
                    + Text("  /  ", style="dim #94a3b8")
                    + Text("command  ", style="dim #64748b")
                )
            else:
                body = (
                    Text("  📻  ", style="bold #86efac")
                    + Text("PTT path  ", style="dim #a7f3d0")
                    + Text("·  ", style="dim #334155")
                    + Text("ready (", style="dim #94a3b8")
                    + Text(self._ptt_hint, style="dim bold #a7f3d0")
                    + Text(")  ·  ", style="dim #94a3b8")
                    + Text(f"{self._mode_hint} → VOX  ", style="dim #c4b5fd")
                    + Text("  ·  ", style="dim #334155")
                    + Text("  /  ", style="dim #94a3b8")
                    + Text("command  ", style="dim #64748b")
                )
        return Panel(
            body,
            style="on #0b1220",
            border_style="dim #334155",
            padding=(0, 1),
            width=w,
        )

    def _stop_footer_unsafe(self) -> None:
        fl = self._footer_live
        if fl is not None and fl.is_started:
            try:
                fl.stop()
            except Exception:
                pass
        self._footer_live = None

    def _start_footer_unsafe(self) -> None:
        if self._footer_live is not None and self._footer_live.is_started:
            return
        foot = self._build_footer()
        # No auto_refresh — nested Live would refresh the root panel every tick (see main Live).
        self._footer_live = Live(
            foot,
            console=self._console,
            auto_refresh=False,
            transient=True,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._footer_live.start(refresh=True)

    def _refresh_footer_only_unsafe(self) -> None:
        if self._footer_live is not None and self._footer_live.is_started:
            self._footer_live.update(self._build_footer(), refresh=True)
        elif self._main_running and not self._suspended:
            self._rerender_unsafe()
        # else: full render will run on next set_status

    def _rerender_unsafe(self) -> None:
        main = self._build_main_panel()
        foot = self._build_footer()
        g_fallback = Group(main, foot)
        if self._live_unavailable:
            self._console.print(g_fallback)
            return
        need_new_main = not self._main_running or self._suspended
        if need_new_main:
            self._stop_footer_unsafe()
            if self._main_live and self._main_live.is_started:
                try:
                    self._main_live.stop()
                except Exception:
                    pass
            self._main_live = None
            self._suspended = False
            # No auto_refresh: a nested footer Live with auto_refresh was calling the root
            # Live's refresh() ~12x/s, redrawing the full session panel and causing heavy flicker
            # (very noticeable in VOX open-mic HUD updates). We repaint on explicit update only.
            self._main_live = Live(
                main,
                console=self._console,
                auto_refresh=False,
                transient=False,
            )
            try:
                self._main_live.start(refresh=True)
            except Exception as exc:
                self._live_unavailable = True
                self._main_running = False
                self._main_live = None
                self._suspended = False
                self._console.print(
                    "[dim]Live PTT status strip is unavailable in this terminal "
                    f"([{type(exc).__name__}]). Status lines print to the scrollback instead.[/]\n"
                )
                self._console.print(g_fallback)
                return
            self._main_running = True
            try:
                self._start_footer_unsafe()
            except Exception:
                self._main_live.update(g_fallback, refresh=True)
        elif self._main_live is not None:
            self._main_live.update(main, refresh=True)
            if self._footer_live is not None and self._footer_live.is_started:
                self._footer_live.update(foot, refresh=True)
            else:
                try:
                    self._start_footer_unsafe()
                except Exception:
                    self._main_live.update(g_fallback, refresh=True)
