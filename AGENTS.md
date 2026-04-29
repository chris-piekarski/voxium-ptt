# AGENTS — context for humans and AI assistants (Voxium)

This file summarizes how the **Voxium** repository is organized, how to develop and verify changes, and **project policy** for Git and pull requests.

---

## Project status (for humans and agents)

**Treat this repository as a functional, maintained product codebase** — not a one-off script or throwaway experiment. Ongoing work is expected; when you change behavior, follow the same standards as production-oriented software (tests, `make lint`, operator-facing docs where appropriate). If a future change leaves something half-finished, leave a **short, explicit note in code, PR, or a tracking issue** rather than implied state.

---

## What this project is

- **Voxium** is a **PTT (push-to-talk) voice-typing** application for the terminal: **VOX** in, text out, over a **local loopback** to a **Whisper**-based server (or local inference). Brand-wise, the repo treats the **HAM/CB radio** heritage (PTT, **VOX**, mic, copy) and the **Apollo-era** image of people flying **new combinations** of **hardware + software + mechanical** systems with **robotic** automation (inference stack, I/O) into **uncharted, local** territory—see [`docs/brand.md`](docs/brand.md).
- The installable package lives under **`src/voxium/`** (setuptools `package-dir` = `src`).
- The console entry point is **`voxium`**, defined in `pyproject.toml` as `voxium.cli.main:main` (the CLI entry imports `voxium.app` for the interactive client).

---

## Requirements

- **Python** ≥ 3.10 (see `pyproject.toml`).
- **Development** optional extras: `black`, `pylint`, `ruff`, `pytest`, `pytest-cov`, `mypy`, `httpx`, type stubs (see `[project.optional-dependencies] dev`).

---

## Repository layout (high level)

