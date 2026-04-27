"""
Slash command completion (``/help``, ``/mic``, …) — testable, no pynput.

When the first token (after ``/``) is still being edited, we match primary commands
(``help``, ``mic``, ``gpu``, ``models``) and all aliases. After the operator types a
space, command completion and hints stop (first word committed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# One canonical name per public command; order = Tab cycle when ambiguous.
SLASH_COMMAND_ORDER: tuple[str, ...] = ("help", "history", "disk", "mic", "gpu", "models")

# (typed token, primary) — all forms accepted by :func:`voxium.slash_commands.run_slash_line`.
SLASH_ALIASES: tuple[tuple[str, str], ...] = (
    ("help", "help"),
    ("?", "help"),
    ("h", "help"),
    ("history", "history"),
    ("hist", "history"),
    ("transcripts", "history"),
    ("disk", "disk"),
    ("du", "disk"),
    ("usage", "disk"),
    ("mic", "mic"),
    ("m", "mic"),
    ("microphone", "mic"),
    ("input", "mic"),
    ("audio", "mic"),
    ("gpu", "gpu"),
    ("g", "gpu"),
    ("cuda", "gpu"),
    ("models", "models"),
    ("model", "models"),
)


def is_slash_command_typing_not_args(buffer: str) -> bool:
    """
    True only while the first word (command name) is being typed — no whitespace after it yet.

    ``/mic x`` and ``/mic`` with trailing space to start an argument are False.
    """
    s = (buffer or "").lstrip()
    if not s.startswith("/"):
        return False
    # First word of the buffer is exactly ``/`` + one token, no following space+rest.
    return re.match(r"^/[^\s]*$", s) is not None


def _command_prefix_lowercase(buffer: str) -> str:
    s = (buffer or "").lstrip()
    if not s.startswith("/"):
        return ""
    t = s[1:]
    if " " in t:
        return ""
    return t.lower()


def list_slash_command_matches(prefix: str) -> list[str]:
    """Return ordered primary command names that match the partial first token (after ``/``)."""
    p = (prefix or "").lower()
    if not p:
        return list(SLASH_COMMAND_ORDER)
    seen: dict[str, None] = {}
    for token, primary in SLASH_ALIASES:
        if len(p) == 1 and p == "h" and len(token) > 1:
            # ``/h`` is only the ``h`` help alias, not the ``hist*``/``history`` commands.
            continue
        if len(p) == 1:
            if token == p or (len(token) > 1 and token.startswith(p)):
                seen[primary] = None
        else:
            if token.startswith(p):
                seen[primary] = None
    return [x for x in SLASH_COMMAND_ORDER if x in seen]


def _longest_common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    a = min(strings, key=len)
    for i, c in enumerate(a):
        if not all(len(s) > i and s[i] == c for s in strings):
            return a[:i]
    return a


@dataclass(frozen=True)
class TabOutcome:
    new_buffer: str
    tab_cycle: int
    did_extend: bool


def apply_slash_tab(
    buffer: str,
    *,
    tab_cycle: int,
) -> TabOutcome:
    """
    ``Tab``: extend with longest common prefix, or if none, pick options[tab_cycle % n].

    Returns updated buffer, next cycle, and whether prefix grew (resets host-side cycle
    logic when the buffer changes in other ways).
    """
    s = (buffer or "").lstrip()
    if not s.startswith("/"):
        return TabOutcome(buffer, 0, False)
    if not is_slash_command_typing_not_args(buffer):
        return TabOutcome(buffer, 0, False)
    t = s[1:]
    prefix = t.lower()
    options = list_slash_command_matches(prefix)
    if not options:
        return TabOutcome(buffer, 0, False)
    if len(options) == 1:
        return TabOutcome(f"/{options[0]}", 0, True)
    lcp = _longest_common_prefix(options)
    if lcp and len(lcp) > len(prefix) and lcp.lower().startswith(prefix):
        return TabOutcome(f"/{lcp}", 0, True)
    n = len(options)
    pick = options[tab_cycle % n]
    next_c = (tab_cycle + 1) % n
    return TabOutcome(f"/{pick}", next_c, True)


def format_slash_command_hints(
    buffer: str,
    *,
    max_len: int = 200,
) -> str:
    """
    One line: `` /help  ·  /mic  · …`` of matches, or empty if not in command-typing state.
    """
    if not is_slash_command_typing_not_args(buffer):
        return ""
    pre = _command_prefix_lowercase(buffer)
    m = list_slash_command_matches(pre)
    if not m:
        return ""
    line = "  " + "  ·  ".join(f"/{x}" for x in m) + "  (Tab)"
    if len(line) > max_len:
        return line[: max(0, max_len - 1)] + "…"
    return line
