"""PTT / Apollo-style RGB startup art for the client. Brand: docs/brand.md."""
from __future__ import annotations

import colorsys
from collections.abc import Iterator

from rich.console import Console, Group
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

# 5 rows × 5 cols, █ = ink. One column gap in merge.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "V": (
        "█   █",
        "█   █",
        "█   █",
        " █ █ ",
        "  █  ",
    ),
    "O": (
        " ███ ",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ),
    "X": (
        "█   █",
        "█   █",
        " ███ ",
        "█   █",
        "█   █",
    ),
    "I": (
        " ███ ",
        "  █  ",
        "  █  ",
        "  █  ",
        " ███ ",
    ),
    "U": (
        "█   █",
        "█   █",
        "█   █",
        "█   █",
        " ███ ",
    ),
    "M": (
        "█   █",
        "██ ██",
        "█ █ █",
        "█   █",
        "█   █",
    ),
}
_GAP = 1
_H0, _H1 = 0.5, 0.78
_WORD = "VOXIUM"


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _merge_word(word: str) -> list[str]:
    layers: list[tuple[str, ...]] = []
    for c in word.upper():
        g = _GLYPHS.get(c)
        if g is None:
            continue
        layers.append(g)
    if not layers:
        return []
    h = len(layers[0])
    gap = " " * _GAP
    return [gap.join(L[row] for L in layers) for row in range(h)]


def _ink_positions(line: str) -> Iterator[tuple[int, int]]:
    k = 0
    for i, c in enumerate(line):
        if c == "█":
            yield (i, k)
            k += 1


def _gradient_line(line: str, h0: float, h1: float) -> Text:
    t = Text()
    ink = list(_ink_positions(line))
    total = max(0, len(ink) - 1)
    pos_to_frac = {i: (idx / max(1, total)) for (i, idx) in ink}
    for j, c in enumerate(line):
        if c == "█" and j in pos_to_frac:
            hue = h0 + (h1 - h0) * pos_to_frac[j]
            r, g, b_ = _hsv(hue, 0.72, 0.98)
            t.append(c, style=Style(color=f"rgb({r},{g},{b_})", bold=True))
        else:
            t.append(c)  # spaces / frame
    return t


def _build_voxium_block() -> Text:
    """One Text object so the block doesn't pick up double line spacing in Group."""
    out = Text("    ")
    first = True
    for row in _merge_word(_WORD):
        if not first:
            out.append("\n    ")
        first = False
        out += _gradient_line(row, _H0, _H1)
    return out


def build_voxium_banner() -> Group:
    top = Text("  PTT VOX Agent CLI", style="dim #64748b")
    rule1 = Text("  " + "·" * 64, style="dim #475569")
    parts: list[Text | str] = [top, "\n", rule1, "\n", _build_voxium_block()]
    parts.append("\n")
    parts.append(Text("  " + "·" * 64, style="dim #475569"))
    parts.append(
        Text.from_markup(
            "\n  [dim]Local Voice Agent - No Uplink[/dim]"
        )
    )
    return Group(*parts)


def show_startup_banner(console: Console) -> None:
    cw = getattr(console, "width", None) or 88
    w = min(int(cw), 92)
    panel = Panel(
        build_voxium_banner(),
        title=Text("Voxium", style="bold rgb(34,211,238)"),
        subtitle=Text("On station  ·  PTT / vox  ·  loopback only", style="italic dim #94a3b8"),
        border_style="rgb(6,182,212)",
        padding=(0, 1),
        width=max(60, w),
    )
    console.print()
    console.print(panel)
    console.print()
