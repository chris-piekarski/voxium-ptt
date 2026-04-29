```text
····················································································
  PTT & VOX  —  VOX in, text out on the loopback  —  you key, the stack is in the loop
····················································································

  █   █  ███  █   █  ███  █   █ █   █
  █   █ █   █ █   █   █   █   █ ██ ██
  █   █ █   █  ███    █   █   █ █ █ █
   █ █  █   █ █   █   █   █   █ █   █
    █    ███  █   █  ███   ███  █   █

····················································································
```

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

If **`.venv` was created on Windows** (it has `Scripts\` / `Lib\`, not `bin/`), `make install` will fail in WSL. Remove that folder from **Windows** or a working shell, then run `make install` again in WSL, **or** keep a separate Linux venv and point Make at it: `make install VENV=.venv-wsl` (and the same `VENV=...` for `make install-dev`, `make lint`, `make test`). The pattern `.venv-*/` is gitignored.

**Developers:** `make install-dev`, `make lint`, `make test`, `make test-cov` (coverage gate; see [docs/testing.md](docs/testing.md)). The dev install now includes **`py-spy`**, which is the preferred first-pass profiler for Voxium; see [docs/profiling.md](docs/profiling.md). Full documentation, diagrams, and package layout: **[docs/README.md](docs/README.md)**.

**Optional:** `make start TT_ARGS="--server-device cpu"` passes extra flags to `voxium run` (same as typing them after `voxium run`).

**Repo snapshot:** `make repo-stats` regenerates [docs/repository-stats.md](docs/repository-stats.md) (line counts and Mermaid pie charts; uses system `python3`, not the venv).

**Cleanup:** `make clean` (tool caches, `__pycache__`, coverage, root `*.egg-info`, and `tools/llama.cpp/` for the local polish runtime; keeps `.venv` and `.dev-install-stamp`), `make uninstall` (removes `.venv`, `.dev-install-stamp`, root `*.egg-info`), `make disk-usage` (size of `models/`, `logs/`, and `tools/llama.cpp/` under the repo).

## Windows (PowerShell; no Make)

On Windows, **do not rely on the Makefile** — use a venv, then the **`voxium`** entry point. After `pip install -e .` and `pip install -e ".[dev]"` for development (includes **`py-spy`** for profiling):

| Linux `make` target | Equivalent (venv activated) |
|---------------------|----------------------------|
| `make start` | `voxium run` (or `voxium`; same default) |
| `make start` with extra flags | `voxium run --your-flags` |
| `make lint` | `python -m black --check src tests scripts`, then `python -m ruff check .`, then `python -m mypy`, then `python -m pylint src/voxium tests scripts --recursive=y` (order matches `scripts/mk.py` `cmd_lint`) |
| `make test` | `python -m pytest tests` |
| `make test-cov` | run `pytest` with the same options as in [docs/testing.md](docs/testing.md) and `pyproject.toml` (pytest-cov) |
| `make repo-stats` | `python3 scripts/generate_repo_stats.py` to refresh [docs/repository-stats.md](docs/repository-stats.md) |
| `make disk-usage` | show sizes of `models/`, `logs/`, `tools/llama.cpp/` under the repo (or use your shell’s `du`) |

**Convenience (from a clone):** run **`scripts\windows\Setup-Voxium.cmd`** once, then start the app with **`Voxium.cmd` in the repository root** (next to `pyproject.toml`) — that file calls `scripts\windows\Voxium.cmd` with the correct folder. Setup now provisions the repo-local **`llama.cpp`** runtime and default **GGUF** polish model by default; pass **`-SkipPolish`** only if you want the STT path without the optional local polish stack. If you need a shortcut in the **parent** of the clone (e.g. `Desktop\WSL-Workspaces\` with the repo in `WSL-Workspaces\voxium\`), use **`scripts\windows\Voxium-From-Parent-Folder.cmd`** there — do **not** copy `scripts\windows\Voxium.cmd` alone; it will `cd` to the wrong directory. The app sets a short window/tab title (override with `VOXIUM_WINDOW_TITLE` if needed).

## Install (pip, no Make)

```bash
python3 -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e .
voxium run        # or: voxium  /  python -m voxium
```

**Windows (simplest):** from the repo root, run **`scripts\windows\Setup-Voxium.cmd`**. That creates the venv, installs Voxium, checks `sounddevice`, and provisions the repo-local polish runtime/model. Then run **`voxium run`** with the venv activated, or `scripts\windows\Voxium.cmd run`.

Install **Linux** packages for paste + audio as above before running. On **Windows**, see the table above for dev commands.

For maintainer profiling, prefer **`py-spy`** on the same OS as the running Voxium process; see [docs/profiling.md](docs/profiling.md).

## Config

Optional: **`%USERPROFILE%\.config\voxium\config.yaml`** on Windows, or **`~/.config/voxium/config.yaml`** elsewhere.

If the file is missing, Voxium uses CLI defaults (see `voxium run --help`). Example:

```yaml
transcription:
  model: small.en
  language: null
  polish_enabled: true
  polish_model: auto
server:
  device: cuda
  compute: float16
  # When polish is enabled, Voxium starts a local `llama-server` if needed.
  # `Setup-Voxium.cmd` or `voxium models --polish --pull-polish` provisions
  # the repo-local runtime under tools/llama.cpp and the default GGUF model under models/polish.
  llama_cpp_url: http://127.0.0.1:11435
  llama_cpp_auto_start: true
  llama_cpp_cmd: null
  llama_cpp_gpu_layers: auto
  llama_cpp_ctx_size: 0
hotkeys:
  record: f9
  recovery: f8
  retry: f6
  mode: f7
history:
  limit: 100
  max_total_chars: 512000
  pending_audio_max_mib: 32
ui:
  # Default false: ``/`` command input only when this terminal window is focused (not other apps).
  # Set true or use ``voxium run --slash-global`` to restore system-wide ``/`` like older builds.
  slash_global: false
```

## Run

```bash
voxium                 # same as: voxium run
voxium run --help
voxium -v --log-level DEBUG
voxium --server-device cpu
```

- **F9 (default):** start/stop recording and transcribe; **F7:** toggle **PTT** (push-to-talk) vs **VOX** (open mic with utterance gating); **F8:** cycle replay of transcripts; **F6 (default):** re-transmit last pending in-RAM capture.
- **`/…` command line:** with the default ``ui.slash_global: false`` (or ``--no-slash-global``), the leading ``/`` only opens the command bar when **this terminal** is the focused window (Windows and Linux X11 with ``$WINDOWID`` + ``xdotool``; **macOS** and **Wayland** best-effort may still allow ``/`` anywhere until we add a tighter check). Once the command bar is open, Voxium keeps accepting the command so brief focus-probe misses do not drop `/help` mid-type. Use ``--slash-global`` or ``VOXIUM_SLASH_GLOBAL=1`` to match legacy behavior.
- **Transcript log:** session-only, in process RAM (bounded: `--history-limit`, `--history-max-chars`); use **`/history`** in the downlink to list, **`/history <n>`** to expand, **`/history copy <n>`** to put one line on the system clipboard.
- **Local data (same paths on all platforms):** `models/` (Hugging Face downloads for faster-whisper), `logs/` (server log, client lock). Override the project root with `VOXIUM_REPO_ROOT` if needed. The server log defaults to `logs/voxium_server.log` (or `--server-log-file`).
- **Health:** `voxium health`, `voxium stats`; foreground server: `voxium server --help`.
- **Re-encode (local GGUF, default on):** By default, Voxium runs a local `llama.cpp` second pass after STT. Use `voxium run --no-polish` or `transcription.polish_enabled: false` in config to skip it. Voxium probes or starts a local `llama-server`, serves GGUF models from `models/polish`, uses a repo-local runtime under `tools/llama.cpp` when present, and shuts down only the `llama-server` process it launched. Use `voxium models` for the two-lane summary, `voxium models transcribe installed` for downloaded STT models, `voxium models polish list` for trusted re-encoder ids plus installed local GGUF selectors, and `voxium models --polish --pull-polish` to provision the default repo-local runtime/model bundle.

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

- **Windows: `Voxium.cmd` / `Setup-Voxium.cmd` says `pyproject.toml not found` or paths look wrong:** the launchers **must stay** in `scripts\windows\` inside your clone. Do **not** copy only `Voxium.cmd` to the Desktop or another folder — the script finds the repo by going **up two levels** from its own file. Run **`scripts\windows\Setup-Voxium.cmd` once** from Explorer (double-click) only when that file lives under your Voxium repository. If the repo is under **OneDrive** and installs fail with file locks, clone to e.g. `C:\src\voxium` or pause OneDrive sync for that folder.
- **Windows: still broken after setup:** run **`scripts\windows\Diagnose-Voxium.cmd`**. It writes **`%TEMP%\voxium-diagnose.log`**, shows Python / venv / `import voxium` / `import sounddevice`, and opens the log in Notepad. Share that file if you need help. Setup also leaves **`logs\voxium-windows-setup.log`** and **`logs\pip-editable-install.log`** in the repo.
- **Polish runtime/model missing after setup:** rerun **`.\.venv\Scripts\python.exe -m voxium models --polish --pull-polish`** from the repo root. Then check **`.\.venv\Scripts\python.exe -m voxium models polish list`** to confirm the trusted ids and installed local GGUF selectors. Setup provisions the repo-local `llama-server` runtime under `tools\llama.cpp` and the default GGUF model under `models\polish`. If you intentionally skipped this during setup, rerun `scripts\windows\Setup-Voxium.cmd` without `-SkipPolish`.
- **`No pyvenv.cfg file` (or Activate.ps1 not found):** the **`.venv` is invalid** — often a **WSL-created** venv used on **Windows**, or an incomplete copy. **Launching** with **`Voxium.cmd`** (repo or `scripts\windows\`) will run **`Setup-Voxium.ps1 -SkipPolish`** once to repair (set **`VOXIUM_NO_AUTO_SETUP=1`** to skip auto-repair and only show manual steps). Or remove the folder: `rmdir /s /q .venv` (cmd) or `Remove-Item -Recurse -Force .venv` (PowerShell), then run **`scripts\windows\Recreate-Windows-Venv.cmd`** or **`scripts\windows\Setup-Voxium.cmd`**. After a good venv, `.\.venv\Scripts\python.exe -m pip ...` works (no second `python` after `.exe`).
- **`No module named 'voxium'`** (including `ModuleNotFoundError`): the editable install is missing in **`.venv`**. **`Voxium.cmd`** will run setup for you unless **`VOXIUM_NO_AUTO_SETUP=1`**. Or from the **repository root**, install the project in editable mode (PowerShell or cmd):
  ```text
  .\.venv\Scripts\python -m pip install -U pip setuptools wheel
  .\.venv\Scripts\python -m pip install -e .
  ```
  Then confirm: `.\.venv\Scripts\python -c "import voxium; print(voxium.__file__)"` should print a path under `src\voxium`. If you still see the error, remove the broken install and reinstall: `.\.venv\Scripts\python -m pip uninstall voxium -y` then `.\.venv\Scripts\python -m pip install -e .` Do not run `pip` from a different working directory or a different venv than the one you use to launch `voxium`.
- **`voxium` exits before the client starts** (often on Linux/WSL): a bare `voxium` is the same as **`voxium run`** and needs the **mic stack** plus **Linux paste helpers**. Install everything in one pass on Debian/Ubuntu/WSL:  
  `sudo apt update && sudo apt install -y portaudio19-dev xdotool xclip`  
  Then run Voxium from the venv you installed into (e.g. `.venv/Scripts/voxium` on Windows, `.venv/bin/voxium` on Linux). `voxium --help`, `voxium models`, and `voxium health` work without a mic. If you use `pip install -e .` from a clone, reinstall after pulling: `python -m pip install -e .` so the `voxium` script matches the tree.
- **`OSError: PortAudio library not found`:** install the system PortAudio **dev** package (see the line above). macOS: `brew install portaudio`. The full client must load PortAudio for `voxium run`.
- **500 / transcription error:** read `logs/voxium_server.log` in the repo (or `--server-log-file`); on Windows, missing `cublas64_12.dll` usually means CUDA 12 is not on `PATH` — use `--server-device cpu` or install the CUDA 12 Toolkit `bin` directory.
- **Slow or CPU-only:** `--model tiny` or `--server-device cpu`.
- **Linux pynput / hotkeys:** X11 required for typical setups; see Wayland note above.
- **macOS:** grant Accessibility to your terminal.

## How it works

1. **pynput** — global hotkeys  
2. **sounddevice** — microphone  
3. **faster-whisper** (local HTTP worker) — transcription on loopback  
4. **llama.cpp** (optional, default on) — local `llama-server` re-encode pass after STT when **polish** is enabled; falls back to raw text if the runtime is missing or errors  
5. **pyperclip + OS tools** — paste

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) in the repository root.

## Acknowledgments

- [Systran/faster-whisper](https://github.com/SYSTRAN/faster-whisper) and CTranslate2  
- pynput, sounddevice, FastAPI, uvicorn
