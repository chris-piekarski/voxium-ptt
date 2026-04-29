"""HTTP client for local `llama-server` (llama.cpp) used by the polish path."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


@dataclass
class LlamaCppChatResult:
    ok: bool
    text: str
    error: str | None
    seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    raw_status: int | None


def _base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/") + "/"


def llama_cpp_reachable(
    base_url: str, *, timeout: float = 1.0
) -> tuple[bool, str | None]:
    """Return `(ok, reason)` using `GET /health` as the readiness probe."""
    try:
        r = requests.get(urljoin(_base(base_url), "health"), timeout=timeout)
    except requests.RequestException as e:
        return False, _format_request_error(base_url, e)
    if r.status_code == 200:
        return True, None
    if r.status_code == 503:
        try:
            data = r.json()
        except json.JSONDecodeError:
            return False, "loading"
        err = data.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message") or "loading")
            return False, msg
        return False, "loading"
    return False, f"HTTP {r.status_code}"


def llama_cpp_loaded_model(base_url: str, *, timeout: float = 1.0) -> str | None:
    """Best-effort loaded model id from `GET /v1/models`."""
    try:
        r = requests.get(urljoin(_base(base_url), "v1/models"), timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    items = data.get("data")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    model_id = first.get("id")
    return str(model_id).strip() if model_id else None


def llama_cpp_chat_completions(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> LlamaCppChatResult:
    """
    OpenAI-style ``POST /v1/chat/completions`` to a local ``llama-server`` instance.

    Reused by the polish pass and the optional on-client UX chatter (different prompts).
    """
    import time as _time

    t0 = _time.perf_counter()
    url = urljoin(_base(base_url), "v1/chat/completions")
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        r = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as e:
        return LlamaCppChatResult(
            ok=False,
            text="",
            error=_format_request_error(base_url, e),
            seconds=_time.perf_counter() - t0,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            raw_status=None,
        )
    elapsed = _time.perf_counter() - t0
    if r.status_code != 200:
        return LlamaCppChatResult(
            ok=False,
            text="",
            error=f"HTTP {r.status_code}: {(r.text or '')[:500]}",
            seconds=elapsed,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            raw_status=r.status_code,
        )
    try:
        data = r.json()
    except json.JSONDecodeError as e:
        return LlamaCppChatResult(
            ok=False,
            text="",
            error=f"Invalid JSON: {e}",
            seconds=elapsed,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            raw_status=r.status_code,
        )
    text = ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                text = str(msg.get("content") or "").strip()
    usage = data.get("usage") if isinstance(data, dict) else None
    return LlamaCppChatResult(
        ok=True,
        text=text,
        error=None,
        seconds=elapsed,
        prompt_tokens=_usage_int(usage, "prompt_tokens"),
        completion_tokens=_usage_int(usage, "completion_tokens"),
        total_tokens=_usage_int(usage, "total_tokens"),
        raw_status=r.status_code,
    )


def llama_cpp_chat(
    base_url: str,
    model: str,
    transcript: str,
    *,
    timeout: float,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> LlamaCppChatResult:
    from voxium.polish_prompt import system_message, user_message

    return llama_cpp_chat_completions(
        base_url,
        model,
        [
            {"role": "system", "content": system_message()},
            {"role": "user", "content": user_message(transcript)},
        ],
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _usage_int(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_request_error(base_url: str, exc: requests.RequestException) -> str:
    base = (base_url or "").rstrip("/") or "http://127.0.0.1:11435"
    if isinstance(exc, requests.Timeout):
        return (
            f"llama.cpp timed out at {base}; check the local llama-server process "
            "and model load, then retry."
        )
    if isinstance(exc, requests.ConnectionError):
        return (
            f"llama.cpp is unreachable at {base}; start the local llama-server "
            "process, verify /health, then retry."
        )
    return f"llama.cpp request failed at {base}: {exc}"