| Area | Role |
|------|------|
| `src/voxium/` | Application code: `app.py` (client UI / hotkeys / **PTT & VOX** input modes), `vox_chunker.py` (utterance end-pointing for **open-mic VOX**), `whisper_server.py` (FastAPI + inference), `cli/`, `config`, `model_registry`, **session UI** (`console_status`, `recording_ui`, `standby_fft`, `standby_telemetry`), **slash** (`slash_commands`, `slash_complete`), `session_history` (in-RAM PTT/VOX transcript list; `/history`), `disk_usage_report` (`make disk-usage` / `/disk`), and **small pure modules** (`loopback`, `hotkey_rules`, `metrics_*`, `speech_guards`, etc.) for testable logic |
| `tests/` | `pytest` suite; `pythonpath` includes `src` and `.` (see `pyproject.toml`) |
| `scripts/` | `mk.py` — Python backend for **GNU make** (venv, install, lint, test); `scripts/windows/` — `venv_bootstrap.cmd`, `Voxium.ps1` / `Voxium.cmd` to launch `voxium` on Windows with a short tab title |
| `Makefile` | Dev workflow: `make install`, `make install-dev`, `make lint`, `make test`, `make test-cov`, etc. |
| `docs/` | **Operator-oriented** documentation, **Mermaid** diagrams, and **brand** (`docs/brand.md`): see [Operator documentation](#operator-documentation) and [Brand voice](#brand-voice) |
| `pyproject.toml` | Project metadata, **black**, **pylint**, **ruff**, **mypy** (with overrides for large modules), **pytest**, **coverage** |

On **Windows**, the README may point to PowerShell / the `voxium` CLI instead of `make`.

**Repository data on disk** (what `make disk-usage` and `/disk` summarize): **`models/`**, **`logs/`**, and **`tools/llama.cpp/`** (local polish / `llama-server` prebuilds) under the project root (or `VOXIUM_REPO_ROOT`). `make clean` removes **`tools/llama.cpp/`** in addition to caches. **Transcript text** for `/history` and replay is **in-process RAM only** — the product does not use a `history/` directory under the repo for that data.

---

## Operator documentation

Files under **`docs/`** are for **operators** (anyone running, deploying, or deeply operating the software): **be verbose, concrete, and helpful**—assume the reader is smart but not already familiar with every internal.

- **Explain the “why” and the “how”**: prerequisites, common failure modes, recovery steps, and where to look (logs, flags, environment variables) when something goes wrong.
- **Use [Mermaid](https://mermaid.js.org/) diagrams** wherever they clarify **flows, dependencies, or architecture** (for example: sequence diagrams for client ↔ server, state diagrams for recording lifecycle, or flowcharts for install and startup). Prefer diagrams over long prose when the system has branching or parallel steps.
- Keep each doc **scannable**: clear headings, short paragraphs, and diagrams near the text they support.

The top-level **README** stays a concise entry point; **depth** belongs in `docs/`.

---

## Brand voice (mandatory for user-facing and operator work)

Voxium’s public tone is defined in **[`docs/brand.md`](docs/brand.md)**. In short:

1. **Radio / PTT** — HAM and CB *culture* (not jargon overload): stress **PTT** and **VOX**, mic checks, “copy/standing by” where it stays readable. Never obscure errors or steps with slang.
2. **Apollo / uncharted stacks** — Narrate the product as *humans steering* a **local** blend of **electrical + software + mechanical** *plus* **automated/robotic** work (inference, streaming) in **new, first-flight** conditions on **your** machine. Metaphor only: no false historical claims.

**Enforcement for contributors and agents**

- New or edited **user-visible** strings (CLI `description` / `epilog`, startup banners, Rich `Panel`/`Table` **titles** where we already use voice, `print()` meant for the operator, high-level `logger.info` on the **server** when they are *informational*): follow **`docs/brand.md`** and keep **actionability** first.
- **Errors**, **tracebacks**, **API field names**, **JSON keys**, and **test output**: **neutral, precise**; no required brand layer.
- **README**, **`docs/*`**, help text, and **scripts** that print human-facing headers (`mic_info`, `gpu_info`, `make help` title) should **align** with the two themes when you touch that file.
- If you are unsure, read **`docs/brand.md`**, then the nearest existing example in the same file.

---

## Setup (typical)

```bash
make install        # venv + editable install (.venv/bin/voxium)
make install-dev    # ensures .[dev] (ruff, pytest, …) via the dev stamp
```

Use the venv’s Python for development (`.venv/bin/python`, `.venv/bin/voxium`).

---

## Verification: what to run

### Merge / PR requirement

All **pull requests must pass** the following before merge:

```bash
make test
make lint
```

- **`make test`**: runs **`pytest tests/`** via the project venv (no coverage gate; see `scripts/mk.py` → `cmd_test`).
- **`make lint`**: runs **`black --check`** on `src/`, `tests/`, and `scripts/`, then **`ruff check <repo root>`**, then **`mypy`**, then **`pylint`** on `src/voxium`, `tests`, and `scripts` (see `[tool.mypy]` and `[tool.pylint.*]` in `pyproject.toml`). A **Ruff sub-score 0–10/10** (one point per Ruff finding, from 10 down) and file scope counts are printed after Ruff. The target fails if any step fails.

Optional / extra (not a substitute for the above unless team policy changes):

- **`make test-cov`**: pytest with **coverage** (terminal per-file / missing-line report, no HTML). The make script passes extra pytest coverage flags; consult `scripts/mk.py` (`cmd_test_cov`) for the exact `pytest` invocation. **`pyproject.toml` `[tool.coverage.*]`** defines `fail_under` and `omit` rules when using `--cov-config` directly.

### Coverage notes (current policy in `pyproject.toml`)

- Coverage is configured for **`src/voxium`** (the `make test-cov` gate does not include `scripts/`), with **branch** coverage.
- **Omitted** from measurement: `voxium/__main__.py`, `voxium/app.py`, `voxium/whisper_server.py`, `voxium/console_status.py` (heavy or interactive; optional-runtime dependencies for full import in some environments).
- **`fail_under`** is set in **`[tool.coverage.report]`** (treat as the baseline for the **included** measured set).

### Tests that may skip

- If **`ctranslate2`** is not installed, **`tests/test_server_health.py`** is skipped at import via `pytest.importorskip` (ASGI `/health` contract; needs the full Whisper server stack to import the module meaningfully).

### Type-checking

- **mypy** is configured in `pyproject.toml`; `voxium.app` and `voxium.whisper_server` have `ignore_errors = true` so the rest of the tree can be checked without being blocked. **`make lint`** runs **mypy** after Black and Ruff.

---

## Code style and linting

- **Black** (format, check-only) and **Ruff** (lint) run under **`make lint`**, plus **mypy** (types) and **pylint** (config under `[tool.pylint]`, with large entrypoints ignored to match the mypy/coverage story). Ruff per-file ignore: `src/voxium/whisper_server.py` → `E402` (imports after side-effect / CUDA path setup) in `pyproject.toml`.

---

## Git and pull request policy

### Signed commits

- **All Git commits should be GPG- or SSH-signed** (or the equivalent your hosting provider uses). Configure your environment so reviewers can trust commit authorship and integrity.

### Conventional Commits (required scope)

Use **Conventional Commits** with a **`type` from this set** (unless a broader org standard explicitly allows more):

- **`feat`** — new user-visible behavior or API
- **`fix`** — bug fixes
- **`chore`** — maintenance (deps, CI, refactors with no product change, docs only when not worth `feat`/`fix`)

**Apply the same style to:**

- **Commit message subject lines** (e.g. `fix: close stream on error`)
- **Pull request titles** (e.g. `chore: bump ruff`)
- **Branch names** (e.g. `feat/health-timeout`, `fix/loopback-parse`, `chore/ruff-04`)

You may add a **scope** in parentheses when it helps, e.g. `fix(cli): normalize argv for bare flags`.

### Branches and PRs

- **Branch names** should reflect the work and follow the same `feat` / `fix` / `chore` prefix convention as above.
- **PR titles** should use the same conventional **`type: summary`** form.
- **Merges are only allowed** when **`make test`** and **`make lint`** both succeed in the **PR branch** (run locally in the venv and/or in CI, depending on your pipeline; the rule is: **no merge** until that bar is met).

---

## Hints for AI / automation agents

- **WSL and Windows on the same clone:** a **single** `.venv` cannot be shared—Windows venvs use `Scripts\` and `Lib\`, Linux venvs use `bin/`. If you develop in **WSL** but also use **Windows** Python in the same repo, use **separate** directories, e.g. default `.venv` for whichever OS you use most, and **`VENV=.venv-wsl` `make install`** for a Linux-only venv in WSL (add `.venv-wsl/` to `.gitignore` if not already). Close processes that hold locks before deleting a broken venv on a shared (`/mnt/...`) path.
- **PTT vs VOX (input mode):** default is **PTT**; **VOX** is open-mic with gating in `voxium.vox_chunker` (`UtteranceChunker`). Toggling mode uses `--mode-hotkey` / `hotkeys.mode` (default **F7** in `DEFAULT_HOTKEYS`); re-transmit is `retry` (default **F6**). Mode switches are logged in the **violet downlink** (`print_input_mode_downlink`), not as a second green `set_status` line.
- **Green Voxium panel (`PttSessionStatusBox`):** **On station** and **VOX re-arm (open mic + live meter)** each start a **new** one-step `Live` session (same idea: readable 1:1 blocks). **Do not** call `ptt_status_box.set_status` for the same status right after `show_status` in the same handler — that stops `Live` twice and duplicates the green box in scrollback. Main/footer `Live` use `auto_refresh=False` to avoid nested-`Live` refresh hammering the root (flicker).
- **Paste target in VOX:** PTT sets `target_window` in `start_recording`; VOX must pass `paste_target` from `get_active_window()` per utterance (and when queuing) or paste/focus can miss the operator’s app.
- **Hotkeys in YAML:** `hotkey_rules.canonical_hotkeys_dict` lowercases `record` / `recovery` / `retry` / `mode` so `Record` / `MODE` work. **`normalize_hotkey_name`** maps bare `6` / `6.0` to `f6` so we do not spuriously warn “adjusted hotkeys.” `hotkey_config_changed` only compares keys the user **set**; empty or unset actions are not compared against defaults in a way that false-triggers the warning.
- Prefer **small, pure functions** in `src/voxium/` and **tests** over growing `app.py` / `whisper_server.py` when the change is testable in isolation.
- **Do not** add unsolicited **root-level** documentation files beyond what maintainers request. **Exception:** updates and additions under **`docs/`** are welcome when they follow the [Operator documentation](#operator-documentation) section (verbose, operator-focused, **Mermaid** where it helps) and, for operator copy, the [Brand voice](#brand-voice) / [`docs/brand.md`](docs/brand.md) rules. This file (`AGENTS.md`) remains the place for **repo-wide policy and structure** for agents and humans.
- Match **existing** naming, imports, and ruff/mypy expectations; avoid drive-by refactors outside the task.
- When you change **any** public-facing or operator-facing string, **re-check** [Brand voice](#brand-voice) and [`docs/brand.md`](docs/brand.md) (PTT & VOX + Apollo-style local “stack” story).

When in doubt, run **`make test`**, **`make lint`**, and keep the PR aligned with the **conventional** + **signed** commit policy above.
