# Profiling Voxium

This is the **preferred** maintainer workflow for performance work in Voxium when the app feels slow but the issue does **not** appear to be model inference itself.

**Default recommendation:** stay on **CPython** and use **`py-spy` first**. Treat it as a **developer tool** installed in the venv via the **dev** extras (`make install-dev` on Linux / WSL, or `pip install -e ".[dev]"` on Windows).

## 1. Why `py-spy` is the preferred first pass

Voxium is an interactive terminal application with:

- hotkeys
- audio capture
- Rich UI redraws
- loopback HTTP calls
- background threads
- subprocess / daemon management

For that shape of application, we prefer a **low-overhead sampling profiler** over a call-by-call profiler.

**Why not start with `cProfile`?**

- `cProfile` instruments every Python call.
- That extra work can distort short interactive delays.
- It is still useful later for a narrow, repeatable hotspot, but it is **not** our default first look.

**Why `py-spy` first?**

- low overhead
- can attach to a live process
- does not require app code changes
- works well for startup lag, redraw churn, polling loops, thread contention, and repeated formatting work

## 2. Install

### Linux / WSL

Use the project dev install so `py-spy` lives in the same venv as the rest of the maintainer tooling:

```bash
make install-dev
```

If you want to install it directly:

```bash
.venv/bin/python -m pip install py-spy
```

If you use a WSL-specific venv:

```bash
VENV=.venv-wsl make install-dev
```

### Windows

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

That installs the maintained developer tool set, including `py-spy`.

To record a flame graph of the **Windows client** during PTT without fragile one-line pastes, from the repository root:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\Profile-Voxium-PTT.ps1
```

Optional flags: `-Duration`, `-Rate`, `-Native`, `-ClientProcessId`, `-OutputPath`, `-Spawn`, `-SpawnArguments`, `-NoSubprocesses`. Default `-OutputPath` picks the first existing directory among `GetFolderPath(Desktop)`, `%USERPROFILE%\OneDrive\Desktop`, `%USERPROFILE%\Desktop`, and `%PUBLIC%\Desktop`; if none exist, it writes under `logs\py-spy\` in the repo (created automatically). Attach mode auto-retries on Windows: plain, then `--nonblocking`, then `--subprocesses` (omit the last with `-NoSubprocesses`). If every attach strategy fails, use `-Spawn` so py-spy starts `python -m voxium` (close the main client first — Windows single-instance mutex). Run `Get-Help .\scripts\windows\Profile-Voxium-PTT.ps1 -Full` for details.

## 3. Same-OS rule

Run `py-spy` from the **same OS context** as the Voxium process you want to profile.

- If Voxium is running in **WSL**, use **WSL `py-spy`**.
- If Voxium is running in **Windows Python**, use **Windows `py-spy`**.

Do **not** expect Windows `py-spy` to profile a WSL process, or vice versa. Voxium already has Windows / WSL split behavior around loopback and venvs; profiling follows the same practical rule.

## 4. Basic workflow

### 4.1 Start Voxium normally

Example in WSL:

```bash
VENV=.venv-wsl ./.venv-wsl/bin/voxium run
```

Example on Windows:

```powershell
.\.venv\Scripts\voxium.exe run
```

### 4.2 Find the process ID

Linux / WSL:

```bash
ps -ef | rg "voxium|python"
```

Windows PowerShell:

```powershell
Get-Process | Where-Object { $_.ProcessName -match "python|voxium" }
```

### 4.3 Live view: `top`

Use this when you want to watch where time is going while the app is active:

```bash
py-spy top --pid <PID>
```

Good first pass for:

- slow startup
- lag while idle
- Rich redraw churn
- polling loops
- repeated config / formatting work

### 4.4 Capture a flamegraph

This is the most useful artifact for code review and follow-up optimization:

```bash
py-spy record -o voxium-profile.svg --pid <PID> --duration 20
```

Then open `voxium-profile.svg` in a browser.

### 4.5 Reference flame graph: idle / standby

A captured `py-spy` flame graph for **idle / standby** lives at
[`docs/profiling/idle-standby-default.svg`](profiling/idle-standby-default.svg).
GitHub renders it as a static preview in the markdown view; download the raw
SVG and open it in a browser to keep the inferno hover, zoom, and search
controls. Use it as the **shape baseline** when comparing a new capture from
the same scenario — if the new capture's tall stacks differ from this one,
that delta is your investigation target.

## 5. How to capture useful traces

Profile **one scenario at a time**. Avoid mixing startup, PTT, slash commands, and VOX behavior in the same capture unless the problem is truly global.

Good scenarios:

1. **Startup**
   Start recording, then launch `voxium run`.
2. **Idle / standby**
   Attach with `py-spy top` and let the app sit where the lag is visible.
3. **PTT stop → paste**
   Record a short sample, stop the key, and capture the end-to-end delay.
4. **Slash command rendering**
   Run `/health`, `/models`, or another slow command during the capture.
5. **VOX loop**
   Capture the re-arm / standby cycle if the app feels slow while listening.

## 6. What we expect to find outside inference

When the model itself is not the issue, likely hotspots include:

- Rich panel rebuilds
- terminal redraw frequency
- repeated string formatting
- repeated config merging or status rendering
- loopback retry / timeout behavior
- subprocess reachability checks
- audio buffer transformation or metering work
- thread wakeups or unnecessary polling

## 7. Follow-up tools after `py-spy`

Use these only after `py-spy` points at a suspicious lane:

- **`scalene`** when you need Python vs native vs memory pressure detail
- **`yappi`** when you need thread-aware timing detail
- **`cProfile`** for a small, deterministic, isolated code path

Our default order is:

1. `py-spy`
2. targeted follow-up profiler if needed
3. code-level timers only around the hotspot that was confirmed

## 8. Keeping profiling out of the product path

`py-spy` is a **developer tool**, not a product feature.

- We do **not** add it to normal runtime code paths.
- We do **not** make Voxium depend on profiling hooks to run.
- We prefer external attachment over permanent instrumentation.

If we later add in-repo timing hooks, keep them:

- behind a flag
- narrow in scope
- focused on confirmed hotspots

## 9. Quick commands

### WSL / Linux

```bash
VENV=.venv-wsl make install-dev
ps -ef | rg "voxium|python"
py-spy top --pid <PID>
py-spy record -o voxium-profile.svg --pid <PID> --duration 20
```

### Windows

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Get-Process | Where-Object { $_.ProcessName -match "python|voxium" }
py-spy top --pid <PID>
py-spy record -o voxium-profile.svg --pid <PID> --duration 20
```
