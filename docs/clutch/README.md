# Clutch — Live Video Support for Hardware

*Software support got solved by chatbots; hardware support didn't, because hardware problems are
physical and visual.* **Clutch** is a customer-service agent that can **see** the problem. It
embeds on a company's support page as a chat widget with three modes:

- **Type** — a normal support chatbox for quick questions.
- **Talk** — a live voice conversation.
- **See** — a live camera session: the customer shows their device, the agent identifies it and
  talks them through the fix in real time — like FaceTiming a technician who has memorized the
  entire manual and is available 24/7.

It works because the agent is **grounded in the company's own documentation** — it pulls the exact
relevant step from the official docs, so instructions are accurate and safe, never improvised.

> **Why now:** voice models are fast + cheap, and real-time retrieval matured with **Moss**. The
> bottleneck was never the docs — it was *seeing the problem and pulling the right step in the
> moment.* Both just got solved.

---

## Two flows

### 1. Company setup (onboarding — cold, self-serve)
```
enterprise logs in → uploads manuals/spec sheets
   → Unsiloed parses them → Moss indexes them (real-time semantic retrieval)
   → we derive a per-company PRODUCT CATALOG (one entry per product, for closed-set ID)
   → Clutch generates a one-line embed snippet
That's the whole integration: log in, upload docs, drop in one line.
```

### 2. Customer support session (runtime — hot, in the widget)
```
customer on the company's (generic) support page → opens the widget
   ├─ Type / Talk: text/voice question → Moss retrieval (scoped to company) → grounded answer
   └─ See (live camera, FRESH context):
        identify(frames, company_catalog) → { product_id, problem_signals }   ← vision does real work
           (multi-product company + generic page ⇒ vision must classify the product + spot the problem)
        → Moss retrieval scoped to clutch-<company>/<product>, driven by the problem
        → grounded, step-by-step fix over LiveKit (voice + visual), first-try resolution
```

## Architecture

```
  COMPANY (setup)                         CUSTOMER (runtime, embedded widget)
  docs ─Unsiloed─▶ Moss index            page ─embed snippet─▶ LiveKit room (chat/voice/video)
       └─▶ product catalog                       │
                                                 ▼
                                   identify (Qwen): product (closed-set) + problem
                                                 ▼
                                   Moss retrieve (scoped to company+product) — grounded
                                                 ▼
                                   agent answers: text / voice / on-feed guidance
   model calls routed through TrueFoundry AI Gateway (fallback, cost, guardrails)
```

## Stack (sponsors)
| Piece | Tech |
|---|---|
| Chat / voice / live video transport + embeddable widget | **LiveKit** |
| Reasoning brain (grounded answers, all modes) | **MiniMax** (via TrueFoundry gateway) |
| Visual understanding of the customer's feed (See) | **Qwen-VL** (via TrueFoundry gateway) |
| Voice I/O (STT + TTS) | **Cartesia** (LiveKit plugins) |
| Parse uploaded manuals/spec sheets | **Unsiloed** |
| Real-time semantic retrieval over the company corpus | **Moss** (host) |
| Model routing (+ agent governance / guardrails) | **TrueFoundry** AI Gateway (Agent Gateway = enterprise/governance layer) |

## Locked decisions
- **Multi-product per company** → closed-set product classification (over the company catalog).
- **Generic support page** (not a product page) → no product context from the page.
- **Fresh "See" context** → vision must identify product **and** problem when the customer hits See.
- **TrueFoundry = model routing** (AI Gateway); Agent Gateway = per-company governance/guardrails (stretch). 
- **FACT dropped** — grounding-in-docs is the accuracy guarantee; cryptographic provenance is out of scope.
- **Catalog requirement:** onboarding derives a per-company product catalog (ideally a reference
  image per product, via Unsiloed-extracted images) — needed for reliable closed-set ID.

## Reuse from built code
- **Unsiloed → Moss ingestion** = the company corpus build. *Already built* in `ingest/`.
- **Perception (`identify`)** — built in `src/perception/` (closed-set product ID + problem from frames).
- **Retrieval/grounding + LiveKit** build on the Moss SDK and the `livekit-moss-vercel` scaffold (`moss-research/`).
- Not used: `find_manual`/MCP (the company uploads docs), AWS. (MiniMax = reasoning brain; Cartesia = voice I/O.)

## Status / what we're building
- **Now:** `identify(frames, catalog) → IdentifyResult` (product closed-set ID + problem signals),
  in `src/perception/` — provider-agnostic, mock-first, Qwen-via-TrueFoundry-Gateway adapter.
- **Next:** per-company catalog (onboarding), multi-tenant Moss indexing, the embed widget + 3 modes.

Fresh Clutch HLDs/LLDs will grow under `docs/clutch/` as the design converges.
