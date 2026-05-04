"""UX chatter on the shared polish/chatter lane — pure helpers and fallbacks."""

from __future__ import annotations

from typing import Any

import pytest

from voxium.llama_cpp_client import llama_cpp_chat_completions
from voxium.standby_telemetry import build_standby_detail_line
from voxium.llama_cpp_client import LlamaCppChatResult
from voxium.ux_chatter import (
    UxChatterLineResult,
    _UX_ECHO_FALLBACK_LOG_SUBS,
    _UX_EDGE_ECHO_FALLBACK,
    _normalize_wit,
    _pick_ux_deterministic,
    clear_ux_chatter_wit,
    fetch_ux_edge_status_detail,
    fetch_ux_log_subtitle,
    fetch_ux_rig_subtitle,
    fetch_ux_shutdown_line,
    fetch_ux_startup_tagline,
    format_ux_chatter_downlink_line,
    is_ux_chatter_wanted,
    request_ux_chatter_edge_line_full,
    request_ux_chatter_line,
    request_ux_chatter_line_full,
    sync_ux_chatter_for_transcript,
    ux_chatter_runtime_from_config,
    ux_output_likely_echoes_seed,
    ux_output_likely_parrots_any_ux_prompt,
    ux_output_too_generic_for_edge_inference,
)
from voxium.polish_model_registry import DEFAULT_TRUSTED_POLISH_MODEL_ID
from voxium.ux_chatter_prompt import (
    _transcript_vibe_cues,
    system_message_ux_chatter_copy,
    system_message_ux_chatter_standby,
    system_message_ux_edge_inference,
    user_message_ux_chatter,
)


def test_ux_chatter_runtime_defaults() -> None:
    rt = ux_chatter_runtime_from_config({})
    assert rt.base_url == "http://127.0.0.1:11435"
    assert rt.model == DEFAULT_TRUSTED_POLISH_MODEL_ID


def test_ux_chatter_runtime_uses_resolved_model_id() -> None:
    from voxium.ux_chatter import (
        clear_resolved_ux_chatter_model_id,
        set_resolved_ux_chatter_model_id,
    )

    set_resolved_ux_chatter_model_id("tinyllama-1.1b-chat-v1.0.Q4_K_M")
    try:
        rt = ux_chatter_runtime_from_config({})
        assert rt.model == "tinyllama-1.1b-chat-v1.0.Q4_K_M"
    finally:
        clear_resolved_ux_chatter_model_id()


def test_normalize_wit() -> None:
    assert _normalize_wit("  a\nb  ", max_chars=10) == "a b"
    assert (
        _normalize_wit("Okay. **Squelch** on `loopback`", max_chars=80)
        == "Okay. Squelch on loopback"
    )
    long = "x" * 100
    out = _normalize_wit(long, max_chars=8)
    assert len(out) <= 8
    assert out.endswith("…")


def test_ux_output_likely_echoes_seed() -> None:
    assert ux_output_likely_echoes_seed(output="  hello world  ", seed="hello world")
    assert not ux_output_likely_echoes_seed(
        output="10-4, roger the local stack", seed="testing one two three"
    )


def test_user_message_ux_chatter_tails() -> None:
    u = user_message_ux_chatter("a" * 500)
    assert "…" in u or len(u) < 600


def test_transcript_vibe_cues_capture_intent_and_tone() -> None:
    cues = _transcript_vibe_cues(
        "Can you fix the broken build right now? We need to ship this release."
    )
    low = cues.lower()
    assert "inquiring" in low
    assert "urgent" in low
    assert "technical" in low or "workaday" in low


def test_user_message_ux_chatter_includes_vibe_cues() -> None:
    u = user_message_ux_chatter("Can you fix the broken build right now?")
    assert "Vibe cues:" in u


def test_ux_chatter_prompts_keep_brand_and_tone() -> None:
    copy = system_message_ux_chatter_copy().lower()
    standby = system_message_ux_chatter_standby().lower()
    edge = system_message_ux_edge_inference().lower()
    assert "**" not in copy and "`" not in copy
    assert "**" not in standby and "`" not in standby
    assert "**" not in edge and "`" not in edge
    assert "ptt" in copy and "vox" in copy
    assert "ptt** & **vox" in copy or "ptt & vox" in copy
    assert "sharp" in copy and "witty" in copy
    assert "pundit" in copy
    assert "10-4" in copy or "10 4" in copy
    assert "qrv" in standby or "qrx" in standby
    assert "over and out" in copy
    assert "do not make it a second 10-4/copy acknowledgment" in standby
    assert "type-out pending" in edge or "decode in flight" in edge


