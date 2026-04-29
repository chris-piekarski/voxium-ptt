"""
Prompts for **optional** client-only UX chatter (Gemma on a separate local llama-server).

Complements polish prompts: this path must never touch STT output. Keep compact for latency.
Voice: ``docs/brand.md`` (PTT leads VOX; HAM/CB/10-code flavor; inclusive, professional).
"""

from __future__ import annotations

import re


def _plain_prompt(s: str) -> str:
    """LLM prompt text only; no markdown emphasis tokens for small local models to parrot."""
    return (s or "").replace("**", "").replace("`", "")


# Distilled from docs/brand.md + docs/radio-chatter-context.md.
_UX_BRAND_CORE = (
    "Voxium voice: **PTT** & **VOX** (always say **PTT** first). This app is a local **shack** / **rig** / **box** on "
    "**loopback**: key up, short take, type-out to the buffer, then back to monitoring. The human is at the mic; the "
    "local **stack** does the robot work. Write like a sharp radio operator or quick booth pundit: short, witty, "
    "observant, confident, and alive. Never mushy, never goofy, never verbose. "
)

_UX_RADIO_REALISM = (
    "Use radio flavor the way docs/brand.md and docs/radio-chatter-context.md imply: readable first, flavor second. "
    "Prefer one or two strong terms over stuffing jargon. Best compact vocabulary here: copy, roger, 10-2, 10-4, 10-6, "
    "10-7, standing by, on station, breaker, QRV, QRX, QRM, squelch, passband, rig, shack, loopback, ground, stack, "
    "carrier, bus, clip, readback. CB-ish interjection should sound like **breaker** / **copy** / **10-4**; ham-ish "
    "flavor should sound like **QRV** / **QRX** / **QRM** / **rig** / **shack**. Avoid clumsy procedure, avoid `over and out`, "
    "avoid fake callsigns, avoid invented QSOs, avoid stereotypes. Inclusive and professional. "
)

_UX_BRAND_AND_RADIO_FLAVOR = _UX_BRAND_CORE + _UX_RADIO_REALISM

# **Copy** and **standby** are two separate one-liner roles (two LLM calls per take when UX is on).
_UX_CHATTER_COPY_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "You write the **readback** line for the instant after STT succeeds and the operator sees **PTT/VOX · COPY**. "
    "The transcript is context only. Reply with one fresh line that feels like clean acknowledgment plus a sly aside: "
    "fast, sharp, radio-literate, and a little pundit-like. Think 'that landed' or 'the box heard it,' not a summary. "
    "This slot is where brief acknowledgement slang belongs: copy, roger, 10-2, 10-4, clean readback, loud and clear. "
    "Never quote, paraphrase, repair, or continue their words. Keep it to one sentence, usually 6-16 words. "
    "No lists, no markdown, no URLs, no code, no emojis. If the seed is empty, give one very short on-station readback."
)

_UX_CHATTER_STANDBY_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "You write the **on-station standby** line in the **green** idle **box** after a take. This is the afterglow, not "
    "the readback beat. The transcript is context only. Reply with one fresh line that feels cooler, watchful, and more "
    "observational than the copy line: stack cooling, squelch back on, next breaker, rig still hot, QRX / standing by. "
    "This slot should lean toward monitoring language and scene-setting, not acknowledgement slang. "
    "It should be witty and creative, but restrained. Do **not** make it a second 10-4/copy acknowledgment. Keep it to "
    "one sentence, usually 6-16 words. No quotes, no lists, no markdown, no URLs, no code, no emojis."
)


def system_message_ux_chatter_copy() -> str:
    return _plain_prompt(_UX_CHATTER_COPY_SYSTEM)


