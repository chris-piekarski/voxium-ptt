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

## 2) Apollo: humans, hardware, software, and robot stacks

**Why:** Voxium is about doing something **unprecedented on your own machine**: wiring **electrical** (mics, GPUs), **mechanical** (keyboards, chassis), and **software** (client, model, server) with **automation** (the stack as “robot work”: inference engines, schedulers) to reach a new outcome—**typing through voice** on the loop, locally.

**How to use it (examples, not a mandatory list):**

- A **mission** or **run** can frame a user session; **uncharted** and **first-flight** are fair for docs when describing *your* local stack, not a guarantee about the industry.
- **Stack**, **ground** (this machine you control), **on station / ready** for services; avoid confusing NASA role-play that obscures the actual client/server split.
- **Rockets** and **spacecraft** are **metaphors** for *systems* that do heavy, automated work; they are not literal product claims.

**Emphasis:** humans **steer**; robots **execute** the heavy, repeatable work (inference, mixing, I/O) within the path you set.

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

Changes that **only** affect internals may ignore this document. Any change to **user-visible** copy, dev-facing `make help` line, or doc meant for **operators** should be checked against [§1](#1-radio-ham-cb-ptt-and-vox) and [§2](#2-apollo-humans-hardware-software-and-robot-stacks) (see [AGENTS.md](../AGENTS.md) for the same rule in contributor policy).

See also: [AGENTS.md](../AGENTS.md), [README.md](../README.md), and [architecture.md](architecture.md).
