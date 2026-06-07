# Fixalong — High-Level Design Set

*An ambient repair copilot for the physical world.* You point your phone at a broken machine
and Fixalong **watches the repair unfold** — the camera is on the whole time, silently
tracking the physical state, retrieving the right step/warning/part as the scene changes, and
**speaking only when it's useful, dangerous, or the next action is clear**.

> **Thesis:** Manuals are static. Repairs are live. **The camera is the state; voice is just
> the control surface.** Most repair assistants wait for the user to explain the problem —
> Fixalong watches the repair unfold. Every meaningful visual change is a **retrieval event**.

## How to read these docs

Start with **[00 — System Overview](00-system-overview.md)**: it defines the live loop,
the component map, and the **shared contracts** every other doc builds on. Each component
HLD below is self-contained and references those contracts rather than redefining them.

| # | HLD | Component | Sponsor | Build tier |
|---|---|---|---|---|
| 00 | [System Overview](00-system-overview.md) | Architecture, live loop, shared contracts, execution plan | — | — |
| 01 | [Ingestion & Repair Graph](01-ingestion-and-repair-graph.md) | Manual → structured repair graph → signed Moss index | Unsiloed (+FACT) | 0/1 |
| 02 | [Retrieval](02-retrieval-moss.md) | Session index, state→query, speculative prefetch | Moss | 0 |
| 03 | [Voice & Realtime](03-voice-livekit.md) | STT→LLM→TTS loop, turn-taking, barge-in | LiveKit | 0 |
| 04 | [Perception / Vision](04-perception-vision-qwen.md) | **Always-on camera** → KriNein pHash gate → observations → silent state + queries | Qwen + KriNein | 0/1 |
| 05 | [Repair State Machine](05-repair-state-machine.md) | The orchestrator / brain — vision-driven state + **speak gate** | — | 0 |
| 06 | [Safety & Control](06-safety-and-control.md) | FACT input verification + TrueFoundry output guardrails | FACT + TrueFoundry | 1 |
| 07 | [Voice Persona](07-voice-persona-minimax.md) | Urgency modes, expressive TTS, fallback | MiniMax (+Nova Sonic) | 1/2 |
| 08 | [Infrastructure](08-infrastructure-aws.md) | Enterprise knowledge backbone, deploy, fallback | AWS / Bedrock | 2 |
| 09 | [Frontend / UX](09-frontend-ux.md) | Camera view, context cards, trust/latency chips, receipt | — | 0/1 |

### Flow HLDs — end-to-end phases (these compose the component HLDs above)

| # | HLD | Phase | Latency |
|---|---|---|---|
| **13** | [**Session Orchestrator (Controller)**](13-session-orchestrator.md) | **the entry point** — decides onboard vs live; lifecycle; re-routing | ms (route decision) |
| 10 | [Onboarding & Manual Acquisition](10-onboarding.md) | **cold** — vision ID → TrueFoundry MCP → Unsiloed → repair-graph chunks | seconds, once per model |
| 11 | [FACT × Moss — Knowledge & Trust](11-fact-moss-knowledge-trust.md) | sign + index + cache (write) · retrieve + verify (read) | sec (write) · <10 ms (read) |
| 12 | [Live Repair Loop](12-live-repair-loop.md) | **hot** — camera → state → retrieve (verified) → speak | <10 ms, every turn |

**HLD 13 is the central controller** that sits above the flows: it identifies the machine,
checks the HLD 11 cache, and routes to **onboard (10→11)** for a new model or straight to
**live (12)** for a known one — and keeps re-routing if the user points at a different machine.
Then: **10** produces chunks → **11** signs+indexes (write) & serves retrieve+verify (read) →
**12** runs the live loop. (13 picks the flow; **HLD 05** is the in-session brain that decides
what to *say*.) Read **13 → 10 → 11 → 12** for the full story; **01–09** for components.

## Execution principle — *broad ambition, spine-first*

We integrate all sponsors, but in an order where **a working demo exists at every hour**
and **every integration has a stub fallback** so one flaky service can't zero the demo.

- **Tier 0 (the spine, must work):** the **always-on camera → state → Moss retrieval →
  speak-gated voice** loop over a pre-signed manual, driven by the Repair State Machine.
  Built so state is drivable by vision *or* voice through one interface (so vision can
  degrade to narration without breaking the loop).
- **Tier 1 (differentiators):** FACT safety/provenance gate, TrueFoundry guardrails,
  MiniMax urgency voice, CLIP semantic tier of the frame gate.
- **Tier 2 (wow / fragile):** AWS Nova Sonic realtime backend, enterprise knowledge backbone,
  offline KriNein receipt generation.

Each component HLD ends with **Failure modes & fallbacks** describing its stub.

## Reuse from this repo
- `ingest/` — Unsiloed → Moss pipeline (parse → chunk → index) → adapt for manuals (HLD 01).
- `ingest/fact_moss.py` — sign at ingest, verify at retrieval (HLD 06).
- `fact/` — FACT protocol library (provenance/trust).
