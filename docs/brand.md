# Voxium brand voice

Voxium’s public voice rests on **two** complementary themes. Every **operator-facing** string (README, `docs/`, CLI help, startup banners, Rich panels, and high-level logs) should reflect one or both—without breaking clarity, searchability, or supportability.

---

## 1) Radio: HAM, CB, PTT, and VOX

**Why:** The product is **push-to-talk (PTT)**. That is the same **floor culture** as amateur and CB radio: a mic key, a short **transmission**, a clear readback. The name *Voxium* itself points at **VOX** (voice): signal in, duty cycle short, no endless carrier unless you need it. In copy, name the pair **PTT & VOX** or **PTT/VOX** (not `VOX/PTT` — PTT leads).

**How to use it (examples, not a mandatory list):**

- Favor **PTT**, **mic check**, **copy** (I read you), **standing by** / **monitoring**, **10-4** / **roger** (sparingly), **over** (end of transmission) where it still reads as English.
- Avoid insulting or exclusive CB stereotypes; keep it **inclusive and professional**—a nod, not a caricature.
- **Errors and diagnostics** must stay **actionable** first. A one-line “radio” flavor is optional; the fix must always be obvious.

**Product nouns to reinforce:** *PTT* (the gesture), **VOX** (voice as the input medium), *loopback* (local link—your rig, your wire).

---

## 2) The new space race: coding agents on a moon-and-back run for software engineering

**Why:** **Coding agents** are the new **space race** for software engineering — humans set the destination, agents fly the heavy lift from **intent to working code and back** with the result.

Voxium puts the human at the key with a **voice channel** into that loop: PTT in, text out, agents and tools downstream.

On your own machine you are also wiring **electrical** (mics, GPUs), **mechanical** (keyboards, chassis), and **software** (client, model, server) with **automation** (inference engines, schedulers, agents) to make that **moon-and-back** round trip routine, locally.

**How to use it (examples, not a mandatory list):**

- A **mission**, **run**, or **moonshot** can frame a user session; **moon-and-back** and **first-flight** are fair for docs when describing the round trip from voice → code → working result on *your* local stack, not a guarantee about the industry.
- **Stack**, **ground** (this machine you control), **on station / ready** for services; avoid NASA role-play that obscures the actual client/server split.
- **Rockets**, **spacecraft**, and **coding agents** are **metaphors / actors** for *systems* that do heavy, automated work — heavy lifters in the agent loop, not literal product claims.

**Emphasis:** humans **steer**; **agents and the inference path** **execute** the heavy, repeatable work (transcription, polish, downstream tooling) within the route you set.

**Vocabulary:** see [radio-chatter-context.md §17](radio-chatter-context.md#17-coding-agents--moon-and-back-vocabulary) for the compact word list (mission, run, moonshot, on station, downlink, agent loop, RTB, …) — companion to §16's radio vocabulary.

---

## 3) Where to apply the voice

| Surface | Guideline |
|--------|------------|
| `README`, `docs/*.md` | Open with a strong line; weave themes in section intros and “how it works” |
| `voxium --help` / subcommands | Short tagline in `description`; epilog can be one short paragraph of flavor |
| Startup (client) | Banner + “standing by / PTT” phrasing; keep hotkey list literal |
| Rich `Panel` / `Table` titles | Metaphor allowed (`Downlink` for health JSON, etc.); not every panel |
| `logger` (server) | **Informational** lines: light flavor. **Error** lines: error first, flavor last or omit |
| JSON, APIs, protocol fields | **Neutral** names—no brand slang in keys/values for operators’ scripts |
| Tests, stack traces, `pragma` | **No** brand requirement; clarity only |

---

## 4) Mermaid in docs

Diagrams should still read **visually** on-theme where easy: e.g. label the user’s path as **PTT** or the server as the **inference stack**; keep nodes short so diagrams stay legible. See [architecture.md](architecture.md) for flowcharts.

---

## 5) Enforcement

Changes that **only** affect internals may ignore this document. Any change to **user-visible** copy, dev-facing `make help` line, or doc meant for **operators** should be checked against [§1](#1-radio-ham-cb-ptt-and-vox) and [§2](#2-the-new-space-race-coding-agents-on-a-moon-and-back-run-for-software-engineering) (see [AGENTS.md](../AGENTS.md) for the same rule in contributor policy).

See also: [AGENTS.md](../AGENTS.md), [README.md](../README.md), and [architecture.md](architecture.md).
