"""
Optional on-station “UX chatter”: short one-liners from the shared local polish/chatter
``llama-server`` lane.

Default **on**; when off or on error, the UI uses the same static copy as before.
See ``docs/ux-chatter-gemma.md``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Any, Literal

from voxium import polish_profile
from voxium.llama_cpp_client import (
    LlamaCppChatResult,
    llama_cpp_chat_completions,
    llama_cpp_reachable,
)
from voxium.metrics_table import format_polish_usage_suffix
from voxium.polish_model_registry import (
    DEFAULT_TRUSTED_POLISH_MODEL_ID,
    POLISH_DEFAULT_MODEL,
)
from voxium.ux_chatter_prompt import (
    system_message_ux_banner,
    system_message_ux_chatter_copy,
    system_message_ux_chatter_standby,
    system_message_ux_edge_inference,
    system_message_ux_log_subtitle,
    system_message_ux_rig_subtitle,
    system_message_ux_shutdown,
    user_message_ux_banner,
    user_message_ux_chatter_copy,
    user_message_ux_chatter_standby,
    user_message_ux_edge_inference,
    user_message_ux_log_subtitle,
    user_message_ux_rig_subtitle,
    user_message_ux_shutdown,
)

_LOG = logging.getLogger(__name__)
_SHARED_LLAMA_CPP_URL_DEFAULT = "http://127.0.0.1:11435"

_wit_lock = threading.Lock()
_cached_wit: str = ""
# Set by :func:`voxium.app.ensure_llama_cpp_for_ux_chatter` so UX chatter shares
# the active polish/re-encode ``llama-server`` and selected model.
_resolved_ux_chatter_model_id: str | None = None
_resolved_ux_chatter_base_url: str | None = None

# When the local model parrots STT (common on small GGUFs), ship on-brand lines instead.
_UX_ECHO_FALLBACK_WITS: tuple[str, ...] = (
    "Roger — new sideband; that last take’s not getting a second carrier on this net, copy.",
    "10-4, home rig: we need a fresh breaker, not a replay of the log, standing by.",
    "Copy—local stack’s hot, but we’re not parking the same words twice, on station.",
    "Breaker one-nine for the loopback: STT’s the log; this line’s the color, 10-2.",
    "QRV on PTT, good buddy; the box heard you but this line’s off-frequency from the type-out, 10-3.",
    "Loud in the can—green board on copy; VOX in the passband, squelch’s tight, over.",
)
_UX_ECHO_FALLBACK_LOG_SUBS: tuple[str, ...] = (
    "PTT & VOX log — STT in the box; this footer’s extra, not a restate, copy.",
    "Local loop only — read the net above; this line’s the aside, clear.",
)
# When the model parrots EDGE INFERENCE prompts, ship static pool lines (aligned with :mod:`voxium.radio_readback`).
_UX_EDGE_ECHO_FALLBACK: tuple[str, ...] = (
    "Local robot on loopback — chewing through this transmission.",
    "QRM-free at the shack: edge op’s on localhost, doin’ the hard lift on that clip.",
    "You dropped carrier; the headless co-pilot’s keyin’ the decode — hold the squelch.",
    "Passin’ it to the ear on 127.0.0.1 — stand by; type-out’s when the math’s done, 10-4.",
    "Same VOX clip — new decode pass on the local wire, breaker.",
    "Re-throwin’ the same take — different draw on the robot deck, 10-4.",
    "Lap two: mouth to bus to text — the stack’s a straight shot, second pass, over.",
    "Déjà on the VOX — new decode round; don’t key, the robot’s still on the line, copy.",
)

_UX_PROMPT_ECHO_NEEDLES: tuple[str, ...] = (
    "topic seed",
    "stt topic seed",
    "mood do not quote",
    "do not output any of this text",
    "do not output any of this",
    "do not quote or paraphrase",
    "not a restate of their words",
    "readback quip",
    "standby aside",
    "green box",
    "same slot as a roger",
    "if you echo the seed",
    "out of spec",
    "channel s double keyed",
    "one original one liner",
    "one line panel subtitle",
    "write the one line panel subtitle",
    "one fresh tagline for this run",
    "sign off for this session",
    "prefix voxium",
    "going clear 73 or 10 7",
    "not a regurgitation of this message",
    "one new line of shack",
    "one short readback",
    "one standby for the green box",
    "your line one",
)


def _norm_ux_sim(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def ux_output_likely_echoes_seed(*, output: str, seed: str) -> bool:
    """
    True when the model line is effectively the STT line (or a paraphrase too close to it).
    Used to avoid shipping “wit” that is just a copy of the user’s words.
    """
    o = _norm_ux_sim(output)
    t = _norm_ux_sim(seed)
    if not o or not t:
        return False
    if o == t:
        return True
    if len(o) >= 10 and o in t:
        return True
    if len(t) >= 10 and t in o and len(o) >= int(len(t) * 0.88):
        return True
    if len(o) > 8 and SequenceMatcher(a=o, b=t).ratio() > 0.78:
        return True
    return False


def ux_output_likely_parrots_log_subtitle_user_prompt(*, output: str) -> bool:
    """
    True when the model repeated our **user** message scaffolding (e.g. “dim footer… CB/HAM spice”) instead of
    a real aside. Distinctive phrases only, so normal radio patter is not filtered.
    """
    o = _norm_ux_sim(output)
    if not o or len(o) < 10:
        return False
    needles = (
        "dim footer",
        "new dim footer",
        "cb ham spice",
        "stt topic seed",
        "not a readback of their",
        "readback of their line",
        "paraphrase this text in your answer",  # new prompt header, if the model regurgitates it
    )
    if any(n in o for n in needles):
        return True
    if "ptt" in o and "vox" in o and "spice" in o:
        return True
    if "one new" in o and "box" in o and "ptt" in o:
        return True
    return False


def ux_output_likely_parrots_edge_inference_user_prompt(*, output: str) -> bool:
    """
    True when the line looks like a mash-up of EDGE INFERENCE / PTT / “Copy” instructions
    (the small UX model often regurgitates prompt scaffolding).
    """
    o = _norm_ux_sim(output)
    raw = output or ""
    raw_l = raw.lower()
    if not o or len(o) < 4:
        return False
    needles = (
        "one quip for the",
        "one quip for",
        "second line under",
        "status header",
        "edge inference re xmit",
        "main header",
        "re xmit second line",
    )
    for n in needles:
        if n in o:
            return True
    if "re xmit" in o and "second" in o and "line" in o:
        return True
    # "PTT – EDGE INFERENCE: Copy."-style label mash
    if "edge" in o and "inference" in o and "ptt" in o and len(o) < 96:
        return True
    if "edge inference" in raw_l and ("ptt" in o or "ptt" in raw_l):
        if " – " in raw or ": " in raw or "—" in raw:
            return True
    if "regurgitation" in o or ("regurgitate" in o and "this message" in o):
        return True
    return False


def ux_output_too_generic_for_edge_inference(*, output: str) -> bool:
    """
    True when the edge-inference line is too bland to add any living UX texture.

    This path exists specifically to make the app feel alive while decode is in flight, so single
    acknowledgements like ``okay`` should fall back to the curated dynamic pool.
    """
    raw = (output or "").strip()
    o = _norm_ux_sim(raw)
    if not o:
        return True
    generic_exact = {
        "ok",
        "okay",
        "k",
        "sure",
        "yes",
        "yep",
        "roger",
        "copy",
        "standing by",
        "processing",
        "working",
        "loading",
        "one moment",
        "please wait",
    }
    if o in generic_exact:
        return True
    words = o.split()
    if len(words) <= 2:
        allowed_short = {"qrv", "qrx", "loopback", "decode", "decoding"}
        if not any(w in allowed_short for w in words):
            return True
    radio_or_stack_terms = (
        "loopback",
        "stack",
        "clip",
        "audio",
        "wire",
        "bus",
        "rig",
        "shack",
        "squelch",
        "decode",
        "decoding",
        "type out",
        "typeout",
        "robot",
        "silicon",
        "localhost",
        "carrier",
        "passband",
        "qrv",
        "qrx",
        "qrm",
        "breaker",
        "copy",
        "10 4",
    )
    if len(words) <= 4 and not any(term in o for term in radio_or_stack_terms):
        return True
    return False


def ux_output_likely_parrots_any_ux_prompt(*, output: str) -> bool:
    """
    True when the model appears to be reciting prompt scaffolding rather than producing UX copy.

    This is intentionally broad and slightly conservative: visible UX text should never contain our
    hidden instruction headers or meta-language.
    """
    o = _norm_ux_sim(output)
    raw = (output or "").strip()
    raw_l = raw.lower()
    if not o or len(o) < 8:
        return False
    if any(n in o for n in _UX_PROMPT_ECHO_NEEDLES):
        return True
    if ux_output_likely_parrots_log_subtitle_user_prompt(output=output):
        return True
    if ux_output_likely_parrots_edge_inference_user_prompt(output=output):
        return True
    if "topic" in o and "seed" in o:
        return True
    if "your line" in o and ("readback" in o or "standby" in o):
        return True
    if "voxium:" in raw_l and "prefix" in o:
        return True
    if ("one line" in o or "one liner" in o) and (
        "subtitle" in o or "tagline" in o or "readback" in o
    ):
        return True
    if any(term in o for term in ("quote", "paraphrase", "markdown", "url")) and (
        "do not" in o or "not" in o
    ):
        return True
    if "copy" in o and "out of spec" in o:
        return True
    return False


def _pick_ux_deterministic(lines: tuple[str, ...], seed: str) -> str:
    if not lines:
        return ""
    h = abs(hash(_norm_ux_sim(seed)))
    return lines[h % len(lines)]


def set_resolved_ux_chatter_model_id(model_id: str | None) -> None:
    global _resolved_ux_chatter_model_id
    m = (model_id or "").strip()
    _resolved_ux_chatter_model_id = m or None


def set_resolved_ux_chatter_runtime(base_url: str | None, model_id: str | None) -> None:
    global _resolved_ux_chatter_base_url, _resolved_ux_chatter_model_id
    b = (base_url or "").strip()
    m = (model_id or "").strip()
    _resolved_ux_chatter_base_url = b or None
    _resolved_ux_chatter_model_id = m or None


def clear_resolved_ux_chatter_model_id() -> None:
    global _resolved_ux_chatter_base_url, _resolved_ux_chatter_model_id
    _resolved_ux_chatter_base_url = None
    _resolved_ux_chatter_model_id = None


@dataclass(frozen=True)
class UxChatterRuntime:
    base_url: str
    model: str
    timeout_s: float
    max_tokens: int
    wit_max_chars: int
    cooldown_s: float
    prompt_tail_chars: int


@dataclass(frozen=True)
class UxChatterLineResult:
    """
    One UX chatter request outcome (for tests, cached wit, and the violet Downlink line).

    ``result`` is set when ``POST /v1/chat/completions`` ran; ``skip`` is set when it did not
    (empty STT line or llama.cpp unreachable).
    """

    wit: str
    result: LlamaCppChatResult | None
    skip: str | None  # "empty_transcript" | "unreachable" | None


def _env_disables_ux() -> bool:
    v = (os.environ.get("VOXIUM_UX_CHATTER", "") or "").strip().lower()
    return v in ("0", "false", "no", "off")


def ux_chatter_runtime_from_config(
    file_config: dict[str, Any] | None,
) -> UxChatterRuntime:
    raw = (file_config or {}).get("ux_chatter")
    uxc: dict[str, Any] = raw if isinstance(raw, dict) else {}
    server = (file_config or {}).get("server")
    server_cfg: dict[str, Any] = server if isinstance(server, dict) else {}
    transcription = (file_config or {}).get("transcription")
    transcription_cfg: dict[str, Any] = (
        transcription if isinstance(transcription, dict) else {}
    )
    base_url = (
        _resolved_ux_chatter_base_url
        or str(uxc.get("base_url") or "").strip()
        or str(server_cfg.get("llama_cpp_url") or "").strip()
        or _SHARED_LLAMA_CPP_URL_DEFAULT
    )
    base_url = base_url or _SHARED_LLAMA_CPP_URL_DEFAULT
    if _resolved_ux_chatter_model_id:
        model = _resolved_ux_chatter_model_id
    else:
        model = str(uxc.get("model") or "").strip()
        if not model:
            model = str(
                transcription_cfg.get("polish_model") or POLISH_DEFAULT_MODEL
            ).strip()
        if not model or model == POLISH_DEFAULT_MODEL:
            model = DEFAULT_TRUSTED_POLISH_MODEL_ID
    return UxChatterRuntime(
        base_url=base_url,
        model=model,
        timeout_s=float(uxc.get("timeout_s") or 0.45),
        max_tokens=int(uxc.get("max_tokens") or 42),
        wit_max_chars=int(uxc.get("wit_max_chars") or 72),
        cooldown_s=float(uxc.get("cooldown_s") or 4.0),
        prompt_tail_chars=int(uxc.get("prompt_tail_chars") or 320),
    )


def is_ux_chatter_wanted(
    *, cli_enabled: bool, file_config: dict[str, Any] | None
) -> bool:
    if _env_disables_ux():
        return False
    if not cli_enabled:
        return False
    return True


def fetch_ux_startup_tagline(
    file_config: dict[str, Any] | None,
    *,
    cli_enabled: bool = True,
) -> str | None:
    """
    When UX chatter (Gemma) is enabled, one optional LLM line for :func:`voxium.startup_banner.show_startup_banner`.
    Returns ``None`` to keep the static ``random.choice`` tagline pool. Never raises.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    timeout = min(2.8, max(0.9, float(rt.timeout_s) + 1.2))
    ok, _ = llama_cpp_reachable(
        rt.base_url, timeout=min(0.65, max(0.35, float(rt.timeout_s)))
    )
    if not ok:
        return None
    try:
        res = llama_cpp_chat_completions(
            rt.base_url,
            rt.model,
            [
                {"role": "system", "content": system_message_ux_banner()},
                {"role": "user", "content": user_message_ux_banner()},
            ],
            timeout=timeout,
            temperature=0.5,
            max_tokens=80,
        )
    except Exception:
        return None
    polish_profile.record("banner", model=rt.model, result=res)
    if not res.ok or not (res.text or "").strip():
        return None
    s = _normalize_wit((res.text or "").strip(), max_chars=160)
    if s and ux_output_likely_parrots_any_ux_prompt(output=s):
        return None
    return s or None


