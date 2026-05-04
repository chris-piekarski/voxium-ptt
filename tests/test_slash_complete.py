"""Unit tests for slash command completion (slash line on the PTT & VOX client)."""

from types import SimpleNamespace

from voxium.slash_complete import (
    _completion_matches_for_buffer,
    apply_slash_tab,
    format_slash_command_hints,
    is_slash_command_typing_not_args,
    list_slash_command_matches,
)


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


def test_list_prefix_s_matches_stats() -> None:
    m = list_slash_command_matches("s")
    assert m == ["stats"]


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
