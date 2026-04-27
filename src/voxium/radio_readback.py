"""
Rotating HAM/CB-tinged readback lines for successful vox (brand: docs/brand.md).
Pure + thread-safe: safe when transcribe runs on a worker thread.
"""
from __future__ import annotations

import threading

_READBACKS: tuple[str, ...] = (
    "10-4, good buddy — copy loud and clear.",
    "Roger; good read on that transmission.",
    "Solid copy — you're 5 by 5 on this end.",
    "QSL: text on the ground, vox in the clear.",
    "That's a copy — no birdies on the line.",
    "Heard you clean — pasting the downlink.",
    "Roger dodger, good audio on the loopback.",
    "Wall-to-wall copy; green board on this end.",
    "You’re on frequency; I got all of it.",
    "No QRM in that take — roger, pasting.",
    "Copy that, standing by.",
    "Good S-units in the vox; text on the way.",
    "Loud in the can — 10-4, pasting the words.",
    "No DX here—just a clean loopback. Roger, pasting.",
    "Clear and readable; over and into the buffer.",
    "Squelch is quiet; good vox, good read.",
    "10-4, good buddy — rubber-ducking it straight to the clip.",
    "In the log — good run on the local wire.",
    "PTT timing’s clean; I’ve got the lot of it.",
    "Armchair copy from here — text is on the glass.",
    "That’s the round-trip — 59 where it counts: your desk.",
    "10-4 — good read on the local wire, pasting now.",
    "Hunt-and-peck? Nah — hunt-and-*paste*. Roger.",
    "The rig heard you: ground loop, good signal, going to log.",
    "73 for this pass — we’ll do it again on the next PTT. Pasting now.",
)

_lock = threading.Lock()
_next: int = 0


def take_readback() -> str:
    """Next line in a 25-phrase cycle (one advance per call)."""
    global _next
    with _lock:
        s = _READBACKS[_next]
        _next = (_next + 1) % len(_READBACKS)
    return s


def take_readback_rexmit() -> str:
    """Same pool, re-transmit run — still one step on the big wheel."""
    return f"{take_readback()} (re-transmit)"


def readback_phrase_count() -> int:
    return len(_READBACKS)


def reset_readback_cycler() -> None:
    """Reset the rotation (tests, or a fresh demo session if you add a call site)."""
    global _next
    with _lock:
        _next = 0