def fetch_ux_rig_subtitle(
    file_config: dict[str, Any] | None,
    hostname: str,
    *,
    cli_enabled: bool = True,
) -> str | None:
    """
    Italic subtitle under the "Voxium" title (hostname + 1960s rig flavor). See
    :func:`voxium.startup_banner.default_rig_subtitle` for the static path.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    timeout = min(2.5, max(0.85, float(rt.timeout_s) + 1.0))
    ok, _ = llama_cpp_reachable(
        rt.base_url, timeout=min(0.6, max(0.35, float(rt.timeout_s)))
    )
    if not ok:
        return None
    h = (hostname or "").strip() or "localhost"
    try:
        res = llama_cpp_chat_completions(
            rt.base_url,
            rt.model,
            [
                {"role": "system", "content": system_message_ux_rig_subtitle()},
                {"role": "user", "content": user_message_ux_rig_subtitle(h)},
            ],
            timeout=timeout,
            temperature=0.55,
            max_tokens=88,
        )
    except Exception:
        return None
    polish_profile.record("rig_subtitle", model=rt.model, result=res)
    if not res.ok or not (res.text or "").strip():
        return None
    s = _normalize_wit((res.text or "").strip(), max_chars=120)
    if not s:
        return None
    if ux_output_likely_parrots_any_ux_prompt(output=s):
        return None
    if "rig" not in s.lower():
        s = f"Rig on station  ·  {s}"[:120].rstrip()
    if h and h not in s:
        s = f"{s}  ·  {h}"[:120].rstrip()
    return s


def fetch_ux_log_subtitle(
    file_config: dict[str, Any] | None,
    transcript: str,
    *,
    cli_enabled: bool = True,
) -> str | None:
    """
    Dim one-line footer for the blue PTT/VOX transcription :class:`rich.panel.Panel` in
    :func:`voxium.app.log_transcription_summary`, seeded by the (post-polish) transcript. Returns
    ``None`` to use the static default. Never raises.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    t0 = (transcript or "").strip()
    if not t0:
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    timeout = min(1.65, max(0.85, float(rt.timeout_s) + 0.75))
    ok, _ = llama_cpp_reachable(
        rt.base_url, timeout=min(0.55, max(0.3, float(rt.timeout_s)))
    )
    if not ok:
        return None
    try:
        res = llama_cpp_chat_completions(
            rt.base_url,
            rt.model,
            [
                {"role": "system", "content": system_message_ux_log_subtitle()},
                {"role": "user", "content": user_message_ux_log_subtitle(t0)},
            ],
            timeout=timeout,
            temperature=0.42,
            max_tokens=72,
        )
    except Exception:
        return None
    polish_profile.record("log_subtitle", model=rt.model, result=res)
    if not res.ok or not (res.text or "").strip():
        return None
    s = _normalize_wit((res.text or "").strip(), max_chars=100)
    if s and (
        ux_output_likely_echoes_seed(output=s, seed=t0)
        or ux_output_likely_parrots_log_subtitle_user_prompt(output=s)
        or ux_output_likely_parrots_any_ux_prompt(output=s)
    ):
        s = _normalize_wit(
            _pick_ux_deterministic(_UX_ECHO_FALLBACK_LOG_SUBS, f"{t0}|uxlog"),
            max_chars=100,
        )
    return s or None


