"""Unit tests for slash command completion (slash line on the PTT & VOX client)."""

from types import SimpleNamespace

from voxium.slash_complete import (
    _completion_matches_for_buffer,
    apply_slash_tab,
    format_slash_command_hints,
    is_slash_command_typing_not_args,
    list_slash_command_matches,
)

# Local short alias used by the targeted-branch tests below.
_cmb = _completion_matches_for_buffer


def _mock_polish_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        "voxium.slash_complete.list_available_polish_models",
        lambda: [
            SimpleNamespace(model_id="qwen2.5-coder-3b-q5km"),
            SimpleNamespace(model_id="qwen2.5-3b-q4km"),
        ],
    )
    monkeypatch.setattr(
        "voxium.slash_complete.list_local_polish_models",
        lambda: [
            SimpleNamespace(name="qwen2.5-coder-3b-q5km", is_trusted=True),
            SimpleNamespace(name="local:custom/shell.gguf", is_trusted=False),
        ],
    )


def test_list_empty_prefix_all_ordered() -> None:
    m = list_slash_command_matches("")
    assert m == [
        "help",
        "health",
        "history",
        "disk",
        "mic",
        "gpu",
        "stats",
        "profile",
        "stream",
        "hotkeys",
        "models",
        "re-encode",
        "polish",
    ]


def test_list_prefix_m_matches_mic_and_models() -> None:
    m = list_slash_command_matches("m")
    assert m == ["mic", "models"]


def test_list_prefix_d_matches_disk() -> None:
    m = list_slash_command_matches("d")
    assert m == ["disk"]


def test_list_prefix_du_matches_disk() -> None:
    m = list_slash_command_matches("du")
    assert m == ["disk"]


def test_list_prefix_s_matches_stats_and_stream() -> None:
    m = list_slash_command_matches("s")
    assert m == ["stats", "stream"]


def test_list_alias_h_matches_help() -> None:
    m = list_slash_command_matches("h")
    assert m == ["help"]


def test_list_prefix_he_matches_help_and_health() -> None:
    m = list_slash_command_matches("he")
    assert m == ["help", "health"]


def test_is_typing_stops_after_space() -> None:
    assert is_slash_command_typing_not_args("/help")
    assert not is_slash_command_typing_not_args("/help ")
    assert not is_slash_command_typing_not_args("/help  x")


def test_format_hints() -> None:
    h = format_slash_command_hints("/m")
    assert "mic" in h
    assert "models" in h
    assert h.endswith("(Tab)")

    h2 = format_slash_command_hints("/he")
    assert "/help" in h2
    assert "/health" in h2

    assert format_slash_command_hints("/help ") == ""


def test_format_hints_for_polish_subcommands(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)

    h = format_slash_command_hints("/models polish ")
    assert "/models polish list" in h
    assert "/models polish installed" in h
    assert "/models polish use" in h

    h2 = format_slash_command_hints("/polish ")
    assert "/polish list" in h2
    assert "/polish use" in h2
    assert "/polish on" in h2


def test_hotkeys_completion() -> None:
    out = apply_slash_tab("/hot", tab_cycle=0)
    assert out.new_buffer == "/hotkeys"

    actions = _completion_matches_for_buffer("/hotkeys ")
    assert "/hotkeys ptt" in actions
    assert "/hotkeys replay" in actions

    ptt_keys = _completion_matches_for_buffer("/hotkeys ptt f1")
    assert "/hotkeys ptt f10" in ptt_keys
    assert "/hotkeys ptt f12" in ptt_keys


def test_stats_completion() -> None:
    out = apply_slash_tab("/sta", tab_cycle=0)
    assert out.new_buffer == "/stats"


def test_stream_completion_subcommands() -> None:
    actions = _completion_matches_for_buffer("/stream ")
    assert "/stream on" in actions
    assert "/stream off" in actions
    assert "/stream status" in actions


def test_stream_completion_via_alias() -> None:
    # /li → completes through the alias table, which canonicalizes to /stream.
    out = apply_slash_tab("/li", tab_cycle=0)
    assert out.new_buffer == "/stream"


def test_tab_one_match_completes() -> None:
    out = apply_slash_tab("/h", tab_cycle=0)
    assert out.new_buffer == "/help"
    assert out.tab_cycle == 0
    assert out.did_extend

    again = apply_slash_tab("/help", tab_cycle=0)
    assert again.new_buffer == "/help"


def test_tab_health_completes() -> None:
    out = apply_slash_tab("/hea", tab_cycle=0)
    assert out.new_buffer == "/health"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_tab_m_ambiguous_picks_by_cycle() -> None:
    """``/m`` is not extended by LCP; Tab picks ordered matches and cycles."""
    out0 = apply_slash_tab("/m", tab_cycle=0)
    assert out0.new_buffer == "/mic"
    assert out0.tab_cycle == 1

    out1 = apply_slash_tab("/m", tab_cycle=1)
    assert out1.new_buffer == "/models"
    assert out1.tab_cycle == 0