def test_fetch_ux_startup_tagline_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="  Local stack on deck, PTT when ready, copy.  ",
            error=None,
            seconds=0.05,
            prompt_tokens=10,
            completion_tokens=12,
            total_tokens=22,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    out = fetch_ux_startup_tagline({}, cli_enabled=True)
    assert out and "PTT" in out and "copy" in out.lower()


def test_fetch_ux_startup_tagline_unwanted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOXIUM_UX_CHATTER", "0")
    assert fetch_ux_startup_tagline({}, cli_enabled=True) is None


def test_fetch_ux_rig_subtitle_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Rig on station  ·  base unit · loopback  ·  10-4 good buddy, copy",
            error=None,
            seconds=0.04,
            prompt_tokens=20,
            completion_tokens=20,
            total_tokens=40,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    out = fetch_ux_rig_subtitle({}, "DESKTOP-VOX1", cli_enabled=True)
    assert out and "rig" in out.lower() and "DESKTOP-VOX1" in out


def test_fetch_ux_log_subtitle_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Local pass on the wire — home stack, copy.",
            error=None,
            seconds=0.04,
            prompt_tokens=20,
            completion_tokens=18,
            total_tokens=38,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    out = fetch_ux_log_subtitle({}, "hello from the desk", cli_enabled=True)
    assert out and "copy" in out.lower()


def test_ux_output_likely_parrots_log_subtitle_user_prompt() -> None:
    from voxium.ux_chatter import ux_output_likely_parrots_log_subtitle_user_prompt

    assert ux_output_likely_parrots_log_subtitle_user_prompt(
        output="Ding dong One new dim footer: **box** + PTT/VOX + CB/HAM spice, copy."
    )
    assert not ux_output_likely_parrots_log_subtitle_user_prompt(
        output="Loud in the passband — PTT in the can, stack on the bus, 10-4, copy."
    )


def test_ux_output_likely_parrots_any_ux_prompt() -> None:
    assert ux_output_likely_parrots_any_ux_prompt(
        output="Topic seed from STT: your line: one readback quip, not a restate of their words."
    )
    assert ux_output_likely_parrots_any_ux_prompt(
        output="Sign off for this session: prefix Voxium: going clear, 73, copy."
    )
    assert not ux_output_likely_parrots_any_ux_prompt(
        output="Roger, local loopback stack is hot and the shack is standing by."
    )


def test_fetch_ux_log_subtitle_falls_back_on_instruction_parrot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def parrot_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Ding dong—One new dim footer: box + PTT/VOX + CB/HAM spice, copy, 10-4, breaker, stack.",
            error=None,
            seconds=0.04,
            prompt_tokens=20,
            completion_tokens=40,
            total_tokens=60,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", parrot_comp)
    out = fetch_ux_log_subtitle({}, "hello from the desk", cli_enabled=True)
    assert out
    assert out in _UX_ECHO_FALLBACK_LOG_SUBS
    assert "dim footer" not in out.lower()


def test_fetch_ux_log_subtitle_empty_transcript() -> None:
    assert fetch_ux_log_subtitle({}, "  ", cli_enabled=True) is None


def test_fetch_ux_shutdown_line_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Voxium: 73, local stack — going clear, copy.",
            error=None,
            seconds=0.03,
            prompt_tokens=12,
            completion_tokens=14,
            total_tokens=26,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    out = fetch_ux_shutdown_line({}, cli_enabled=True)
    assert out and out.startswith("Voxium:")


def test_fetch_ux_shutdown_line_prepends_voxium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Signing off the loopback — clear.",
            error=None,
            seconds=0.02,
            prompt_tokens=5,
            completion_tokens=8,
            total_tokens=13,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    out = fetch_ux_shutdown_line({}, cli_enabled=True)
    assert out and out.startswith("Voxium:")


def test_fetch_ux_shutdown_line_drops_prompt_scaffolding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Sign off for this session: prefix Voxium: going clear, 73, copy.",
            error=None,
            seconds=0.02,
            prompt_tokens=5,
            completion_tokens=8,
            total_tokens=13,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    assert fetch_ux_shutdown_line({}, cli_enabled=True) is None


def test_fetch_ux_shutdown_line_drops_ctrl_c_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Voxium: (Ctrl+C)",
            error=None,
            seconds=0.02,
            prompt_tokens=5,
            completion_tokens=8,
            total_tokens=13,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    assert fetch_ux_shutdown_line({}, cli_enabled=True) is None