def _shutdown_line_has_wit(line: str) -> bool:
    body = re.sub(r"^voxium:\s*", "", line.strip(), flags=re.IGNORECASE)
    body = body.strip(" \t\r\n.:-—–")
    if not body:
        return False
    compact = re.sub(r"[\s()._`'\"-]+", "", body).lower()
    if compact in {"ctrlc", "controlc", "sigint", "keyboardinterrupt"}:
        return False
    return any(ch.isalpha() for ch in body) and len(body) >= 12


def _normalize_shutdown_line(raw: str) -> str | None:
    # Keep in sync with :data:`voxium.app._VOXIUM_SHUTDOWN_DEFAULT` (static Ctrl+C line).
    static = "Voxium: 73 / 10-7 — going clear, copy."
    s = (raw or "").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return static
    s = s[:130].rstrip()
    if s.lower().startswith("voxium:"):
        line = s if len(s) < 200 else s[:199] + "…"
    else:
        line = f"Voxium: {s}"
    return line if _shutdown_line_has_wit(line) else None


def fetch_ux_shutdown_line(
    file_config: dict[str, Any] | None,
    *,
    cli_enabled: bool = True,
) -> str | None:
    """
    One optional Gemma line for clean Ctrl+C exit (replaces the static *going clear* message).
    Returns ``None`` to use the default. Never raises.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    # Allow a bit more wall time than the hot path; sign-off runs once at exit and the UX stack
    # must still be up (see :func:`voxium.app.run_client` — farewell before ``cleanup_client_runtime``).
    timeout = min(2.6, max(0.85, float(rt.timeout_s) + 0.85))
    ok, _ = llama_cpp_reachable(
        rt.base_url, timeout=min(0.65, max(0.3, float(rt.timeout_s)))
    )
    if not ok:
        return None
    try:
        res = llama_cpp_chat_completions(
            rt.base_url,
            rt.model,
            [
                {"role": "system", "content": system_message_ux_shutdown()},
                {"role": "user", "content": user_message_ux_shutdown()},
            ],
            timeout=timeout,
            temperature=0.4,
            max_tokens=64,
        )
    except Exception:
        return None
    polish_profile.record("shutdown", model=rt.model, result=res)
    if not res.ok or not (res.text or "").strip():
        return None
    s = _normalize_shutdown_line((res.text or "").strip())
    if s and ux_output_likely_parrots_any_ux_prompt(output=s):
        return None
    return s


def get_ux_chatter_wit() -> str:
    """Last **standby** line (green on-station block), not the COPY readback line."""
    with _wit_lock:
        return _cached_wit


def clear_ux_chatter_wit() -> None:
    clear_resolved_ux_chatter_model_id()
    global _cached_wit
    with _wit_lock:
        _cached_wit = ""


def _tail_transcript(t: str, n: int) -> str:
    t = (t or "").strip()
    if n <= 0 or len(t) <= n:
        return t
    return "…" + t[-n:].lstrip()


def _normalize_wit(
    s: str,
    *,
    max_chars: int,
) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ")
    # UX copy should never surface markdown emphasis tokens from prompt leakage.
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" `\"'“”*•-—")
    if not s:
        return ""
    if 0 < max_chars < len(s):
        s = s[: max(0, max_chars - 1)].rstrip() + "…"
    return s


def request_ux_chatter_line_full(
    runtime: UxChatterRuntime,
    transcript: str,
    *,
    purpose: Literal["copy", "standby"] = "copy",
) -> UxChatterLineResult:
    """
    Synchronous request (for tests and Downlink). ``purpose`` selects the **readback** (COPY row) vs
    **standby** (green block) prompt; wit may be ``""`` when the model returns nothing useful.
    """
    if not (transcript or "").strip():
        return UxChatterLineResult("", None, "empty_transcript")
    ok, _ = llama_cpp_reachable(runtime.base_url, timeout=min(0.35, runtime.timeout_s))
    if not ok:
        return UxChatterLineResult("", None, "unreachable")
    tail = _tail_transcript(
        (transcript or "").strip(),
        int(runtime.prompt_tail_chars),
    )
    if purpose == "standby":
        user = user_message_ux_chatter_standby(tail)
        system = system_message_ux_chatter_standby()
        fb_seed = f"{tail}\n|standby|"
    else:
        user = user_message_ux_chatter_copy(tail)
        system = system_message_ux_chatter_copy()
        fb_seed = tail
    res = llama_cpp_chat_completions(
        runtime.base_url,
        runtime.model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        timeout=runtime.timeout_s,
        temperature=0.55,
        max_tokens=max(8, int(runtime.max_tokens)),
    )
    polish_profile.record(
        "chatter_standby" if purpose == "standby" else "chatter_copy",
        model=runtime.model,
        result=res,
    )
    if not res.ok or not (res.text or "").strip():
        if res.error and _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug("ux chatter: no line (%s)", res.error[:120])
        w = (
            _normalize_wit((res.text or ""), max_chars=int(runtime.wit_max_chars))
            if res.ok
            else ""
        )
        return UxChatterLineResult(w, res, None)
    w = _normalize_wit(res.text, max_chars=int(runtime.wit_max_chars))
    if w and (
        ux_output_likely_echoes_seed(output=w, seed=tail)
        or ux_output_likely_parrots_any_ux_prompt(output=w)
    ):
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "ux chatter: model line echoed STT or prompt; using on-brand fallback"
            )
        w = _normalize_wit(
            _pick_ux_deterministic(_UX_ECHO_FALLBACK_WITS, fb_seed),
            max_chars=int(runtime.wit_max_chars),
        )
    return UxChatterLineResult(w, res, None)


def request_ux_chatter_edge_line_full(
    runtime: UxChatterRuntime,
    *,
    rexmit: bool = False,
) -> UxChatterLineResult:
    """
    Synchronous request for the **EDGE INFERENCE** second line (no STT text yet; blocks until done).
    """
    ok, _ = llama_cpp_reachable(runtime.base_url, timeout=min(0.35, runtime.timeout_s))
    if not ok:
        return UxChatterLineResult("", None, "unreachable")
    edge_timeout = max(0.6, min(1.25, float(runtime.timeout_s) * 1.5))
    res = llama_cpp_chat_completions(
        runtime.base_url,
        runtime.model,
        [
            {"role": "system", "content": system_message_ux_edge_inference()},
            {"role": "user", "content": user_message_ux_edge_inference(rexmit=rexmit)},
        ],
        timeout=edge_timeout,
        temperature=0.55,
        max_tokens=max(8, int(runtime.max_tokens)),
    )
    polish_profile.record("edge_inference", model=runtime.model, result=res)
    if not res.ok or not (res.text or "").strip():
        if res.error and _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug("ux chatter edge: no line (%s)", res.error[:120])
        w = (
            _normalize_wit((res.text or ""), max_chars=int(runtime.wit_max_chars))
            if res.ok
            else ""
        )
        return UxChatterLineResult(w, res, None)
    w = _normalize_wit(res.text, max_chars=int(runtime.wit_max_chars))
    if w and (
        ux_output_likely_parrots_edge_inference_user_prompt(output=w)
        or ux_output_too_generic_for_edge_inference(output=w)
    ):
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "ux chatter edge: line looked generic or echoed instructions; using on-brand fallback"
            )
        w = _normalize_wit(
            _pick_ux_deterministic(
                _UX_EDGE_ECHO_FALLBACK,
                f"edge|{int(rexmit)}|{res.text!s}"[:200],
            ),
            max_chars=int(runtime.wit_max_chars),
        )
    return UxChatterLineResult(w, res, None)


def fetch_ux_edge_status_detail(
    file_config: dict[str, Any] | None,
    *,
    cli_enabled: bool,
    rexmit: bool = False,
) -> str | None:
    """
    When UX chatter is on, one LLM line for the **EDGE INFERENCE** status detail; ``None`` to use
    :func:`voxium.radio_readback.take_edge_inference_detail` (or re-xmit variant). Never raises.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    full = request_ux_chatter_edge_line_full(rt, rexmit=rexmit)
    w = (full.wit or "").strip()
    return w if w else None


