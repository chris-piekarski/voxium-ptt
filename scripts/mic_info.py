#!/usr/bin/env python3
"""
Print default microphone / PortAudio settings and a table of all audio devices.

Run via: make mic-info  (uses the project venv) or: .venv/bin/python scripts/mic_info.py
"""

from __future__ import annotations

import platform
import sys


def _fmt_ch(n: int) -> str:
    if n == 0:
        return "—"
    return str(n)


def main() -> int:
    try:
        import sounddevice as sd
    except ImportError as e:
        print(
            "Could not import sounddevice (used for microphone access).",
            file=sys.stderr,
        )
        print("Install the project in a venv:  make install", file=sys.stderr)
        print(f"  ({e})", file=sys.stderr)
        return 1
    except OSError as e:
        print(
            "sounddevice could not load PortAudio (the audio I/O library).",
            file=sys.stderr,
        )
        print(
            "On many Linux systems you need:  libportaudio2  (e.g. sudo apt install portaudio19-dev or libportaudio2)",
            file=sys.stderr,
        )
        print(
            "Then reinstall / rebuild python-sounddevice in your venv if needed.",
            file=sys.stderr,
        )
        print(f"  ({e})", file=sys.stderr)
        return 1

    print("Voxium — mic check / audio path (PortAudio · PTT needs a clear input)")
    print()
    print(f"Platform:     {platform.platform()}")
    try:
        print(f"Python:       {sys.version.split()[0]} ({sys.executable})")
    except OSError:
        print(f"Python:       {sys.version.split()[0]}")

    ver = getattr(sd, "__version__", "?")
    print(f"sounddevice:  {ver}")
    try:
        pa = sd.get_portaudio_version()
        if isinstance(pa, tuple) and len(pa) >= 2:
            print(f"PortAudio:    {pa[0]}\n              {pa[1]}")
        else:
            print(f"PortAudio:    {pa!r}")
    except Exception as ex:
        print(f"PortAudio:    (could not read version: {ex})")
    print()

    try:
        ha_raw = sd.query_hostapis()
    except Exception as ex:
        print(f"Host APIs: (unavailable) — {ex}\n", file=sys.stderr)
        hostapis = []
    else:
        if isinstance(ha_raw, dict):
            hostapis = [ha_raw]
        else:
            try:
                hostapis = [ha_raw[i] for i in range(len(ha_raw))]  # type: ignore[index]
            except Exception:
                hostapis = list(ha_raw) if ha_raw else []
    names_by_id = {
        h["index"]: h["name"] for h in hostapis if isinstance(h, dict) and "index" in h
    }

    default_in: int | None = None
    default_out: int | None = None
    try:
        di = sd.query_devices(kind="input")
        if isinstance(di, dict) and "index" in di:
            default_in = int(di["index"])
    except Exception as ex:
        print(f"Default input device: (unavailable) — {ex}\n")
    else:
        print("Default input device (used when kind='input')")
        if default_in is not None and isinstance(di, dict):
            print(f"  index {default_in} — {di.get('name', '?')!s}")
        else:
            print("  (not resolved)")
        print()

    try:
        d_out = sd.query_devices(kind="output")
        if isinstance(d_out, dict) and "index" in d_out:
            default_out = int(d_out["index"])
    except Exception:
        d_out = None
    if default_out is not None:
        print("Default output device (used when kind='output')")
        if isinstance(d_out, dict):
            print(f"  index {default_out} — {d_out.get('name', '?')!s}")
        print()

    if hostapis:
        print("Host APIs (backends PortAudio can use for this run)")
        for h in hostapis:
            ddi = h.get("default_input_device", -1)
            ddo = h.get("default_output_device", -1)
            print(
                f"  [{h['index']}] {h.get('name', '?')!s} — "
                f"default in #{ddi}, out #{ddo} — {h.get('device_count', 0)} devices"
            )
        print()

    def _collect_devices() -> list[dict]:
        try:
            raw = sd.query_devices()
        except Exception as ex:
            raise RuntimeError(str(ex)) from ex
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], dict):
            return [d for d in raw if isinstance(d, dict)]
        out: list[dict] = []
        i = 0
        while True:
            try:
                d = sd.query_devices(i)
            except (ValueError, OSError, IndexError):
                break
            if isinstance(d, dict):
                out.append(d)
            i += 1
            if i > 256:
                break
        return out

    try:
        devs = _collect_devices()
    except RuntimeError as ex:
        print(f"Could not query devices: {ex}", file=sys.stderr)
        return 1
    if not devs:
        print("No audio devices were returned by PortAudio.", file=sys.stderr)
        return 1

    w_idx = 4
    w_in = 4
    w_out = 4
    w_sr = 7
    w_ha = 12
    nam_w = 40

    def row(
        mark: str,
        idx: str,
        ins: str,
        outs: str,
        sr: str,
        ha: str,
        name: str,
    ) -> str:
        return (
            mark
            + idx.rjust(w_idx)
            + "  "
            + ins.rjust(w_in)
            + "  "
            + outs.rjust(w_out)
            + "  "
            + sr.ljust(w_sr)
            + "  "
            + ha.ljust(w_ha)
            + "  "
            + name
        )

    print("All devices  (in/out = max input / output channel count; — = none)")
    print(row("", "idx", "in", "out", "Hz (def)", "host API", "name"))
    print("-" * (1 + w_idx + 2 + w_in + 2 + w_out + 2 + w_sr + 2 + w_ha + 2 + nam_w))

    for d in devs:
        if not isinstance(d, dict) or "index" not in d:
            continue
        i = int(d["index"])
        ha_i = d.get("hostapi")
        ha_n = "?"
        if ha_i is not None:
            try:
                ha_n = str(names_by_id.get(int(ha_i), f"api[{ha_i}]"))
            except (TypeError, ValueError):
                ha_n = str(ha_i)

        m_in = d.get("max_input_channels", 0) or 0
        m_out = d.get("max_output_channels", 0) or 0
        sr = d.get("default_samplerate")
        sr_s = (
            f"{int(sr)}"
            if isinstance(sr, (int, float)) and sr == int(sr)
            else (f"{sr}" if sr is not None else "?")
        )

        mark = (
            "* "
            if (default_in is not None and i == default_in)
            or (default_out is not None and i == default_out)
            else "  "
        )

        nm = str(d.get("name", "?"))[:200]
        print(
            row(
                mark,
                str(i),
                _fmt_ch(m_in),
                _fmt_ch(m_out),
                sr_s,
                ha_n[:w_ha],
                nm,
            )
        )

    print()
    if default_in is not None or default_out is not None:
        print(
            "Rows marked with * are the current default input and/or default output device."
        )
    print(
        "Note: the actual list depends on the OS, drivers, and whether PulseAudio / PipeWire / JACK, etc. are in use (Linux), or the default sound control panel (Windows, macOS)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