def test_is_ux_chatter_wanted_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOXIUM_UX_CHATTER", "0")
    assert is_ux_chatter_wanted(cli_enabled=True, file_config={}) is False
    monkeypatch.delenv("VOXIUM_UX_CHATTER", raising=False)
    assert is_ux_chatter_wanted(cli_enabled=True, file_config={}) is True
    assert is_ux_chatter_wanted(cli_enabled=False, file_config={}) is False


def test_ux_output_likely_parrots_edge_inference_user_prompt() -> None:
    from voxium.ux_chatter import ux_output_likely_parrots_edge_inference_user_prompt

    assert ux_output_likely_parrots_edge_inference_user_prompt(
        output="PTT – EDGE INFERENCE: Copy. ; more junk"
    )
    assert not ux_output_likely_parrots_edge_inference_user_prompt(
        output="Local stack on loopback — chewing the clip, 10-4, copy."
    )


def test_ux_output_too_generic_for_edge_inference() -> None:
    assert ux_output_too_generic_for_edge_inference(output="okay")
    assert ux_output_too_generic_for_edge_inference(output="sure")
    assert not ux_output_too_generic_for_edge_inference(
        output="Local stack on loopback — chewing the clip, 10-4, copy."
    )


def test_request_ux_chatter_edge_line_rejects_instruction_mash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def bad_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="PTT – EDGE INFERENCE: Copy.",
            error=None,
            seconds=0.02,
            prompt_tokens=20,
            completion_tokens=6,
            total_tokens=26,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", bad_comp)
    rt = ux_chatter_runtime_from_config({})
    full = request_ux_chatter_edge_line_full(rt, rexmit=False)
    assert full.wit
    assert "edge inference" not in (full.wit or "").lower()
    assert "PTT – EDGE" not in (full.wit or "")
    seed = f"edge|0|{'PTT – EDGE INFERENCE: Copy.'!s}"[:200]
    exp = _normalize_wit(
        _pick_ux_deterministic(_UX_EDGE_ECHO_FALLBACK, seed),
        max_chars=int(rt.wit_max_chars),
    )
    assert (full.wit or "") == exp


def test_request_ux_chatter_edge_line_rejects_generic_okay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def bland_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="okay",
            error=None,
            seconds=0.02,
            prompt_tokens=8,
            completion_tokens=1,
            total_tokens=9,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", bland_comp)
    rt = ux_chatter_runtime_from_config({})
    full = request_ux_chatter_edge_line_full(rt, rexmit=False)
    assert (full.wit or "").strip()
    assert (full.wit or "").strip().lower() != "okay"
    seed = f"edge|0|{'okay'!s}"[:200]
    exp = _normalize_wit(
        _pick_ux_deterministic(_UX_EDGE_ECHO_FALLBACK, seed),
        max_chars=int(rt.wit_max_chars),
    )
    assert (full.wit or "") == exp


def test_request_ux_chatter_edge_line_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="  Stack’s chewing the clip on loopback — 10-4, copy.  ",
            error=None,
            seconds=0.02,
            prompt_tokens=40,
            completion_tokens=14,
            total_tokens=54,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    rt = ux_chatter_runtime_from_config({})
    full = request_ux_chatter_edge_line_full(rt, rexmit=False)
    assert full.result is not None and full.result.ok
    assert "loopback" in (full.wit or "").lower()
    r2 = request_ux_chatter_edge_line_full(rt, rexmit=True)
    assert (r2.wit or "").strip()
    d = fetch_ux_edge_status_detail({}, cli_enabled=True, rexmit=False)
    assert d and "loopback" in d.lower()


def test_request_ux_chatter_edge_line_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (False, None),
    )
    rt = ux_chatter_runtime_from_config({})
    full = request_ux_chatter_edge_line_full(rt, rexmit=False)
    assert full.skip == "unreachable" and full.result is None
    assert fetch_ux_edge_status_detail({}, cli_enabled=True) is None


def test_request_ux_chatter_line_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_reachable(_b: str, timeout: float = 1.0) -> tuple[bool, str | None]:
        return False, "nope"

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_reachable", fake_reachable)
    rt = ux_chatter_runtime_from_config({})
    assert request_ux_chatter_line(rt, "hello stack") == ""
    full = request_ux_chatter_line_full(rt, "hello stack")
    assert full.skip == "unreachable" and full.result is None


