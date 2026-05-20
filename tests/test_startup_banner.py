"""Unit tests for startup banner (pure merge + no crash)."""

from voxium.startup_banner import (
    _BANNER_TAGLINES,
    _DEFAULT_RULE_DOTS,
    _GLYPHS,
    _faceplate_width,
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


def test_faceplate_width_clamps_to_usable_columns() -> None:
    """Narrow terminals must not force a faceplate wider than the panel (Copilot PR #7)."""
    assert _faceplate_width(40) == 38
    assert _faceplate_width(None) == _DEFAULT_RULE_DOTS
    assert _faceplate_width(120) == 118


def test_get_startup_hostname_handles_oserror(monkeypatch) -> None:
    """If socket.gethostname raises, default to 'localhost'."""
    from voxium import startup_banner

    def boom() -> str:
        raise OSError("no socket")

    monkeypatch.setattr(startup_banner.socket, "gethostname", boom)
    assert startup_banner.get_startup_hostname() == "localhost"


def test_get_startup_hostname_truncates_long_name(monkeypatch) -> None:
    from voxium import startup_banner

    monkeypatch.setattr(startup_banner.socket, "gethostname", lambda: "x" * 200)
    out = startup_banner.get_startup_hostname()
    assert out.endswith("…")
    assert len(out) == 118


def test_default_rig_subtitle_truncates_long_hostname() -> None:
    """default_rig_subtitle clips hostnames longer than 56 chars."""
    out = default_rig_subtitle("y" * 80)
    assert "…" in out


def test_fit_plain_empty_and_one_column() -> None:
    """_fit_plain: width=0 → empty, width=1 → ellipsis if overflow."""
    from voxium.startup_banner import _fit_plain

    assert _fit_plain("anything", 0) == ""
    assert _fit_plain("ab", 1) == "…"
    assert _fit_plain("ab", 5) == "ab   "
    assert _fit_plain("abcdef", 4) == "abc…"


def test_build_faceplate_truncates_hostname_at_28_chars() -> None:
    """Hostname >28 chars gets clipped with an ellipsis in the faceplate."""
    from voxium.startup_banner import _build_faceplate

    plate = _build_faceplate("z" * 60, content_width=80)
    assert "…" in plate.plain
