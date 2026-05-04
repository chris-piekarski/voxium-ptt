"""
Slash command completion (``/help``, ``/mic``, ``/models polish ...``) — testable, no pynput.

The first token still completes the primary commands and aliases. After the operator
commits the command word, completion continues for the modeled subcommands and
allow-listed model tags that matter for the session UX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voxium.model_registry import TRUSTED_MODELS
from voxium.polish_model_registry import (
    POLISH_DEFAULT_MODEL,
    list_available_polish_models,
    list_local_polish_models,
)

# One canonical name per public command; order = Tab cycle when ambiguous.
SLASH_COMMAND_ORDER: tuple[str, ...] = (
    "help",
    "health",
    "history",
    "disk",
    "mic",
    "gpu",
    "stats",
    "hotkeys",
    "models",
    "re-encode",
    "polish",
)

# (typed token, primary) — all forms accepted by :func:`voxium.slash_commands.run_slash_line`.
SLASH_ALIASES: tuple[tuple[str, str], ...] = (
    ("help", "help"),
    ("?", "help"),
    ("h", "help"),
    ("health", "health"),
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
    ("stats", "stats"),
    ("stat", "stats"),
    ("hotkeys", "hotkeys"),
    ("hotkey", "hotkeys"),
    ("keys", "hotkeys"),
    ("models", "models"),
    ("model", "models"),
    ("re-encode", "re-encode"),
    ("reencode", "re-encode"),
    ("polish", "polish"),
    ("p", "polish"),
)
_PRIMARY_BY_ALIAS: dict[str, str] = dict(SLASH_ALIASES)
_MODELS_SUBCOMMANDS: tuple[str, ...] = ("transcribe", "polish")
_TRANSCRIBE_ACTIONS: tuple[str, ...] = ("list", "installed", "use")
_MODELS_POLISH_ACTIONS: tuple[str, ...] = ("list", "installed", "use", "on", "off")
_POLISH_ACTIONS: tuple[str, ...] = ("list", "installed", "use", "model", "on", "off")
_HOTKEY_ACTIONS: tuple[str, ...] = ("ptt", "replay")
_HOTKEY_NAMES: tuple[str, ...] = tuple(f"f{i}" for i in range(1, 13))


def _polish_model_names() -> list[str]:
    names = [POLISH_DEFAULT_MODEL]
    names.extend(model.model_id for model in list_available_polish_models())
    names.extend(
        model.name for model in list_local_polish_models() if not model.is_trusted
    )
    seen: dict[str, None] = {}
    for name in names:
        seen[name] = None
    return list(seen)


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


def _normalize_primary(command: str) -> str:
    return _PRIMARY_BY_ALIAS.get((command or "").lower(), (command or "").lower())


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


def _match_options(prefix: str, options: tuple[str, ...] | list[str]) -> list[str]:
    p = (prefix or "").lower()
    if not p:
        return list(options)
    return [opt for opt in options if opt.lower().startswith(p)]


def _completion_matches_for_buffer(buffer: str) -> list[str]:
    s = (buffer or "").lstrip()
    if not s.startswith("/"):
        return []
    if is_slash_command_typing_not_args(buffer):
        prefix = s[1:].lower()
        return [f"/{opt}" for opt in list_slash_command_matches(prefix)]

    trailing_space = s.endswith(" ")
    parts = s[1:].split()
    if not parts:
        return []
    primary = _normalize_primary(parts[0])
    args = parts[1:]

    if primary == "models":
        return _models_completion_matches(args, trailing_space)
    if primary == "polish":
        return _polish_completion_matches(args, trailing_space)
    if primary == "hotkeys":
        return _hotkeys_completion_matches(args, trailing_space)
    return []


def _models_completion_matches(args: list[str], trailing_space: bool) -> list[str]:
    if not args:
        return (
            [f"/models {opt}" for opt in _MODELS_SUBCOMMANDS] if trailing_space else []
        )

    sub = args[0].lower()
    transcribe_names = tuple(sorted(TRUSTED_MODELS))
    polish_names = _polish_model_names()
    if len(args) == 1 and not trailing_space:
        options = list(_MODELS_SUBCOMMANDS) + list(transcribe_names)
        return [f"/models {opt}" for opt in _match_options(sub, options)]

    if sub == "transcribe":
        if len(args) == 1 and trailing_space:
            return [f"/models transcribe {opt}" for opt in _TRANSCRIBE_ACTIONS]
        if len(args) == 2 and not trailing_space:
            opts = [
                f"/models transcribe {opt}"
                for opt in _match_options(args[1], _TRANSCRIBE_ACTIONS)
            ]
            opts.extend(
                f"/models transcribe {name}"
                for name in _match_options(args[1], transcribe_names)
            )
            return opts
        if len(args) == 2 and trailing_space and args[1].lower() == "use":
            return [f"/models transcribe use {name}" for name in transcribe_names]
        if len(args) == 3 and args[1].lower() == "use" and not trailing_space:
            return [
                f"/models transcribe use {name}"
                for name in _match_options(args[2], transcribe_names)
            ]
        return []

    if sub == "polish":
        if len(args) == 1 and trailing_space:
            return [f"/models polish {opt}" for opt in _MODELS_POLISH_ACTIONS]
        if len(args) == 2 and not trailing_space:
            opts = [
                f"/models polish {opt}"
                for opt in _match_options(
                    args[1], list(_MODELS_POLISH_ACTIONS) + polish_names
                )
            ]
            return opts
        if len(args) == 2 and trailing_space and args[1].lower() in {"use", "model"}:
            action = args[1].lower()
            return [f"/models polish {action} {name}" for name in polish_names]
        if (
            len(args) == 3
            and args[1].lower() in {"use", "model"}
            and not trailing_space
        ):
            return [
                f"/models polish {args[1].lower()} {name}"
                for name in _match_options(args[2], polish_names)
            ]
        return []

    return []


def _polish_completion_matches(args: list[str], trailing_space: bool) -> list[str]:
    if not args:
        return [f"/polish {opt}" for opt in _POLISH_ACTIONS] if trailing_space else []

    head = args[0].lower()
    if len(args) == 1 and not trailing_space:
        options = list(_POLISH_ACTIONS) + _polish_model_names()
        return [f"/polish {opt}" for opt in _match_options(head, options)]

    if head in {"use", "model"}:
        if len(args) == 1 and trailing_space:
            return [f"/polish {head} {name}" for name in _polish_model_names()]
        if len(args) == 2 and not trailing_space:
            return [
                f"/polish {head} {name}"
                for name in _match_options(args[1], _polish_model_names())
            ]
        return []

    return []


def _hotkeys_completion_matches(args: list[str], trailing_space: bool) -> list[str]:
    if not args:
        return [f"/hotkeys {opt}" for opt in _HOTKEY_ACTIONS] if trailing_space else []
    action = args[0].lower()
    if len(args) == 1 and not trailing_space:
        return [f"/hotkeys {opt}" for opt in _match_options(action, _HOTKEY_ACTIONS)]
    if len(args) == 1 and trailing_space and action in _HOTKEY_ACTIONS:
        return [f"/hotkeys {action} {key}" for key in _HOTKEY_NAMES]
    if len(args) == 2 and action in _HOTKEY_ACTIONS and not trailing_space:
        return [
            f"/hotkeys {action} {key}" for key in _match_options(args[1], _HOTKEY_NAMES)
        ]
    return []


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
    options = _completion_matches_for_buffer(buffer)
    if not options:
        return TabOutcome(buffer, 0, False)
    if len(options) == 1:
        return TabOutcome(options[0], 0, True)
    prefix = s
    lcp = _longest_common_prefix(options)
    if lcp and len(lcp) > len(prefix) and lcp.lower().startswith(prefix):
        return TabOutcome(lcp, 0, True)
    n = len(options)
    pick = options[tab_cycle % n]
    next_c = (tab_cycle + 1) % n
    return TabOutcome(pick, next_c, True)


def format_slash_command_hints(
    buffer: str,
    *,
    max_len: int = 200,
) -> str:
    """
    One line: `` /help  ·  /mic  · …`` of matches, or empty if there is nothing actionable.
    """
    m = _completion_matches_for_buffer(buffer)
    if not m:
        return ""
    line = "  " + "  ·  ".join(m) + "  (Tab)"
    if len(line) > max_len:
        return line[: max(0, max_len - 1)] + "…"
    return line
