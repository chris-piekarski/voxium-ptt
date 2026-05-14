# Changelog

All notable changes to Voxium are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2] - Unreleased

### Added
- `/transcribe-stream` WebSocket lane with live readback for PTT takes. The
  client falls back gracefully if `websocket-client` is missing, but the dep
  is required at runtime for live text — see
  [docs/plans/live-transcribe-stream.md](docs/plans/live-transcribe-stream.md).
- `/profile` slash command exposing LLM polish and Whisper `/transcribe` lane
  timings; standardized ring window, chatter `max_tokens`, and `history.limit`
  defaults to 42.
- Client `/stats` and `/hotkeys M` (Morse) commands; new `persistent_stats`,
  `morse_code`, `morse_audio`, and `exit_pause` helpers.
- Windows `Profile-Voxium-PTT.ps1` py-spy helper with Desktop / repo-logs
  fallback and auto-retry attach for `--subprocesses` capture.
- [docs/profiling.md](docs/profiling.md): py-spy-first profiling workflow,
  same-OS attach rules (Windows vs WSL), flamegraph capture.

### Changed
- UX chatter now shares the polish lane instead of provisioning its own,
  unifying the client chatter source.
- README: live screenshot moved below intro; flame graph relocated; operator
  copy refreshed.

### Fixed
- Force IPv4 on loopback URLs to skip the IPv6 fallback stall
  ([#9](https://github.com/chris-piekarski/voxium-ptt/pull/9)).
- `requirements.txt`: restore missing `websocket-client>=1.7.0` runtime dep
  (drift from `pyproject.toml`; broke pip-only installs of the new streaming
  readback).
- `scripts/Profile-Voxium-PTT`: parameter order, Desktop path resolution via
  `GetFolderPath`, py-spy `--subprocesses` default on Windows attach.

## [0.0.1] - 2026-04-29

Initial public tag — PTT & VOX local voice-to-text over loopback to
faster-whisper, with optional llama.cpp polish lane and Apollo-stack /
HAM-radio brand voice. See
[README.md](README.md) and [docs/](docs/README.md) for the v0.0.1 baseline.
