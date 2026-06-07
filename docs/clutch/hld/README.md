# Clutch HLDs

Fresh high-level designs for Clutch (live video support for hardware). Read
**[00 — Overview](00-overview.md)** first: it defines the two flows, the component map, and the
**shared contracts** every HLD references (don't redefine them).

| HLD | Component | Owner | Build tier |
|---|---|---|---|
| [00](00-overview.md) | System overview + shared contracts | — | — |
| [01](01-onboarding-corpus.md) | Onboarding & Corpus (upload → Unsiloed → Moss + catalog → snippet) | | 0 |
| [02](02-perception.md) | Perception (vision: product ID + problem) | | 0 |
| [03](03-retrieval-grounding.md) | Retrieval & Grounding (Moss + cited compose) | | 0 |
| [04](04-agent.md) | Conversation Agent (multi-modal brain) | | 0 |
| [05](05-realtime-voice-widget.md) | Realtime, Voice & Widget (LiveKit + Cartesia voice + embed) | | 0 |
| [06](06-gateway-infra.md) | Gateway, Multi-tenancy & Infra (TrueFoundry + tenancy + deploy) | | 1 |

## Build plan (four-way work division)
All per-engineer briefs live in the **[`engineers/`](engineers/)** subfolder. Read
**[10 — Work Division & the Fusing System](engineers/10-work-division-and-fusing.md)** for how the
seven HLDs split into four equal, non-colliding sections that fuse through one seam per layer.
Per-engineer briefs:

| Section | Engineer | HLDs | Brief |
|---|---|---|---|
| A — Experience (FE/Design) | **Arman** | 05 (client), 01 (console UI) | [11](engineers/11-arman-frontend-experience.md) |
| B — Platform (Auth/DB/API) | **Arham** | 01, 06 | [12](engineers/12-arham-platform-infra.md) |
| C — Cognition (Vision/Orchestrator) | **Tonmoy** | 02, 04 | [13](engineers/13-tonmoy-vision-orchestrator.md) |
| D — Inference (Retrieval/Voice serving) | **Fardin** | 03, 05 (server) | [14](engineers/14-fardin-inference-retrieval-voice.md) |

## HLD template (every HLD uses these sections)
```
# Clutch HLD NN — <Component>
**Status:** Draft v0.1 · **Build tier:** N · **Sponsor:** …

## 1. Purpose (one paragraph — what this part does, bounded)
## 2. Scope (in / out)
## 3. Design (how it works; key decisions)
## 4. Data & interfaces (signatures it PRODUCES / CONSUMES; reference 00 contracts)
## 5. Sequence / flow
## 6. Failure modes & fallbacks (fail-safe behavior)
## 7. Sponsor mapping
## 8. Reuse (built code) + Open questions
```

## Conventions
- Python 3.11+, **uv** (root project `yc-hackathon`, code under `src/`). Frontend widget = JS/TS.
- Stack: LiveKit (transport) · MiniMax (brain) · Qwen (vision) · Cartesia (voice I/O) · Unsiloed · Moss · TrueFoundry gateway.
  All end models (brain/vision/STT/TTS/parse) are **defaults behind swap points** — see the
  **model-portability guarantee** (00 §7); a vendor change is one adapter or config line, never a rewrite.
- **Grounding guarantee:** agent states only facts from retrieved company docs, cited; never invents.
- **Multi-tenant:** one Moss index + catalog per company; every session scoped by the company key.
- Built/reusable: `ingest/` (Unsiloed→Moss, *done*), `src/perception/` (identify, *done*),
  the `livekit-moss-vercel` scaffold in `moss-research/`.