def request_ux_chatter_line(
    runtime: UxChatterRuntime,
    transcript: str,
) -> str:
    """
    Synchronous request (for tests). Returns normalized **copy**-role wit or "" on any failure.
    """
    return request_ux_chatter_line_full(runtime, transcript, purpose="copy").wit


def format_ux_chatter_downlink_line(
    runtime: UxChatterRuntime,
    line_result: UxChatterLineResult,
) -> tuple[str, str] | None:
    """
    One Downlink line for the experience (Gemma) path: model, timing, tokens — **no** transcript
    text. Returns ``None`` when nothing should be printed (e.g. empty STT line).
    """
    if line_result.skip == "empty_transcript":
        return None
    m = (runtime.model or "—").strip() or "—"
    if line_result.skip == "unreachable":
        return (
            f"Experience: {m} — llama.cpp not on station (static wit, copy).",
            "warning",
        )
    res = line_result.result
    if res is None:
        return None
    pol = {
        "tokens_in": res.prompt_tokens,
        "tokens_out": res.completion_tokens,
        "total_tokens": res.total_tokens,
    }
    tok = format_polish_usage_suffix(pol)
    try:
        tail = f"{float(res.seconds):.2f}s"
    except (TypeError, ValueError):
        tail = "n/a"
    if not res.ok:
        err = (res.error or "error")[:200]
        return (f"Experience: {m} · {tail} — {err} (static wit, copy).", "warning")
    wit = (line_result.wit or "").strip()
    if wit:
        return (
            f"Experience: {m} · {tail} · line ready{tok} (local wit, copy).",
            "info",
        )
    return (
        f"Experience: {m} · {tail} — empty line{tok} (static wit, copy).",
        "warning",
    )


