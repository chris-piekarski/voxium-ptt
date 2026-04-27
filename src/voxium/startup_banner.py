"""RGB block wordmark for the client: PTT & VOX, rig / shack (radio box) + local inference stack. Brand: docs/brand.md."""
from __future__ import annotations

import colorsys
import random
from collections.abc import Iterator

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from voxium.console_status import voxium_panel_width

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
# Decorative rule when width is not passed (e.g. tests) — with content_width, rules scale to the panel.
_DEFAULT_RULE_DOTS = 64

# One line per startup; rotate for flavor. PTT & VOX, shack, light 10-codes, edge = local STT (see docs/brand.md).
_BANNER_TAGLINES: tuple[str, ...] = (
    "Local robot on loopback — chewing through this transmission.",
    "Edge stack at the shack — VOX in one side, text out the other, copy.",
    "PTT down, inference up — the silicon crew’s on it. 10-4.",
    "Speech to text on the home wire — rig at the desk, robot on the headroom, no skip.",
    "Breaker one-nine for the VOX — stack on automatic, readback’s on your screen.",
    "You hold the key; the stack’s on deck — STT in the loop, standing by.",
    "Chewing the carrier into words — edge inference, shack hot, all local on the bus.",
    "VOX in the passband, text in the buffer — copy like CB, work like a machine.",
    "10-4 on the transcript — you’re QRV on PTT, the model’s running the traffic.",
    "No repeater, no phone patch — just loopback, VOX, and the words at your fist.",
    "QRM? Not here. You, the mic, and a robot that never double-keys the PTT.",
    "Rubber-duck the VOX, clear the VOX check — the stack’s already on your audio, over.",
    "Rattle the VOX, grab the take — from squelch to script on this edge box.",
    "What’s your 20? The loopback — VOX in, QSO with your own stack, 10-2 on copy.",
    "Good buddy to your keyboard — local STT, breaker open when you need the words.",
    "Monitor: PTT in, type out — the shack’s got a robot op chewing your transmission.",
    "10-1 was bad; this pass is 10-2 — VOX to text, first flight on your own rig, copy.",
)


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


def _rule_text(content_width: int | None) -> Text:
    n = _DEFAULT_RULE_DOTS if content_width is None else max(0, content_width - 2)
    return Text("  " + "·" * n, style="dim #475569")


def build_voxium_banner(*, tagline: str | None = None, content_width: int | None = None) -> Group:
    # Radio box: PTT & VOX, rig, shack; robotics: inference stack (see docs/brand.md).
    line = tagline if tagline is not None else random.choice(_BANNER_TAGLINES)
    top = Text("  PTT & VOX box — VOX in, text out · shack, no uplink", style="dim #64748b")
    rule1 = _rule_text(content_width)
    parts: list[Text | str] = [top, "\n", rule1, "\n", _build_voxium_block()]
    parts.append("\n")
    parts.append(_rule_text(content_width))
    parts.append(Text("\n  " + escape(line), style="dim #94a3b8"))
    return Group(*parts)


def show_startup_banner(console: Console) -> None:
    w = voxium_panel_width(console)
    # Border 2 + horizontal padding 1+1 — same inner width as transcribe/PTT panels (app.py).
    inner_w = max(4, w - 4)
    panel = Panel(
        build_voxium_banner(content_width=inner_w),
        title=Text("Voxium", style="bold rgb(34,211,238)"),
        title_align="left",
        subtitle=Text(
            "Rig on station  ·  PTT & VOX  ·  loopback  ·  you key, stack in the loop",
            style="italic dim #94a3b8",
        ),
        subtitle_align="left",
        border_style="rgb(6,182,212)",
        padding=(0, 1),
        width=w,
    )
    console.print()
    console.print(panel)
    console.print()