def test_format_ux_chatter_downlink_line() -> None:
    rt = ux_chatter_runtime_from_config({})
    assert (
        format_ux_chatter_downlink_line(
            rt,
            UxChatterLineResult("", None, "empty_transcript"),
        )
        is None
    )
    w, lev = format_ux_chatter_downlink_line(
        rt,
        UxChatterLineResult("", None, "unreachable"),
    )
    assert "not on station" in w and lev == "warning"
    w, lev = format_ux_chatter_downlink_line(
        rt,
        UxChatterLineResult(
            "hi",
            LlamaCppChatResult(
                ok=True,
                text="hi",
                error=None,
                seconds=0.14,
                prompt_tokens=175,
                completion_tokens=11,
                total_tokens=186,
                raw_status=200,
            ),
            None,
        ),
    )
    assert "line ready" in w and "0.14" in w and "175→11" in w and lev == "info"
    w, lev = format_ux_chatter_downlink_line(
        rt,
        UxChatterLineResult(
            "",
            LlamaCppChatResult(
                ok=True,
                text="",
                error=None,
                seconds=0.1,
                prompt_tokens=1,
                completion_tokens=0,
                total_tokens=1,
                raw_status=200,
            ),
            None,
        ),
    )
    assert "empty line" in w and lev == "warning"


def test_request_ux_chatter_line_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="  Copy, short line  ",
            error=None,
            seconds=0.01,
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    rt = ux_chatter_runtime_from_config({})
    out = request_ux_chatter_line(rt, "testing one two")
    assert "Copy" in out
    assert "\n" not in out
    full = request_ux_chatter_line_full(rt, "testing one two")
    assert full.wit == out
    assert full.result is not None and full.result.ok


def test_request_ux_chatter_line_rejects_stt_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that returns the same text as the STT line must not ship as wit."""
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_echo(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="hello from the microphone test",
            error=None,
            seconds=0.01,
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_echo)
    rt = ux_chatter_runtime_from_config({})
    t = "hello from the microphone test"
    w = request_ux_chatter_line(rt, t)
    assert w != t
    assert "hello from the microphone test" not in w


def test_request_ux_chatter_line_rejects_prompt_parrot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_prompt(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Topic seed from STT: your line: one readback quip, not a restate of their words.",
            error=None,
            seconds=0.01,
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_prompt)
    rt = ux_chatter_runtime_from_config({})
    w = request_ux_chatter_line(rt, "testing one two")
    assert w
    assert "topic seed" not in w.lower()
    assert "readback quip" not in w.lower()


def test_fetch_ux_startup_tagline_drops_prompt_scaffolding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        return LlamaCppChatResult(
            ok=True,
            text="Write one fresh tagline for this run with PTT, loopback, stack, and copy.",
            error=None,
            seconds=0.05,
            prompt_tokens=10,
            completion_tokens=12,
            total_tokens=22,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    assert fetch_ux_startup_tagline({}, cli_enabled=True) is None


def test_sync_ux_chatter_for_transcript_fills_wit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_ux_chatter_wit()
    monkeypatch.setattr(
        "voxium.ux_chatter.llama_cpp_reachable",
        lambda _b, timeout=1.0: (True, None),
    )
    _calls: list[str] = []

    def fake_comp(*_a: Any, **_k: Any) -> Any:
        i = len(_calls)
        _calls.append("copy" if i == 0 else "standby")
        text = (
            "  Local wit for copy line  "
            if i == 0
            else "  Shack is quiet, stack is cooling, standing by  "
        )
        return LlamaCppChatResult(
            ok=True,
            text=text,
            error=None,
            seconds=0.01,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            raw_status=200,
        )

    monkeypatch.setattr("voxium.ux_chatter.llama_cpp_chat_completions", fake_comp)
    r = sync_ux_chatter_for_transcript("hello", {}, cli_enabled=True, on_complete=None)
    assert r is not None and (r.wit or "").strip() == "Local wit for copy line"
    assert _calls == ["copy", "standby"]
    from voxium.ux_chatter import get_ux_chatter_wit

    assert "standing by" in get_ux_chatter_wit().lower()
    assert "copy line" not in get_ux_chatter_wit()


def test_standby_detail_puts_ux_wit_at_far_left() -> None:
    from voxium.standby_fft import reset_spectrum_state

    reset_spectrum_state()
    t = build_standby_detail_line(
        0,
        {
            "ux_chatter_wit": "roger, local loop",
        },
    )
    pl = t.plain.strip().lower()
    assert pl.startswith("roger,")
    assert "standing by" not in pl[:20]


def test_llama_cpp_chat_completions_parses_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class R:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "pong"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }

    def fake_post(*_a: Any, **_k: Any) -> R:
        return R()

    monkeypatch.setattr("voxium.llama_cpp_client.requests.post", fake_post)
    out = llama_cpp_chat_completions(
        "http://127.0.0.1:1/",
        "m1",
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        timeout=1.0,
        max_tokens=8,
    )
    assert out.ok and out.text == "pong"
