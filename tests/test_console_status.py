"""Session status box: layout helper and recording-HUD switch."""

from typing import cast

from rich.console import Console

from voxium.standby_fft import SPECTRUM_BARS, SPECTRUM_DISPLAY_WIDTH

from voxium.console_status import (
    ON_STATION_HEAD,
    PttSessionStatusBox,
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
    assert standing_by_ready_starts_new_panel("◉ PTT/VOX · Standing by", None)
    assert not standing_by_ready_starts_new_panel("◉ PTT/VOX · Standing by", "")
    assert not standing_by_ready_starts_new_panel("📻 PTT ACTIVE", None)


def test_vox_open_fresh_panel_predicate() -> None:
    vox_open = "🎙️ PTT/VOX · VOX (OPEN MIC)"
    assert vox_open_listening_starts_fresh_panel(vox_open, " ")
    assert vox_open_listening_starts_fresh_panel(vox_open, "")
    assert not vox_open_listening_starts_fresh_panel(vox_open, None)
    assert not vox_open_listening_starts_fresh_panel("📻 PTT ACTIVE", " ")
    assert not vox_open_listening_starts_fresh_panel("◉ PTT/VOX · Standing by", " ")


def test_status_uses_recording_hud_line() -> None:
    assert status_uses_recording_hud_line("📻 PTT ACTIVE")
    assert not status_uses_recording_hud_line("🤖 EDGE INFERENCE")
    assert not status_uses_recording_hud_line("◉ PTT/VOX · Standing by")


def test_build_status_box_panel_layout() -> None:
    p1 = build_status_box_panel("◉ PTT/VOX · Standing by", "Standing by.")
    assert p1 is not None
    assert "Voxium" in str(p1.title)
    p2 = build_status_box_panel("📻 PTT ACTIVE", "F9 drops carrier", recording_hud="")
    p3 = build_status_box_panel(
        "📻 PTT ACTIVE", "F9 drops carrier", recording_hud="0.0s  •  1 ch"
    )
    assert p2 != p3


def test_session_panel_multi_step() -> None:
    p = build_voxium_session_panel(
        [
            PttStatusStep("◉ PTT/VOX · Standing by", "Standing by."),
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
    assert downlink_subtitle_for_slash_line("/health") == "health"
    assert downlink_subtitle_for_slash_line("/history") == "history"
    assert downlink_subtitle_for_slash_line("/hist 2") == "history"
    assert downlink_subtitle_for_slash_line("/gpu") == "GPU"
    assert downlink_subtitle_for_slash_line("/mic") == "mic / capture"
    assert downlink_subtitle_for_slash_line("/models base") == "models"
    assert downlink_subtitle_for_slash_line("/polish on") == "re-encode"
    assert downlink_subtitle_for_slash_line("/p on") == "re-encode"
    assert downlink_subtitle_for_slash_line("/re-encode on") == "re-encode"
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


def test_command_footer_cursor_sits_after_text() -> None:
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    box = PttSessionStatusBox(c)
    box.set_command_line("/help", True)
    c.print(box._build_footer())
    text = c.export_text(clear=True)
    assert "/help▎" in text
    assert "/help ▎" not in text


def test_idle_footer_shows_mic_gain_chip_and_updates_on_set_level() -> None:
    c = Console(force_terminal=True, width=140, record=True, color_system="truecolor")
    box = PttSessionStatusBox(c)
    c.print(box._build_footer())
    default_text = c.export_text(clear=True)
    assert "GAIN" in default_text
    assert "(auto)" in default_text

    box.set_mic_gain_level(8.0, auto=False)
    c.print(box._build_footer())
    boosted = c.export_text(clear=True)
    assert "GAIN" in boosted
    assert "8.0" in boosted
    assert "(man)" in boosted


def test_idle_footer_shows_morse_audio_toggle_state() -> None:
    c = Console(force_terminal=True, width=120, record=True, color_system="truecolor")
    box = PttSessionStatusBox(c)
    c.print(box._build_footer())
    off_text = c.export_text(clear=True)

    box.set_morse_audio_state(True)
    c.print(box._build_footer())
    on_text = c.export_text(clear=True)

    assert "M Morse" in off_text and "off" in off_text
    assert "M Morse on" in on_text


def test_standby_head_shows_compact_morse_indicator() -> None:
    c = Console(force_terminal=True, width=120, record=True, color_system="truecolor")
    box = PttSessionStatusBox(
        c, standby_context=lambda: {"last_transcript_text": "sos"}
    )
    box._session_steps = [PttStatusStep(ON_STATION_HEAD, "")]

    c.print(box._build_main_panel())
    text = c.export_text(clear=True)
    assert "PTT/VOX · Standing by" in text
    assert "M 🔇" in text

    box = PttSessionStatusBox(
        c,
        standby_context=lambda: {
            "last_transcript_text": "sos",
            "morse_audio_playing": True,
        },
    )
    box._session_steps = [PttStatusStep(ON_STATION_HEAD, "")]

    c.print(box._build_main_panel())
    text = c.export_text(clear=True)
    assert "PTT/VOX · Standing by" in text
    assert "M 🔊" in text


def test_voxium_panel_width_uses_terminal_columns() -> None:
    class _C:
        width = 100

    assert voxium_panel_width(cast(Console, _C())) == 100

    class _Zero:
        width = 0

    assert voxium_panel_width(cast(Console, _Zero())) == 80


def test_status_box_builders_pick_up_changed_console_width() -> None:
    class _MutableConsole:
        width = 100

    c = cast(Console, _MutableConsole())
    box = PttSessionStatusBox(c)

    assert box._build_footer().width == 100
    c.width = 72
    assert box._build_footer().width == 72

    box._session_steps = [PttStatusStep("◉ PTT/VOX · Standing by", "")]
    assert box._build_main_panel().width == 72
    c.width = 90
    assert box._build_main_panel().width == 90


def test_recording_hud_refresh_updates_footer_at_current_width() -> None:
    class _MutableConsole:
        width = 100

    class _FakeLive:
        is_started = True

        def __init__(self) -> None:
            self.widths: list[int] = []

        def update(self, renderable, *, refresh: bool = False) -> None:
            assert refresh
            self.widths.append(renderable.width)

    c = cast(Console, _MutableConsole())
    box = PttSessionStatusBox(c)
    main_live = _FakeLive()
    footer_live = _FakeLive()
    box._main_live = main_live
    box._footer_live = footer_live
    box._main_running = True
    box._session_steps = [PttStatusStep("📻 PTT ACTIVE", "", live_hud="old")]

    c.width = 76
    box.update_recording_hud("new")

    assert main_live.widths[-1] == 76
    assert footer_live.widths[-1] == 76


# -----------------------------------------------------------------------------
# Inference health HUD row
# -----------------------------------------------------------------------------


def test_inference_status_row_omitted_when_no_snapshots() -> None:
    from voxium.console_status import build_inference_status_row

    assert build_inference_status_row(None) is None
    assert build_inference_status_row([]) is None


def test_inference_status_row_renders_label_and_servers() -> None:
    from voxium.console_status import build_inference_status_row
    from voxium.inference_health import (
        STATE_DEGRADED,
        STATE_OK,
        InferenceHealthSnapshot,
    )

    row = build_inference_status_row(
        [
            InferenceHealthSnapshot(
                server="whisper",
                state=STATE_OK,
                last_ok_at=1.0,
                last_error_at=None,
                last_error_msg=None,
                consecutive_failures=0,
            ),
            InferenceHealthSnapshot(
                server="polish",
                state=STATE_DEGRADED,
                last_ok_at=None,
                last_error_at=2.0,
                last_error_msg="CUDA error: unknown error",
                consecutive_failures=1,
            ),
        ]
    )
    assert row is not None
    plain = row.plain
    assert "INFER" in plain
    assert "Whisper" in plain
    assert "Polish" in plain
    assert "CUDA error: unknown error" in plain


def test_inference_status_row_truncates_long_errors() -> None:
    from voxium.console_status import build_inference_status_row
    from voxium.inference_health import (
        STATE_FAILED,
        InferenceHealthSnapshot,
    )

    long_msg = "x" * 200
    row = build_inference_status_row(
        [
            InferenceHealthSnapshot(
                server="polish",
                state=STATE_FAILED,
                last_ok_at=None,
                last_error_at=1.0,
                last_error_msg=long_msg,
                consecutive_failures=3,
            )
        ]
    )
    assert row is not None
    # Truncation glyph keeps the row to ~one HUD line.
    assert "…" in row.plain
    assert len(row.plain) < 100


def test_inference_status_row_orders_known_servers_first() -> None:
    from voxium.console_status import build_inference_status_row
    from voxium.inference_health import (
        STATE_OK,
        InferenceHealthSnapshot,
    )

    row = build_inference_status_row(
        [
            InferenceHealthSnapshot(
                server="polish",
                state=STATE_OK,
                last_ok_at=1.0,
                last_error_at=None,
                last_error_msg=None,
                consecutive_failures=0,
            ),
            InferenceHealthSnapshot(
                server="whisper",
                state=STATE_OK,
                last_ok_at=1.0,
                last_error_at=None,
                last_error_msg=None,
                consecutive_failures=0,
            ),
        ]
    )
    assert row is not None
    # Whisper precedes Polish despite snapshot order.
    assert row.plain.index("Whisper") < row.plain.index("Polish")


def test_session_panel_includes_infer_row_when_snapshots_passed() -> None:
    from voxium.inference_health import (
        STATE_OK,
        InferenceHealthSnapshot,
    )

    panel = build_voxium_session_panel(
        [PttStatusStep("◉ PTT/VOX · Standing by", "Standing by.")],
        80,
        inference_snapshots=[
            InferenceHealthSnapshot(
                server="whisper",
                state=STATE_OK,
                last_ok_at=1.0,
                last_error_at=None,
                last_error_msg=None,
                consecutive_failures=0,
            )
        ],
    )
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    c.print(panel)
    out = c.export_text(clear=True)
    assert "INFER" in out
    assert "Whisper" in out
