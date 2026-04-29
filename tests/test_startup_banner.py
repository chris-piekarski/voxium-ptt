"""Unit tests for startup banner (pure merge + no crash)."""

from voxium.startup_banner import (
    _BANNER_TAGLINES,
    _GLYPHS,
    _merge_word,
    build_voxium_banner,
    default_rig_subtitle,
    show_startup_banner,
)
from rich.console import Console


def test_merge_word_skips_unknown_characters() -> None:
    """Characters not in ``_GLYPHS`` are skipped; no layers → empty result."""
    assert _merge_word("123") == []


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
    g = build_voxium_banner(tagline="Custom tagline for test.")
    assert g is not None
    c = Console(force_terminal=True, width=100, record=True, color_system="truecolor")
    show_startup_banner(c, tagline="Custom tagline for test.")
    s = c.export_text(clear=True)
    assert "Voxium" in s
    assert "█" in s
    assert "Custom tagline" in s


def test_banner_tagline_pool_nonempty() -> None:
    assert len(_BANNER_TAGLINES) >= 8
    for t in _BANNER_TAGLINES:
        assert t.strip()


def test_default_rig_subtitle_hostname_and_rig() -> None:
    s = default_rig_subtitle("shack-01")
    assert "Rig" in s and "shack-01" in s and "1960" in s and "PTT" in s
