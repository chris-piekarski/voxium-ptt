# Pre-PR Review: ux_chatter (Gemma) feature branch

## Context

This branch adds the **`ux_chatter`** subsystem — a second `llama-server` (Gemma 3 1B) running on port `11436`, separate from the existing `polish` llama.cpp instance. It generates console-only flourishes (startup tagline, panel subtitle, "Standing by" wit, shutdown farewell, post-transcript downlink). Default-on; opt-out via `--no-ux-chatter` or `VOXIUM_UX_CHATTER=0`.

**Scope (second-pass):** 15 modified files, 7 new files, **~905 line insertions** (up from ~628 at first pass — `app.py` grew to +502, and `llama_cpp_daemon.py` (+106), `console_status.py` (+26), `ensure_model_client.py` (+27), `radio_readback.py` (+7), `slash_commands.py` (+3) entered scope).

**Process:** Two review passes with parallel Explore agents (per-area), then every "must-fix" claim verified against the actual code. Agents tend to flag concurrency and lifecycle issues that don't survive a careful read; rejected claims are listed at the bottom.

## Findings worth acting on

### Must-fix (verified)

None of the verified findings are true blockers. The branch is shippable.

### Should-fix (verified, low effort, real value)

1. **Env-var precedence gap: `VOXIUM_UX_CHATTER=0` does not prevent the second `llama-server` from starting**
   - Help text at `src/voxium/app.py:675` promises "set `VOXIUM_UX_CHATTER=0` to force off."
   - The runtime guard works: `is_ux_chatter_wanted()` at `src/voxium/ux_chatter.py:160-165` checks `_env_disables_ux()` and short-circuits Gemma fetches.
   - But `ensure_llama_cpp_for_ux_chatter()` at `src/voxium/app.py:1464` is called unconditionally at `app.py:3851` and its own guard checks only `config.ux_chatter` (the CLI flag), **not** the env var. So with `--ux-chatter` set (or default-on) and `VOXIUM_UX_CHATTER=0`, the second `llama-server` still starts and idles.
   - Fix: gate the call at line 3851 with `is_ux_chatter_wanted(...)` (matching the banner-block call at line 3815-3819), or add the env check inside `ensure_llama_cpp_for_ux_chatter` itself.

2. **Test gap: async `schedule_ux_chatter_after_transcript` flow is not exercised**
   - `tests/test_ux_chatter.py` covers the helpers but not:
     a) Cooldown skips a duplicate transcript fired within `cooldown_s` (`src/voxium/ux_chatter.py:481-486`)
     b) The `on_complete` callback is invoked and exceptions inside it don't crash the worker (already wrapped at `src/voxium/ux_chatter.py:498-501` — good — but untested)
     c) The returned `Future` resolves with the cached wit
   - Add 2–3 small tests using `concurrent.futures` assertions; mocks for `request_ux_chatter_line_full` are straightforward.

3. **Silent exception swallowing in fetch_* helpers**
   - `src/voxium/ux_chatter.py:17, 57, 106, 156` — bare `except Exception: return None` with no logging.
   - For best-effort UX features silent-degrade is intentional, but a single `_LOG.debug("ux chatter fetch failed", exc_info=True)` per handler costs nothing and saves an hour next time the loopback misbehaves. Compare to the (good) pattern already in `_run` at line 501.

4. **HF download path: code duplication with polish_provision**
   - `src/voxium/ux_chatter_provision.py` duplicates `format_*_hf_error`, `ensure_*_model_downloaded`, and reuses `VoxiumPolishHubTqdm` indirectly.
   - Not a blocker — both subsystems work — but extracting one shared `ensure_hf_model_downloaded(repo_id, filename, local_dir, …)` would make the third LLM (whenever) cheap. Defer to a follow-up PR.

### Nice-to-have

5. **Banner top-line text change is intentional but worth a callout in the PR description**
   - `src/voxium/startup_banner.py:178`: `"PTT & VOX box — VOX in, text out · shack, no uplink"` → `"PTT & VOX Speech-to-Text for Terminal Input"`
   - The CB/HAM flavor moved into the new `default_rig_subtitle()` (lines 100–110: *"Rig on station at {host}  ·  PTT & VOX  ·  loopback  ·  1960s base-station copy, you key, stack in the loop"*). Personality preserved, just relocated. Mention it in the PR body so reviewers don't think it's accidental.

6. **`docs/architecture.md` does not mention `ux_chatter` yet**
   - Polish has its own subsection (3.2). UX chatter has none. The design doc (`docs/ux-chatter-gemma.md:128`) explicitly defers this ("optional… when behavior is real"). Behavior is real now. One paragraph + a node in the Mermaid diagram would be enough.

7. **`scripts/windows/Voxium.ps1`: comment-only addition, no env var setup for UX**
   - Polish gets `$env:VOXIUM_POLISH_ENABLED=1` (line 28). UX chatter doesn't, because Python defaults it on. Correct, but the asymmetry will trip a future reader. One-line comment ("UX chatter defaults on at the Python layer; only `VOXIUM_UX_CHATTER=0` is honored to opt out") would fix it.

