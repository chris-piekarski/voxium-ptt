#!/usr/bin/env python3
"""
Manual /transcribe-stream harness — feeds synthetic float32 PCM into the WebSocket
endpoint of a running ``voxium server`` instance and prints partial / final frames.

Usage::

    python tools/stream_test.py
    python tools/stream_test.py --url ws://127.0.0.1:8002/transcribe-stream
    python tools/stream_test.py --duration 6.0 --chunk-ms 250
    python tools/stream_test.py --tone 440 --duration 3 --silence

Phase 1 dev tool (see ``docs/plans/live-transcribe-stream.md`` §8). Not part of
``make test`` — run by hand against a live server. Requires ``websocket-client``,
which is in the project dev extras.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

import numpy as np

try:
    from websocket import WebSocket, WebSocketException, create_connection
except ImportError:
    sys.stderr.write(
        "Voxium: websocket-client is required. Install via `pip install -e .[dev]` "
        "or `pip install websocket-client`.\n"
    )
    sys.exit(2)

SAMPLE_RATE = 16_000


@dataclass
class _Args:
    url: str
    duration: float
    chunk_ms: int
    tone: float
    silence: bool


def _parse(argv: list[str] | None = None) -> _Args:
    parser = argparse.ArgumentParser(
        description="Manual /transcribe-stream WebSocket harness for Voxium."
    )
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8002/transcribe-stream",
        help="WebSocket URL (default: ws://127.0.0.1:8002/transcribe-stream)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=6.0,
        help="Total seconds of audio to send (default: 6.0)",
    )
    parser.add_argument(
        "--chunk-ms",
        type=int,
        default=250,
        help="Chunk size in milliseconds (default: 250)",
    )
    parser.add_argument(
        "--tone",
        type=float,
        default=0.0,
        help="If > 0, send a sine wave at this frequency (Hz) instead of silence.",
    )
    parser.add_argument(
        "--silence",
        action="store_true",
        help="Force silence even when --tone is specified.",
    )
    args = parser.parse_args(argv)
    return _Args(
        url=args.url,
        duration=float(args.duration),
        chunk_ms=int(args.chunk_ms),
        tone=float(args.tone),
        silence=bool(args.silence),
    )


def _make_audio(seconds: float, tone_hz: float, silence: bool) -> np.ndarray:
    samples = int(seconds * SAMPLE_RATE)
    if tone_hz > 0 and not silence:
        t = np.arange(samples, dtype=np.float32) / SAMPLE_RATE
        return (0.2 * np.sin(2.0 * np.pi * tone_hz * t)).astype(np.float32)
    return np.zeros(samples, dtype=np.float32)


def _print_msg(prefix: str, payload: dict) -> None:
    kind = payload.get("type", "?")
    if kind == "partial":
        seq = payload.get("seq")
        text = payload.get("text") or ""
        secs = payload.get("audio_seconds")
        decode_ms = payload.get("decode_ms")
        is_final = payload.get("is_final")
        suppressed = payload.get("suppressed")
        flag = " FINAL" if is_final else ""
        sup = " SUPPR" if suppressed else ""
        print(
            f"{prefix} partial seq={seq} audio={secs}s decode={decode_ms}ms{flag}{sup}"
            f" text={text!r}"
        )
    elif kind == "session_open":
        print(f"{prefix} session_open {json.dumps(payload, indent=2)}")
    elif kind == "keepalive":
        print(f"{prefix} keepalive session_seconds={payload.get('session_seconds')}")
    elif kind == "error":
        print(f"{prefix} ERROR code={payload.get('code')} msg={payload.get('message')}")
    else:
        print(f"{prefix} {payload}")


def _drain_until_final(ws: WebSocket, *, deadline_s: float) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            ws.settimeout(max(0.1, end - time.monotonic()))
            raw = ws.recv()
        except WebSocketException:
            return
        if not raw:
            return
        try:
            payload = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError:
            continue
        if payload is None:
            continue
        _print_msg("<<", payload)
        if payload.get("type") == "partial" and payload.get("is_final"):
            return
        if payload.get("type") == "error":
            return


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    audio = _make_audio(args.duration, args.tone, args.silence)
    chunk_samples = int(SAMPLE_RATE * args.chunk_ms / 1000.0)
    if chunk_samples <= 0:
        sys.stderr.write("chunk-ms too small; nothing to send\n")
        return 2
    print(
        f">> connecting {args.url}  duration={args.duration}s "
        f"chunk={args.chunk_ms}ms tone={args.tone}Hz silence={args.silence}"
    )
    try:
        ws = create_connection(args.url, timeout=2.0)
    except (OSError, WebSocketException) as exc:
        sys.stderr.write(f"connect failed: {exc}\n")
        return 1

    try:
        # Read session_open
        try:
            raw = ws.recv()
            if not isinstance(raw, str):
                sys.stderr.write(
                    f"expected text session_open frame, got {type(raw).__name__}\n"
                )
                return 1
            opened = json.loads(raw)
            _print_msg("<<", opened)
        except (json.JSONDecodeError, WebSocketException) as exc:
            sys.stderr.write(f"failed to read session_open: {exc}\n")
            return 1

        # Stream audio at near-real-time pacing
        cursor = 0
        period = args.chunk_ms / 1000.0
        while cursor < audio.size:
            end_idx = min(cursor + chunk_samples, audio.size)
            chunk = np.ascontiguousarray(audio[cursor:end_idx], dtype=np.float32)
            try:
                ws.send_binary(chunk.tobytes())
                print(f">> sent {chunk.size} samples ({chunk.size / SAMPLE_RATE:.2f}s)")
            except WebSocketException as exc:
                sys.stderr.write(f"send failed: {exc}\n")
                break
            cursor = end_idx
            # Drain whatever the server has sent so far before the next chunk.
            ws.settimeout(0.05)
            try:
                while True:
                    raw = ws.recv()
                    if not raw:
                        break
                    payload = (
                        json.loads(raw) if isinstance(raw, str) else {"type": "binary"}
                    )
                    _print_msg("<<", payload)
            except WebSocketException:
                pass
            time.sleep(period)

        # Flush
        try:
            ws.send(json.dumps({"type": "end"}))
            print(">> sent end")
        except WebSocketException as exc:
            sys.stderr.write(f"end-send failed: {exc}\n")
            return 1
        _drain_until_final(ws, deadline_s=5.0)
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
