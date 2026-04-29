"""Prompt guardrail tests for the polish pass."""

from __future__ import annotations

from voxium.polish_prompt import POLISH_PROMPT_VERSION, system_message, user_message


def test_polish_prompt_version_is_current() -> None:
    assert POLISH_PROMPT_VERSION == "2"


def test_system_prompt_mentions_terminal_guardrails() -> None:
    msg = system_message().lower()
    assert "shell commands" in msg
    assert "code" in msg
    assert "paths" in msg
    assert "environment variables" in msg
    assert "log lines" in msg
    assert "do not add facts" in msg


def test_user_message_preserves_structured_text_instruction() -> None:
    msg = user_message("cat file.log | grep hi")
    assert "preserve commands" in msg.lower()
    assert "cat file.log | grep hi" in msg