8. **Idempotency flag is set before startup succeeds**
   - `_llama_cpp_ux_ready_checked` at `app.py:1464+` is set `True` before the actual server launch resolves. If the banner-block call (line 3819) fails to start the server, the main-path call (line 3851) won't retry — it just sees the flag set and returns. Same pattern as polish, so consistent, but worth a code comment so future readers don't think retry is wired up.

9. **Shutdown farewell may add a small Ctrl+C delay**
   - `_print_shutdown_farewell()` at `app.py:2141` synchronously calls `fetch_ux_shutdown_line` (with timeouts). If the UX server was never reachable, you pay the timeout on exit (≈0.9–2.8s per the per-helper budget). Acceptable, but worth knowing if users complain about slow Ctrl+C exit.

10. **`ux_chatter.py:105-106`: redundant fallback chain**
    - `str(uxc.get("base_url") or "http://...").strip() or "http://..."` — after the `or ""` and `.strip()`, the second `or` already covers empty. Minor.

### Rejected (agent claims that did NOT survive verification)

- **Race on `_cached_wit` (claimed at `ux_chatter.py:494`).** The `with _wit_lock:` opens at line 493 and the assignment is on line 496. `global _cached_wit` is a Python scoping declaration, not a runtime statement — the lock IS held.
- **Race on `_llama_cpp_ux_ready_checked` from background threads.** Grep confirms the function is only called from the synchronous `run_client` path (`app.py:3819, 3851`); never from a thread or callback. No lock needed.
- **`show_startup_banner(tagline=...)` signature missing.** Diff confirms the function now accepts `tagline` and `rig_subtitle` kwargs (`startup_banner.py:192-197`).
- **"Cross-session contamination" race on global `config` in `_ux_chatter_on_complete`.** Voxium is a single-process per-invocation CLI; `config` is assigned once during `run_client` startup and never reassigned mid-session.
- **`_llama_cpp_ux_ready_checked` init order bug.** Flag is properly module-scoped.
- **Cleanup race in `stop_managed_llama_cpp` causing the second daemon never to be killed.** Verified at `llama_cpp_daemon.py:269-303`: every operation is wrapped in `try/except Exception: pass`. The function never raises; the cleanup chain at `app.py:2564-2573` is safe.
- **Log-file append race in `append_llama_stack_log_line`.** POSIX `O_APPEND` is atomic for small writes; the two daemons write to separate files anyway. Non-issue.
- **Port collision between :11435 (polish) and :11436 (UX).** Different ports; can't collide.

## Critical files in this PR (final personal scan)

- `src/voxium/app.py` — the +502-line integration. Worth a final read of `_ux_chatter_on_complete`, `_maybe_schedule_ux_chatter`, `ensure_llama_cpp_for_ux_chatter`, `_print_shutdown_farewell`, and the argparse `uxc_group` block (lines ~670–717).
- `src/voxium/ux_chatter.py` — global state, locks, executor lifecycle. Apply finding #3.
- `src/voxium/ux_chatter_provision.py` — duplication with polish; finding #4.
- `src/voxium/llama_cpp_daemon.py` — verify the +106 lines do what you expect for the second instance (label/log path/port).
- `tests/test_ux_chatter.py` — coverage gap from finding #2.
- `tests/test_ensure_model_client.py` — new `quiet_success` helper covered; spot-check tests are tight.

## How to verify before pushing

1. `make lint` and `make test` — full suite, including new ux_chatter and daemon tests.
2. Smoke test: `voxium run` with default flags. Confirm the second llama.cpp comes up on `:11436`, banner shows the new top label, rig subtitle shows the radio language.
3. Smoke test: `VOXIUM_UX_CHATTER=0 voxium run`. **Currently** confirms no Gemma calls — but the second llama-server still starts (finding #1). After fixing #1, confirm no second server appears in `ps`.
4. Smoke test: `--no-ux-chatter` flag should cleanly skip the second server start.
5. Smoke test: `voxium models --pull-ux-chatter` lands the GGUF in the resolved `ux_models_dir()`.
6. Ctrl+C exit with UX on — confirm farewell line appears (model-written if reachable, static fallback otherwise) and exit takes <3s.
7. After committing, review the GitHub diff once more — the +905-line view often surfaces noise the per-file diff hides.

## Recommended PR body outline

- One sentence summary: *"Adds Gemma-driven UX chatter subsystem (second llama.cpp on `:11436`) for console flourishes; default-on, opt-out."*
- Note the banner top-label rebrand (finding #5) so reviewers don't think it's accidental.
- Acknowledge follow-up debt: shared HF-download utility (finding #4) and architecture.md prose (finding #6).
- Note finding #1 (`VOXIUM_UX_CHATTER=0` env-var precedence) — fix before merge or call out as a known issue.
