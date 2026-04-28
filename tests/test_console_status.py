"""Session status box: layout helper and recording-HUD switch."""
from rich.console import Console

from voxium.standby_fft import SPECTRUM_BARS, SPECTRUM_DISPLAY_WIDTH

from voxium.console_status import (
    PttStatusStep,
    build_status_box_panel,
    build_voxium_session_panel,
    downlink_subtitle_for_slash_line,
    print_agent_telemetry_panel,
    print_input_mode_downlink,
    standing_by_ready_starts_new_panel,
    status_uses_recording_hud_line,
    vox_open_listening_starts_fresh_panel,
    voxium_panel_width,
)
from voxium.standby_telemetry import build_standby_detail_line


def test_standing_by_ready_starts_new_panel() -> None:
    assert standing_by_ready_starts_new_panel("◉ PTT/VOX · ON STATION", None)
    assert not standing_by_ready_starts_new_panel("◉ PTT/VOX · ON STATION", "")
    assert not standing_by_ready_starts_new_panel("📻 PTT ACTIVE", None)


def test_vox_open_fresh_panel_predicate() -> None:
    vox_open = "🎙️ PTT/VOX · VOX (OPEN MIC)"
    assert vox_open_listening_starts_fresh_panel(vox_open, " ")
    assert vox_open_listening_starts_fresh_panel(vox_open, "")
    assert not vox_open_listening_starts_fresh_panel(vox_open, None)
    assert not vox_open_listening_starts_fresh_panel("📻 PTT ACTIVE", " ")
    assert not vox_open_listening_starts_fresh_panel("◉ PTT/VOX · ON STATION", " ")


def test_status_uses_recording_hud_line() -> None:
    assert status_uses_recording_hud_line("📻 PTT ACTIVE")
    assert not status_uses_recording_hud_line("🤖 EDGE INFERENCE")
    assert not status_uses_recording_hud_line("◉ PTT/VOX · ON STATION")


def test_build_status_box_panel_layout() -> None:
    p1 = build_status_box_panel("◉ PTT/VOX · ON STATION", "Standing by.")
    assert p1 is not None
    assert "Voxium" in str(p1.title)
    p2 = build_status_box_panel("📻 PTT ACTIVE", "F9 drops carrier", recording_hud="")
    p3 = build_status_box_panel("📻 PTT ACTIVE", "F9 drops carrier", recording_hud="0.0s  •  1 ch")
    assert p2 != p3


def test_session_panel_multi_step() -> None:
    p = build_voxium_session_panel(
        [
            PttStatusStep("◉ PTT/VOX · ON STATION", "Standing by."),
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
    assert "agent" in s and "telemetry" in s


def test_standby_detail_line_no_decode_vs_with_decode() -> None:
    from voxium.standby_fft import reset_spectrum_state

    reset_spectrum_state()
    empty = build_standby_detail_line(0, {})
    have = build_standby_detail_line(
        0,
        {
            "has_last_decode": True,
            "sample_rate_hz": 16000,
            "channels": 1,
            "last_realtime_factor": 0.5,
            "last_audio_seconds": 1.2,
            "last_model_name": "base",
        },
    )
    assert "Standing by" in empty.plain and "Standing by" in have.plain
    assert SPECTRUM_BARS[0] * SPECTRUM_DISPLAY_WIDTH in empty.plain
    assert "kHz" in empty.plain and "kHz" in have.plain
    assert "no decode yet" in empty.plain
    assert "last RTF" in have.plain and "base" in have.plain


def test_downlink_subtitle_for_slash_line() -> None:
    assert downlink_subtitle_for_slash_line("/history") == "history"
    assert downlink_subtitle_for_slash_line("/hist 2") == "history"
    assert downlink_subtitle_for_slash_line("/gpu") == "GPU"
    assert downlink_subtitle_for_slash_line("/mic") == "mic / capture"
    assert downlink_subtitle_for_slash_line("/models base") == "models"
    assert downlink_subtitle_for_slash_line("/disk") == "disk / storage"
    assert downlink_subtitle_for_slash_line("/du") == "disk / storage"
    assert downlink_subtitle_for_slash_line("/unknown-cmd") == "session command"


def test_input_mode_downlink_in_violet_panel() -> None:
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    print_input_mode_downlink(
        c, mode="vox", mode_hotkey_label="F7", ptt_hotkey_label="F9"
    )
    t = c.export_text(clear=True)
    assert "Downlink" in t and "input mode" in t
    assert "🎤" in t and "VOX" in t


def test_voxium_panel_width_uses_terminal_columns() -> None:
    class _C:
        width = 100

    assert voxium_panel_width(_C()) == 100

    class _Zero:
        width = 0

    assert voxium_panel_width(_Zero()) == 80
