# Voxium 0.0.1

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) [![Version](https://img.shields.io/badge/Version-0.0.1-555555)](./pyproject.toml) [![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#what-you-need) [![Operator documentation](https://img.shields.io/badge/Operator%20docs-0.0.1-0A66C2?logo=markdown&logoColor=white)](./docs/README.md) [![Mermaid](https://img.shields.io/badge/Mermaid-diagrams-ff3670?logo=mermaid&logoColor=white)](./docs/README.md#diagram-index)

**PTT (push-to-talk) voice typing for your terminal** — **VOX** in, text out, over a local loopback to [Systran faster-whisper](https://github.com/SYSTRAN/faster-whisper). No cloud in the product path.

It is the same muscle memory as a **radio key**: short transmissions, then **copy** to the screen. Under the hood you are **stacking** mic, CPU/GPU, and model in a very **Apollo** way: humans at the key, **robot** work in the inference path, **uncharted** only in the sense of *your* machine’s first clean run. Press a hotkey, speak, press again: text is pasted where you are typing. Default path is **GPU (CUDA)**; use `--server-device cpu` if you have no working CUDA stack. **Brand story (radio + space-race tone):** [docs/brand.md](docs/brand.md).

## What you need

- **Python 3.10+**
- **GPU (recommended):** A working CUDA setup for ctranslate2 on your OS. On Windows, install the [NVIDIA CUDA Toolkit](https://developer.nvidia.com/cuda-downloads) (12.x) so `cublas` / related DLLs load; or run with `--server-device cpu`.
- **Linux (X11):** `xdotool` and `xclip` for pasting; PortAudio for recording (e.g. `portaudio19-dev` on Debian/Ubuntu). **Wayland:** may need an X11 session or extra setup for global hotkeys.
- **macOS:** PortAudio (e.g. `brew install portaudio`); accessibility permissions for the terminal for hotkey capture.
- **Windows:** Default recording device in Sound settings; for GPU, ensure CUDA libraries match your install.

## Install (GNU Make, Ubuntu / WSL / Linux)

The [Makefile](Makefile) targets only **Linux-style** venvs (`.venv/bin/...`) and are intended for **GNU make** on the command line (e.g. Ubuntu, Debian, WSL2). From the repository root:

```bash
make help          # list targets
make install       # venv + pip install -e .  (installs .venv/bin/voxium)
make start         # same as: .venv/bin/voxium run
```

**Developers:** `make install-dev`, `make lint`, `make test`, `make test-cov` (coverage gate; see [docs/testing.md](docs/testing.md)). Full documentation, diagrams, and package layout: **[docs/README.md](docs/README.md)**.

**Optional:** `make start TT_ARGS="--server-device cpu"` passes extra flags to `voxium run` (same as typing them after `voxium run`).

**Repo snapshot:** `make repo-stats` regenerates [docs/repository-stats.md](docs/repository-stats.md) (line counts and Mermaid pie charts; uses system `python3`, not the venv).

**Cleanup:** `make clean` (tool caches, `__pycache__`, coverage, root `*.egg-info`; keeps `.venv` and `.dev-install-stamp`), `make uninstall` (removes `.venv`, `.dev-install-stamp`, root `*.egg-info`), `make disk-usage` (size of those three data directories).

## Windows (PowerShell; no Make)

On Windows, **do not rely on the Makefile** — use a venv, then the **`voxium`** entry point. After `pip install -e .` and `pip install -e ".[dev]"` for development:

| Linux `make` target | Equivalent (venv activated) |
|---------------------|----------------------------|
| `make start` | `voxium run` (or `voxium`; same default) |
| `make start` with extra flags | `voxium run --your-flags` |
| `make lint` | `python -m ruff check .` |
| `make test` | `python -m pytest tests` |
| `make test-cov` | run `pytest` with the same options as in [docs/testing.md](docs/testing.md) and `pyproject.toml` (pytest-cov) |
| `make repo-stats` | `python3 scripts/generate_repo_stats.py` to refresh [docs/repository-stats.md](docs/repository-stats.md) |
| `make disk-usage` | show sizes of `models/`, `logs/` under the repo (or use your shell’s `du`) |

**Convenience (from a clone):** `scripts\windows\venv_bootstrap.cmd` creates `.venv` and editable-installs Voxium. After that, `scripts\windows\Voxium.cmd` (or `Voxium.ps1` with `ExecutionPolicy` bypass) runs `voxium`. The app sets a short window/tab title (default matches `VOXIUM_WINDOW_TITLE` in `voxium/app.py`; override with environment variable `VOXIUM_WINDOW_TITLE` before launch if needed).

## Install (pip, no Make)

```bash
python3 -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e .
voxium run        # or: voxium  /  python -m voxium
```

Install **Linux** packages for paste + audio as above before running. On **Windows**, see the table above for dev commands.

## Config

Optional: **`%USERPROFILE%\.config\voxium\config.yaml`** on Windows, or **`~/.config/voxium/config.yaml`** elsewhere.

If the file is missing, Voxium uses CLI defaults (see `voxium run --help`). Example:

```yaml
transcription:
  model: base
  language: null
server:
  device: cuda
  compute: float16
hotkeys:
  record: f9
  recovery: f8
  retry: f7
history:
  limit: 100
  max_total_chars: 512000
  pending_audio_max_mib: 32
```

## Run

```bash
voxium                 # same as: voxium run
voxium run --help
voxium -v --log-level DEBUG
voxium --server-device cpu
```

- **F9 (default):** start/stop recording and transcribe; **F8:** cycle replay of PTT/VOX transcripts from this run (re-paste, newest first, wraps); **F7:** re-transmit (re-run transcription on the last in-RAM capture when available).
- **Transcript log:** session-only, in process RAM (bounded: `--history-limit`, `--history-max-chars`); use **`/history`** in the downlink to list, **`/history <n>`** to expand, **`/history copy <n>`** to put one line on the system clipboard.
- **Local data (same paths on all platforms):** `models/` (Hugging Face downloads for faster-whisper), `logs/` (server log, client lock). Override the project root with `VOXIUM_REPO_ROOT` if needed. The server log defaults to `logs/voxium_server.log` (or `--server-log-file`).
- **Health:** `voxium health`, `voxium stats`; foreground server: `voxium server --help`.

Voxium only uses a **local loopback** HTTP server for transcription.

## Model list

`voxium models` (or `voxium models --json`). Voxium only allows the trusted Systran model names shipped in the CLI.

## Systemd (Linux, optional)

Run Voxium on login with a user unit — point `ExecStart` at your venv’s `voxium` with the `run` subcommand (e.g. `/path/to/.venv/bin/voxium run`), set `Environment=DISPLAY=:0` for graphical paste, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voxium
```

## Troubleshooting

- **`ModuleNotFoundError: No module named 'voxium'`** (including when running `voxium` or `python -m voxium`): the package is not installed into that Python environment. From the **repository root** (the folder that contains `pyproject.toml` and `src/voxium/`), install the project in editable mode (PowerShell or cmd):
  ```text
  .\.venv\Scripts\python -m pip install -U pip setuptools wheel
  .\.venv\Scripts\python -m pip install -e .
  ```
  Then confirm: `.\.venv\Scripts\python -c "import voxium; print(voxium.__file__)"` should print a path under `src\voxium`. If you still see the error, remove the broken install and reinstall: `.\.venv\Scripts\python -m pip uninstall voxium -y` then `.\.venv\Scripts\python -m pip install -e .` Do not run `pip` from a different working directory or a different venv than the one you use to launch `voxium`.
- **500 / transcription error:** read `logs/voxium_server.log` in the repo (or `--server-log-file`); on Windows, missing `cublas64_12.dll` usually means CUDA 12 is not on `PATH` — use `--server-device cpu` or install the CUDA 12 Toolkit `bin` directory.
- **Slow or CPU-only:** `--model tiny` or `--server-device cpu`.
- **Linux pynput / hotkeys:** X11 required for typical setups; see Wayland note above.
- **macOS:** grant Accessibility to your terminal.

## How it works

1. **pynput** — global hotkeys  
2. **sounddevice** — microphone  
3. **faster-whisper** (local HTTP worker) — transcription  
4. **pyperclip + OS tools** — paste

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) in the repository root.

## Acknowledgments

- [Systran/faster-whisper](https://github.com/SYSTRAN/faster-whisper) and CTranslate2  
- pynput, sounddevice, FastAPI, uvicorn
