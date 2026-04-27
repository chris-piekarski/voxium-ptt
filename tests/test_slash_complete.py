"""Unit tests for slash command completion (slash line on the PTT & VOX client)."""

from voxium.slash_complete import (
    apply_slash_tab,
    format_slash_command_hints,
    is_slash_command_typing_not_args,
    list_slash_command_matches,
)


def test_list_empty_prefix_all_ordered() -> None:
    m = list_slash_command_matches("")
    assert m == ["help", "history", "disk", "mic", "gpu", "models"]


def test_list_prefix_m_matches_mic_and_models() -> None:
    m = list_slash_command_matches("m")
    assert m == ["mic", "models"]


def test_list_prefix_d_matches_disk() -> None:
    m = list_slash_command_matches("d")
    assert m == ["disk"]


def test_list_prefix_du_matches_disk() -> None:
    m = list_slash_command_matches("du")
    assert m == ["disk"]


def test_list_alias_h_matches_help() -> None:
    m = list_slash_command_matches("h")
    assert m == ["help"]


def test_is_typing_stops_after_space() -> None:
    assert is_slash_command_typing_not_args("/help")
    assert not is_slash_command_typing_not_args("/help ")
    assert not is_slash_command_typing_not_args("/help  x")


def test_format_hints() -> None:
    h = format_slash_command_hints("/m")
    assert "mic" in h
    assert "models" in h
    assert h.endswith("(Tab)")

    assert format_slash_command_hints("/help ") == ""


def test_tab_one_match_completes() -> None:
    out = apply_slash_tab("/h", tab_cycle=0)
    assert out.new_buffer == "/help"
    assert out.tab_cycle == 0
    assert out.did_extend

    again = apply_slash_tab("/help", tab_cycle=0)
    assert again.new_buffer == "/help"


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