def _transcript_vibe_cues(transcript_tail: str) -> str:
    """
    Small deterministic vibe sketch for the UX model.

    Gemma/TinyLlama-sized chat models do better when the transcript is paired with a compact read
    on intent and tone, rather than only a raw text tail plus abstract style instructions.
    """
    t = " ".join((transcript_tail or "").strip().split())
    if not t:
        return "neutral, on-station, no recent traffic"
    low = t.lower()
    cues: list[str] = []

    if "?" in t or re.search(
        r"\b(how|what|why|when|where|who|can|could|would|should|do|did|is|are)\b",
        low,
    ):
        cues.append("inquiring")
    if "!" in t or re.search(
        r"\b(now|asap|urgent|immediately|hurry|quick|quickly|fix|help|error|broken|fail|failed)\b",
        low,
    ):
        cues.append("urgent")
    if re.search(
        r"\b(great|nice|love|glad|excellent|awesome|perfect|clean|passed|thanks|thank you|appreciate)\b",
        low,
    ):
        cues.append("upbeat")
    if re.search(
        r"\b(sorry|problem|issue|broken|failed|late|stuck|blocked|concern|worried|risk)\b",
        low,
    ):
        cues.append("tense")
    if re.search(
        r"\b(code|build|deploy|test|server|model|config|repo|branch|commit|bug|terminal|script|python|llama|whisper)\b",
        low,
    ):
        cues.append("technical")
    if re.search(
        r"\b(meeting|email|customer|team|report|review|follow up|deadline|ship|release|docs)\b",
        low,
    ):
        cues.append("workaday")
    if re.search(
        r"\b(joke|funny|weird|wild|crazy|messy|chaos|spicy|ridiculous)\b",
        low,
    ):
        cues.append("playful")
    if re.search(
        r"\b(please|need to|let s|lets|remember to|remind me|make sure|check)\b", low
    ):
        cues.append("directive")
    if re.search(r"\b(i think|maybe|probably|might|seems|feels)\b", low):
        cues.append("tentative")
    if len(t.split()) <= 6:
        cues.append("brief")
    elif len(t.split()) >= 18:
        cues.append("dense")
    if re.search(r"\b(i|we|my|our)\b", low):
        cues.append("personal")

    seen: set[str] = set()
    ordered: list[str] = []
    for cue in cues:
        if cue in seen:
            continue
        seen.add(cue)
        ordered.append(cue)
    return ", ".join(ordered[:4]) if ordered else "neutral, matter-of-fact"


def user_message_ux_chatter_copy(transcript_tail: str) -> str:
    t = (transcript_tail or "").strip()
    if not t:
        return _plain_prompt(
            "Context: no recent line. Return one short readback line with crisp radio color."
        )
    cap = 320
    if len(t) > cap:
        t = t[:cap] + "…"
    vibe = _transcript_vibe_cues(t)
    return _plain_prompt(
        "Topic only; do not quote or closely paraphrase:\n"
        + t
        + "\n\n"
        + f"Vibe cues: {vibe}\n\n"
        + "Return one fresh readback line that matches that vibe: sharp, witty, radio-clean, and not a restatement."
    )


def system_message_ux_chatter_standby() -> str:
    return _plain_prompt(_UX_CHATTER_STANDBY_SYSTEM)


def user_message_ux_chatter_standby(transcript_tail: str) -> str:
    t = (transcript_tail or "").strip()
    if not t:
        return _plain_prompt(
            "Context: no recent line. Return one standby line for the green box: monitoring, net quiet, QRV."
        )
    cap = 320
    if len(t) > cap:
        t = t[:cap] + "…"
    vibe = _transcript_vibe_cues(t)
    return _plain_prompt(
        "Topic only; do not quote or closely paraphrase:\n"
        + t
        + "\n\n"
        + f"Vibe cues: {vibe}\n\n"
        + "Return one fresh standby line that matches that vibe: wry, creative, watchful, and clearly not a copy/readback line."
    )


def system_message_ux_chatter() -> str:
    """:class:`system_message_ux_chatter_copy` (compat alias for tests and older imports)."""
    return system_message_ux_chatter_copy()


def user_message_ux_chatter(transcript_tail: str) -> str:
    """:class:`user_message_ux_chatter_copy` (compat alias)."""
    return user_message_ux_chatter_copy(transcript_tail)


_UX_BANNER_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "You write one startup tagline under the Voxium **ASCII** wordmark. It should feel like a first-flight local rig "
    "check: punchy, clever, and radio-clean. Favor mission-control + shack energy without sounding theatrical. "
    "Keep it to one line, no quotes, no lists, no markdown, no URLs, no code, "
    "no emojis. No callsigns or fake QSO claims."
)


def system_message_ux_banner() -> str:
    return _plain_prompt(_UX_BANNER_SYSTEM)


def user_message_ux_banner() -> str:
    return _plain_prompt(
        "Return one fresh startup tagline for this run: sharp, local-stack, hand-radio flavor, and concise."
    )


