"""Unit tests for startup banner (pure merge + no crash)."""
from voxium.startup_banner import _GLYPHS, _merge_word, build_voxium_banner, show_startup_banner
from rich.console import Console


def test_merge_voxium_five_rows() -> None:
    lines = _merge_word("VOXIUM")
    assert len(lines) == 5
    w = len(lines[0])
    for ln in lines:
        assert len(ln) == w
    assert "█" in lines[0]


def test_glyphs_define_voxium_letters() -> None:
    for c in "VOXIUM":
        assert c in _GLYPHS
        assert len(_GLYPHS[c]) == 5


def test_build_group_and_print_smoke() -> None:
    g = build_voxium_banner()
    assert g is not None
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    show_startup_banner(c)
    s = c.export_text(clear=True)
    assert "Voxium" in s
    assert "█" in s
