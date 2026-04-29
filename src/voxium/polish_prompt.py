"""Versioned prompt for the local polish pass."""

from __future__ import annotations

POLISH_PROMPT_VERSION = "2"

# Keep this compact and explicit so tests can lock the guardrails.
_POLISH_SYSTEM = (
    "You are a text cleanup assistant for terminal dictation. "
    "Re-encode natural-language prose only: fix grammar, punctuation, capitalization, and filler words. "
    "Preserve exact shell commands, code, filenames, paths, flags, environment variables, log lines, stack traces, and quoted text. "
    "Do not add facts, explanations, headings, or answers. "
    "Do not paraphrase technical tokens or normalize punctuation inside commands/code/logs. "
    "If the input is mostly code, command, or log text, return it unchanged except for obvious surrounding prose cleanup. "
    "Keep the same language. Preserve line breaks for structured input; otherwise return one cleaned paragraph, copy."
)


def system_message() -> str:
    return _POLISH_SYSTEM


def user_message(transcript: str) -> str:
    t = (transcript or "").strip()
    return (
        "Transcript to clean up. Keep the same meaning. Preserve commands, code, paths, flags, "
        f"environment variables, and log text exactly.\n\n{t}"
    )