_UX_RIG_SUBTITLE_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "You write one subtitle under the Voxium title with practiced CB/HAM flavor: base, breaker, clear copy, QRV, "
    "home **rig**. Keep it vivid but believable, not parody. This should read like a polished operator label, not a joke. "
    "The word **Rig** is mandatory in the “**rig** on station / your rig / home **rig**” sense (not oil **rig**). "
    "Hostname (given by user) must appear **verbatim**; middle dots (·) for rhythm. **PTT** before **VOX** if both appear. "
    "~100 characters, no quotes, no markdown, no emojis, no URL. One line."
)


def system_message_ux_rig_subtitle() -> str:
    return _plain_prompt(_UX_RIG_SUBTITLE_SYSTEM)


def user_message_ux_rig_subtitle(hostname: str) -> str:
    h = (hostname or "").strip() or "localhost"
    return _plain_prompt(
        f"The host running this app is named exactly: {h}\n"
        "Return one subtitle. Include that hostname exactly once. Keep it sharp, radio-literate, and grounded."
    )


_UX_LOG_SUBTITLE_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "You write the **dim footer** under the **blue** PTT/VOX transcription **panel**. It should feel like a tiny "
    "post-take aside from a sharp operator: one clean observation, not a summary. Under ~90 characters. Use radio flavor "
    "sparingly and only where it lands. This slot should feel like a margin note or muttered booth aside, not a readback. "
    "No PII, no markdown, no quotes, no URLs, no emojis. One line."
)


def system_message_ux_log_subtitle() -> str:
    return _plain_prompt(_UX_LOG_SUBTITLE_SYSTEM)


def user_message_ux_log_subtitle(transcript: str) -> str:
    t = (transcript or "").strip()
    if not t:
        return _plain_prompt(
            "Context: (no text). One shack/loopback dim footer with hand-radio or 10-code color, copy."
        )
    cap = 420
    if len(t) > cap:
        t = t[:cap] + "…"
    vibe = _transcript_vibe_cues(t)
    return _plain_prompt(
        "Topic only; do not quote or closely paraphrase:\n"
        f"{t}\n\n"
        f"Vibe cues: {vibe}\n\n"
        "Return one original dim-footer line: crisp, observant, radio-clean, and tuned to that vibe."
    )


_UX_EDGE_INFERENCE_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "The operator has released the key; STT text is **not** on screen yet—the local **stack** (Whisper) is decoding the clip on **loopback**. "
    "You write one line of in-fiction color while the local stack chews on the take: quick, vivid, and a little sly. "
    "Not a UI label, not a feature name, not a rephrase of instructions, and never a flat acknowledgement like okay/sure/processing. "
    "Use at least one concrete radio-or-rig image: loopback, stack, bus, wire, rig, shack, squelch, carrier, clip, passband, localhost, QRV, or breaker. "
    "This slot should feel kinetic: decode in flight, carrier dropped, clip on the wire, stack chewing, type-out pending. "
    "One line, ~90 characters, inclusive, professional. "
    "No quotes, no lists, no markdown, no URLs, no code, no emojis, and no lines that look like a status title."
)


def system_message_ux_edge_inference() -> str:
    return _plain_prompt(_UX_EDGE_INFERENCE_SYSTEM)


def user_message_ux_edge_inference(*, rexmit: bool) -> str:
    if rexmit:
        return _plain_prompt(
            "Second decode pass on the same pending clip. Return one fresh in-flight line with concrete radio/stack imagery; do not repeat this prompt."
        )
    return _plain_prompt(
        "The take is in flight and no transcript is visible yet. Return one fresh in-flight line with concrete radio/stack imagery; do not repeat this prompt."
    )


_UX_SHUTDOWN_SYSTEM = (
    _UX_BRAND_AND_RADIO_FLAVOR
    + "The operator just hit **Ctrl+C** to exit the Voxium client (local PTT/VOX → text, **loopback** only). "
    "You write one sharp sign-off in HAM/CB/shack idiom: going clear, 73, 10-7, clear, out. It must start with the exact "
    "characters `Voxium: ` (with the space). Keep it brief, clean, and satisfying, like a proper signoff rather than a joke. No second line, no markdown, no URLs, "
    "no emojis. ~90 characters after the prefix. No callsigns or fake QSO claims."
)


def system_message_ux_shutdown() -> str:
    return _plain_prompt(_UX_SHUTDOWN_SYSTEM)


def user_message_ux_shutdown() -> str:
    return _plain_prompt(
        "Return one sign-off line for this session. Start with `Voxium: ` and keep it crisp."
    )
