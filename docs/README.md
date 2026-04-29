# Voxium documentation

Welcome to the **Voxium 0.0.1** technical documentation. The voice here follows **[brand.md](brand.md)** — **PTT & VOX** (radio heritage) and an **Apollo-style** story: people wiring **hardware + software + mech** with **automated** work (inference) for **local, first-stack** runs. These pages also cover **architecture**, **test and coverage policy**, and **Mermaid** (GitHub, VS Code, etc.).

| Document | Description |
|----------|-------------|
| [brand.md](brand.md) | **Brand voice:** HAM/CB-tinged *PTT* & **VOX** plus Apollo / uncharted-local-stack tone; where to use it. |
| [architecture.md](architecture.md) | System context, major components, PTT and **VOX (open mic)** request flows, and current layout (with diagrams). |
| [testing.md](testing.md) | Unit-test strategy, coverage **fail-under** in `pyproject.toml`, markers, and how to run `pytest` / `make test-cov`. |
| [repository-stats.md](repository-stats.md) | **LOC snapshot** of the tree (by area and `voxium` subpackages), with Mermaid `pie` diagrams; run `make repo-stats` to refresh. |
| [llm-polish-plan.md](llm-polish-plan.md) | Design and implementation notes for the optional local **LLM polish** pass: STT text goes through a repo-local **llama.cpp** runtime (`llama-server` + GGUF model) on the loopback stack; Windows setup/bootstrap now provisions the runtime and default model into the repo. |
| [radio-chatter-context.md](radio-chatter-context.md) | Optional **CB / HAM** phrasing reference for brand-adjacent copy (not legal or protocol guidance). |
| [ux-chatter-gemma.md](ux-chatter-gemma.md) | **Gemma**-driven **console-only** UX (on by default; opt out with `--no-ux-chatter` or config), second `llama-server` on a separate port, guardrails. |

## Diagram index

- **Brand** — PTT & VOX + local stack “first flight” tone: [brand.md](brand.md)
- **Context (C4-style)** — who uses Voxium and what it talks to: [architecture.md#1-system-context](architecture.md#1-system-context)
- **Logical components** — client, local server, model registry: [architecture.md#2-logical-components](architecture.md#2-logical-components)
- **Record-to-paste sequence** — hotkey through transcription to paste: [architecture.md#3-sequence-record--transcribe--paste](architecture.md#3-sequence-record--transcribe--paste)
- **VOX (open mic)** — continuous capture, `vox_chunker`, green-panel cycles: [architecture.md#31-vox-open-mic-path](architecture.md#31-vox-open-mic-path)
- **Polish path** — optional `/polish` and `/re-encode` (same lane), repo-local `llama-server`, raw fallback semantics: [architecture.md#3-sequence-record--transcribe--paste](architecture.md#3-sequence-record--transcribe--paste)
- **Bootstrap provisioning** — `Setup-Voxium.cmd` / `voxium models --polish --pull-polish`: [architecture.md#32-bootstrap-and-local-polish-provisioning](architecture.md#32-bootstrap-and-local-polish-provisioning)
- **Packaging & files** — `src/voxium` layout (incl. session UI, standby, slash): [architecture.md#4-repository-layout](architecture.md#4-repository-layout)
- **Coverage & test layers** — unit vs integration, `src/voxium` fail-under, `scripts/` out of gate: [testing.md#2-test-layers](testing.md#2-test-layers)
- **Repository size** — code vs area / package (pies + tables): [repository-stats.md](repository-stats.md)
- **LLM polish** — STT → optional local LLM, `llama-server`, GGUF model inventory, API sketch: [llm-polish-plan.md](llm-polish-plan.md)
- **UX chatter** — Gemma on a **second** `llama-server` (same engine as polish), console-only, default on: [ux-chatter-gemma.md](ux-chatter-gemma.md)

## Conventions

- **Windows:** optional repo scripts under `scripts/windows/` (`Voxium.cmd` / `Voxium.ps1`, `venv_bootstrap.cmd`) are documented in the top-level **README** (Install / Windows).
- **Code samples** in these docs are illustrative; the **source of truth** is the repository and `pyproject.toml`.
- **Mermaid** blocks use the `mermaid` fenced language; if a viewer does not render them, use [GitHub’s preview](https://github.com) or [Mermaid Live Editor](https://mermaid.live).
- **Branding** changes belong in the voice of [brand.md](brand.md) when the text is **operator-facing** (not JSON keys, not raw errors).