def sync_ux_chatter_for_transcript(
    transcript: str,
    file_config: dict[str, Any] | None,
    cli_enabled: bool,
    *,
    on_complete: Any = None,
) -> UxChatterLineResult | None:
    """
    Two synchronous :func:`request_ux_chatter_line_full` calls: **copy** (readback for the green
    **PTT/VOX · COPY** row) and **standby** (``_cached_wit`` for the on-station block). The return
    value is the **copy** result; Downlink uses that pass for metrics. Replaces
    :mod:`voxium.radio_readback` for COPY when a copy wit is returned.
    """
    if not is_ux_chatter_wanted(cli_enabled=cli_enabled, file_config=file_config):
        return None
    t = (transcript or "").strip()
    if not t:
        return None
    rt = ux_chatter_runtime_from_config(file_config)
    full_copy = request_ux_chatter_line_full(rt, t, purpose="copy")
    full_standby = request_ux_chatter_line_full(rt, t, purpose="standby")
    sw = (full_standby.wit or "").strip()
    with _wit_lock:
        global _cached_wit
        if sw:
            _cached_wit = sw
    if on_complete is not None:
        try:
            on_complete(full_copy, rt)
        except Exception:
            _LOG.debug("ux chatter on_complete (sync) failed", exc_info=True)
    return full_copy
