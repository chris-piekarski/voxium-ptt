"""
Rotating HAM/CB-tinged readback lines for successful VOX (brand: docs/brand.md).
Pure + thread-safe: safe when transcribe runs on a worker thread.
"""
from __future__ import annotations

import threading

_READBACKS: tuple[str, ...] = (
    "10-4, good buddy — copy loud and clear.",
    "Roger; good read on that transmission.",
    "Solid copy — you're 5 by 5 on this end.",
    "QSL: text on the ground, VOX in the clear.",
    "That's a copy — no birdies on the line.",
    "Heard you clean — pasting the downlink.",
    "Roger dodger, good audio on the loopback.",
    "Wall-to-wall copy; green board on this end.",
    "You’re on frequency; I got all of it.",
    "No QRM in that take — roger, pasting.",
    "Copy that, standing by.",
    "Good S-units in the VOX; text on the way.",
    "Loud in the can — 10-4, pasting the words.",
    "No DX here—just a clean loopback. Roger, pasting.",
    "Clear and readable; over and into the buffer.",
    "Squelch is quiet; good VOX, good read.",
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
    global _next, _edge_infer_next, _edge_rexmit_next
    with _lock:
        _next = 0
        _edge_infer_next = 0
        _edge_rexmit_next = 0


# Status detail while the local STT is running (after PTT, before paste). Cycles; brand: docs/brand.md.
_EDGE_INFERENCE_DETAILS: tuple[str, ...] = (
    "Local robot on loopback — chewing through this transmission.",
    "Breaker one-nine for the VOX — the stack’s on your audio, stand by for the type-out.",
    "Rubber down, silicon up — the metal ear’s spinnin’ the dial on your take, good buddy.",
    "QRM-free at the shack: edge op’s on localhost, doin’ the hard lift on that clip.",
    "You dropped carrier; the headless co-pilot’s keyin’ the decode — hold the squelch.",
    "Passin’ it to the ear on 127.0.0.1 — copy comin’ when the math’s done, 10-4.",
    "That’s a roger on the round-trip — one hop: mouth, bus, model; don’t double-key.",
    "Rattlin’ the VOX into letters — home rig’s hot, you’re still the control op, over.",
    "Your 20’s the desk; the stack’s in the same room — no phone patch, all backplane, copy.",
    "Squelch’s open on the silicone side — choppin’ your pass into plain text, standing by.",
    "10-2 on the readback path — the fan on the card’s the only other carrier in the shack.",
    "From squelch to script on the same box — you’re QRV, the bot’s the lid on channel.",
    "Ain’t skip, ain’t a repeater — just you, the mic, and a good buddy on the bus.",
    "Clear to the local wire — the robot’s trollin’ the waveform, green board in the void, 10-4.",
    "Wall-to-wall on the floatin’ words — hold short; the edge crew’s chattin’ up the chips.",
    "Key down was clean — now the silicon jockey’s chewin’ the file for a word check, over.",
    "No eyeball, no double-hop — PTT, loopback, text in the log; 73 in advance on this one.",
    "Ringin’ the VOX in the back room — the stack’s got your audio on the hook, out.",
    "The band’s local except your voice and a few watts on the M.2 — stand by, breaker.",
    "Loud in the can, light on the pipe — the machine’s on frequency; don’t break in yet.",
    "This ain’t a net — it’s a one-talker, one-robot, same-shack QSO. Copy when it’s typed.",
    "Duck the echo; trust the bus — the rig on loopback’s readin’ the mail, good buddy.",
    "Carrier’s in the rear-view — inference’s in the right seat, mappin’ the audio to type.",
    "Sunny side on the VOX, grey matter on the GPU — hold one for the downlink, 10-4.",
)

_edge_infer_next: int = 0

_REXMIT_EDGE_DETAILS: tuple[str, ...] = (
    "Same VOX clip — new decode pass on the local wire, breaker.",
    "Re-throwin’ the same take — different draw on the robot deck, 10-4.",
    "Round two on the rubber — local stack’s listenin’ one more lap, over.",
    "That clip’s back on the lift — the metal op’s givin’ it another go, good buddy.",
    "Re-keyed the file on loopback — stand by for a fresh read on the home wire.",
    "Same audio, new dice — silicon’s runnin’ the play twice; copy when the words land.",
    "One more swing at the carrier — re-xmit, no skip, all on the backplane, over.",
    "Reheat the VOX on localhost — the crew’s chattin’ up the clip again, 10-4.",
    "Double-buzz on the same file — the edge box wants another look at that transmission.",
    "Encore on the bus — the rig’s hittin’ replay, stand by for type-out, breaker.",
    "Lap two: mouth to bus to text — the stack’s a straight shot, second pass, over.",
    "Déjà on the VOX — new decode round; don’t key, the robot’s still on the line, copy.",
)

_edge_rexmit_next: int = 0


def take_edge_inference_detail() -> str:
    """Next edge-inference status line (one advance per PTT end / transcribe start)."""
    global _edge_infer_next
    with _lock:
        s = _EDGE_INFERENCE_DETAILS[_edge_infer_next]
        _edge_infer_next = (_edge_infer_next + 1) % len(_EDGE_INFERENCE_DETAILS)
    return s


def take_edge_inference_rexmit_detail() -> str:
    """Next re-transmit edge status line (F7 re-run on pending WAV)."""
    global _edge_rexmit_next
    with _lock:
        s = _REXMIT_EDGE_DETAILS[_edge_rexmit_next]
        _edge_rexmit_next = (_edge_rexmit_next + 1) % len(_REXMIT_EDGE_DETAILS)
    return s


def edge_inference_phrase_count() -> int:
    return len(_EDGE_INFERENCE_DETAILS)


def edge_rexmit_phrase_count() -> int:
    return len(_REXMIT_EDGE_DETAILS)
