"""Client-side poll of local /ensure-model for Hugging Face download progress (purple downlink). Brand: docs/brand.md."""

from __future__ import annotations

import time
from collections.abc import Callable

import requests
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from voxium.console_status import (
    DOWNLINK_MODEL_FETCH_TITLE,
    build_downlink_telemetry_panel,
    print_agent_telemetry_panel,
)
from voxium.loopback import get_server_endpoint_url, is_loopback_url

_ENSURE_POST_TIMEOUT = 30.0
_ENSURE_POLL_TIMEOUT = 7200.0
_ENSURE_POLL_INTERVAL = 0.35


def _freeze_ptt(freeze: Callable[[], None] | None) -> None:
    if freeze is not None:
        try:
            freeze()
        except Exception:
            pass


def _model_fetch_panel(
    console: Console, model: str, job_id: str, progress_line: str
) -> Panel:
    """One Downlink box, one line: model, job id, and current HF / load status (updating), copy."""
    pl = (progress_line or "…").strip().replace("\n", " ")
    line = f"Model {model!r}  ·  job {job_id}  ·  {pl}"
    if len(line) > 520:
        line = line[:517] + "…"
    return build_downlink_telemetry_panel(
        console,
        Text(line, style="dim #ddd6fe"),
        title=DOWNLINK_MODEL_FETCH_TITLE,
    )


def ensure_model_on_loopback_server(
    server_url: str,
    console: Console,
    model: str,
    *,
    freeze_for_external_output: Callable[[], None] | None = None,
) -> bool:
    """
    If the local /transcribe server is on loopback, start or join a model fetch
    and show one live Downlink panel (single progress line) while polling.

    Returns True when the model is loaded on the server (or was already), False on hard errors.
    """
    if not is_loopback_url(server_url):
        return True

    post_url = get_server_endpoint_url(server_url, "ensure-model")
    try:
        r = requests.post(
            post_url,
            json={"model": model},
            timeout=_ENSURE_POST_TIMEOUT,
        )
    except OSError as exc:
        _freeze_ptt(freeze_for_external_output)
        print_agent_telemetry_panel(
            console,
            [
                (
                    f"Could not reach the local /transcribe server for a model preflight: {exc}",
                    "warning",
                ),
                (
                    f"Session model is still {model!r} for this client — bring the server up, then PTT, copy.",
                    "info",
                ),
            ],
        )
        return False
    if r.status_code == 200:
        msg = (r.json() or {}).get(
            "message"
        ) or f"Model {model!r} is on the server stack, copy."
        _freeze_ptt(freeze_for_external_output)
        print_agent_telemetry_panel(
            console,
            [(msg, "info")],
        )
        return True
    if r.status_code != 202:
        detail: str | object = r.text
        try:
            d = r.json()
            if isinstance(d, dict) and "detail" in d:
                detail = d["detail"]
        except Exception:
            pass
        _freeze_ptt(freeze_for_external_output)
        print_agent_telemetry_panel(
            console,
            [
                (
                    f"Model preflight on the local server failed ({r.status_code}).",
                    "error",
                ),
                (str(detail)[:2000], "error"),
            ],
        )
        return False

    job_id = (r.json() or {}).get("job_id")
    if not job_id:
        _freeze_ptt(freeze_for_external_output)
        print_agent_telemetry_panel(
            console,
            [("Model preflight: server returned 202 but no job_id, copy.", "error")],
        )
        return False

    poll_url = get_server_endpoint_url(server_url, f"ensure-model/jobs/{job_id}")
    t0 = time.monotonic()
    done_ok = False

    _freeze_ptt(freeze_for_external_output)

    with Live(
        _model_fetch_panel(
            console,
            model,
            job_id,
            "Starting fetch from Hugging Face — this may take a while, copy.",
        ),
        console=console,
        refresh_per_second=6,
        transient=True,
    ) as live:
        while time.monotonic() - t0 < _ENSURE_POLL_TIMEOUT:
            try:
                pr = requests.get(poll_url, timeout=8.0)
            except OSError as exc:
                print_agent_telemetry_panel(
                    console,
                    [
                        (
                            f"Lost contact with the server during model fetch: {exc}",
                            "error",
                        ),
                    ],
                )
                return False
            if pr.status_code != 200:
                print_agent_telemetry_panel(
                    console,
                    [
                        (
                            f"Model job status read failed with HTTP {pr.status_code}, copy.",
                            "error",
                        ),
                    ],
                )
                return False
            data = pr.json() or {}
            st = str(data.get("status") or "")
            pline = str(data.get("progress_line") or "").strip()
            err = data.get("error")

            if st == "error" or err is not None:
                text = str(err or "Model load or download failed on the server.")
                if pline:
                    text = f"{text}  ·  {pline}"
                print_agent_telemetry_panel(
                    console,
                    [
                        ("Model fetch failed on the local server, copy.", "error"),
                        (text[:4000], "error"),
                    ],
                )
                return False

            if st == "ready" and data.get("done") and err is None:
                if pline:
                    live.update(_model_fetch_panel(console, model, job_id, pline))
                done_ok = True
                break

            if pline:
                live.update(_model_fetch_panel(console, model, job_id, pline))
            time.sleep(_ENSURE_POLL_INTERVAL)

    if not done_ok:
        print_agent_telemetry_panel(
            console,
            [
                (
                    "Model fetch timed out while waiting on the local server, copy.",
                    "error",
                )
            ],
        )
        return False

    print_agent_telemetry_panel(
        console,
        [
            (f"Model {model!r} is on the stack and ready for PTT, copy.", "info"),
        ],
    )
    return True