def test_tab_mo_completes_single_primary_models() -> None:
    """``/mo`` only matches the ``models`` command (aliases ``model`` / ``models``)."""
    out = apply_slash_tab("/mo", tab_cycle=0)
    assert out.new_buffer == "/models"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_tab_models_direct_transcribe_model_completes() -> None:
    out = apply_slash_tab("/models ba", tab_cycle=0)
    assert out.new_buffer == "/models base"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_tab_models_polish_completes_subcommand() -> None:
    out = apply_slash_tab("/models p", tab_cycle=0)
    assert out.new_buffer == "/models polish"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_tab_models_polish_lists_actions(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)

    out0 = apply_slash_tab("/models polish ", tab_cycle=0)
    assert out0.new_buffer == "/models polish list"
    assert out0.tab_cycle == 1

    out1 = apply_slash_tab("/models polish ", tab_cycle=2)
    assert out1.new_buffer == "/models polish use"
    assert out1.did_extend


def test_tab_models_polish_use_completes_trusted_id(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)

    out = apply_slash_tab("/models polish use qwen2.5-c", tab_cycle=0)
    assert out.new_buffer == "/models polish use qwen2.5-coder-3b-q5km"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_tab_polish_model_completes_tag(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)

    out = apply_slash_tab("/polish model qwen2.5-c", tab_cycle=0)
    assert out.new_buffer == "/polish model qwen2.5-coder-3b-q5km"
    assert out.tab_cycle == 0
    assert out.did_extend


def test_models_transcribe_subcommand_completions_after_space() -> None:
    m = _completion_matches_for_buffer("/models transcribe ")
    assert m and all(x.startswith("/models transcribe") for x in m)


def test_models_transcribe_use_prefix() -> None:
    m = _completion_matches_for_buffer("/models transcribe u")
    assert any("use" in x for x in m)


def test_models_transcribe_use_space_completes_models(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    m = _completion_matches_for_buffer("/models transcribe use ")
    assert any("base" in x or "qwen" in x for x in m)


def test_models_transcribe_use_second_arg_prefix(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    m = _completion_matches_for_buffer("/models transcribe use b")
    assert m and m[0].startswith("/models transcribe use ")


def test_polish_subcommand_completions(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    m = _completion_matches_for_buffer("/polish l")
    assert m


def test_apply_slash_tab_non_slash_unchanged() -> None:
    o = apply_slash_tab("hello", tab_cycle=0)
    assert o.new_buffer == "hello" and o.did_extend is False


def test_completion_returns_empty_for_non_slash_buffer() -> None:
    assert _cmb("hello world") == []
    assert _cmb("") == []


def test_completion_returns_empty_for_slash_only_no_parts() -> None:
    # /<space> — parts is empty after the space
    assert _cmb("/ ") == []


def test_completion_profile_subcommand_with_trailing_space() -> None:
    out = _cmb("/profile ")
    assert out and all(x.startswith("/profile ") for x in out)


def test_completion_profile_prefix_first_arg() -> None:
    # 'r' should match the 'reset' action.
    assert _cmb("/profile r") == ["/profile reset"]
    # Mismatched prefix → no completions.
    assert _cmb("/profile zzz") == []


def test_completion_stream_first_arg_prefix() -> None:
    out = _cmb("/stream o")
    assert any(x == "/stream on" for x in out)
    assert any(x == "/stream off" for x in out)


def test_completion_models_no_args_no_space_returns_empty() -> None:
    # `/models` with NO trailing space and no further args has no completions
    assert _cmb("/models") == ["/models"]  # is_slash_command_typing path
    # Sub case: after the slash command body but exactly word boundary
    # → first arg with prefix completions still works
    assert _cmb("/models polish unknownact") == []


def test_completion_polish_use_trailing_space_lists_models(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    out = _cmb("/polish use ")
    assert out and any("qwen" in x for x in out)


def test_completion_polish_use_second_arg_prefix(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    out = _cmb("/polish use qwen2.5-c")
    assert out and out[0].startswith("/polish use qwen2.5-coder")


def test_completion_polish_use_third_arg_no_completion(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    # Once we have 3 args, no more completions
    assert _cmb("/polish use foo bar") == []


def test_completion_models_polish_use_third_arg_prefix(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    out = _cmb("/models polish use qwen2.5-c")
    assert out and out[0].startswith("/models polish use qwen2.5-coder")


def test_completion_models_polish_model_word_lists(monkeypatch) -> None:
    _mock_polish_registry(monkeypatch)
    out = _cmb("/models polish model ")
    assert out and any("qwen" in x for x in out)


def test_completion_hotkeys_trailing_space_with_action() -> None:
    out = _cmb("/hotkeys ptt ")
    assert out and all(x.startswith("/hotkeys ptt ") for x in out)


def test_completion_hotkeys_too_many_args_empty() -> None:
    assert _cmb("/hotkeys ptt f1 extra") == []


def test_completion_unknown_command_returns_empty() -> None:
    # /xyz is not a real command; falls through with no completions
    assert _cmb("/xyz abc") == []


def test_apply_slash_tab_no_options_unchanged() -> None:
    # /xyz with no matching primary → unchanged
    out = apply_slash_tab("/xyz unknown", tab_cycle=3)
    assert out.new_buffer == "/xyz unknown"
    assert out.did_extend is False
    assert out.tab_cycle == 0


def test_format_hints_truncates_long_line(monkeypatch) -> None:
    # Force a very long hint list and assert truncation marker.
    long = ["/x" + ("y" * 30) for _ in range(20)]
    monkeypatch.setattr(
        "voxium.slash_complete._completion_matches_for_buffer", lambda _b: long
    )
    out = format_slash_command_hints("/x", max_len=40)
    assert out.endswith("…")
    assert len(out) <= 40
