"""Session status box: layout helper and recording-HUD switch."""
from rich.console import Console

from voxium.console_status import (
    PttStatusStep,
    build_status_box_panel,
    build_voxium_session_panel,
    print_agent_telemetry_panel,
    standing_by_ready_starts_new_panel,
    status_uses_recording_hud_line,
)


def test_standing_by_ready_starts_new_panel() -> None:
    assert standing_by_ready_starts_new_panel("◉ VOX/PTT · ON STATION", None)
    assert not standing_by_ready_starts_new_panel("◉ VOX/PTT · ON STATION", "")
    assert not standing_by_ready_starts_new_panel("📻 PTT ACTIVE", None)


def test_status_uses_recording_hud_line() -> None:
    assert status_uses_recording_hud_line("📻 PTT ACTIVE")
    assert not status_uses_recording_hud_line("🤖 EDGE INFERENCE")
    assert not status_uses_recording_hud_line("◉ VOX/PTT · ON STATION")


def test_build_status_box_panel_layout() -> None:
    p1 = build_status_box_panel("◉ VOX/PTT · ON STATION", "Standing by.")
    assert p1 is not None
    assert "Voxium" in str(p1.title)
    p2 = build_status_box_panel("📻 PTT ACTIVE", "F9 drops carrier", recording_hud="")
    p3 = build_status_box_panel("📻 PTT ACTIVE", "F9 drops carrier", recording_hud="0.0s  •  1 ch")
    assert p2 != p3


def test_session_panel_multi_step() -> None:
    p = build_voxium_session_panel(
        [
            PttStatusStep("◉ VOX/PTT · ON STATION", "Standing by."),
            PttStatusStep("🤖 EDGE INFERENCE", "Decoding…"),
        ],
        80,
    )
    assert "Voxium" in str(p.title)


def test_telemetry_panel_smoke() -> None:
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    print_agent_telemetry_panel(c, [("System: test", "info"), ("Note", "warning")])
    s = c.export_text(clear=True)
    assert "Downlink" in s and "System: test" in s
