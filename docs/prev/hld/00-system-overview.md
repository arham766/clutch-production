# Fixalong HLD 00 — System Overview

**Status:** Draft v0.1 · **Build tier:** — · **Audience:** whole team

This is the master document. It defines the live loop, the component map, and the
**shared contracts** every component HLD depends on. Read this first.

---

## 1. Purpose

An **ambient repair copilot** that *watches the repair unfold*. The camera is on the whole
time and is the **primary, continuous context stream**; the system silently tracks the
physical state of the repair, retrieves grounded manual context as the scene changes, and
**speaks only when it's useful, dangerous, or the next action is clear**.

> **The camera is the state. Voice is just the control surface.**
>
> Fixalong is an ambient repair copilot that watches the physical state of the repair,
> listens for intent or danger, and only speaks grounded steps when the next action is safe
> and relevant.

## 2. The core insight — vision-first, not voice-first

Most repair assistants *wait for the user to explain the problem*. Fixalong **watches the
repair unfold**. The state changes that matter are observed primarily through **video**, not
narration:

> rear panel opens · a glossy roller comes into view · a torn fragment appears · a hand nears
> the fuser · a cable is unplugged

Each of those **changes which instruction, warning, diagram, or branch is relevant** — and
most of them happen *without the user saying anything*. So the architecture is a continuous
loop driven by the camera:

- **The camera continuously updates `RepairState`** (gated cheaply by KriNein pHash so the VLM
  runs only on real change — HLD 04). This is the main signal.
- **Every meaningful visual change is a retrieval/prefetch event** — Moss (<10 ms) keeps
  context warm for what the user is *looking at*, before they say anything.
- **Voice is the control surface, not the input of record.** Speech expresses *intent*
  ("how do I clean it?"), *observations* ("it looks shiny"), or *danger* ("I smell burning")
  — and the State Machine resolves pronouns like "it"/"this" against the live visual state.
- **The agent is mostly silent.** It speaks only on a trigger: danger, a clear next step, an
  explicit question, or a needed confirmation (HLD 05 §"when to speak").

## 3. The live loop

```
 ┌──── SESSION ORCHESTRATOR (HLD 13) ── the entry point ────┐
 │  identify machine (vision+OCR) → known model? (HLD 11 cache)
 │     ├─ yes ────────────────────────────────▶ enter LIVE  │
 │     └─ no  ─▶ ONBOARD (HLD 10 → HLD 11 write) ─▶ LIVE     │   …keeps re-routing if the
 └──────────────────────────│──────────────────────────────┘     machine changes
                            ▼
   ┌──────────────────────── THE AMBIENT LOOP (HLD 12) ───────────────────────┐
   │                                                                           │
 cam ═══▶ KriNein pHash gate ═══▶ Qwen (frame → observation)   ── ALWAYS ON ── │  primary stream
   │        (drops ~80–95%)        parts · panel state · hazards               │
 mic ───▶ LiveKit STT ───▶ intent / observation / "danger"     ── intermittent│  control surface
   │                    │                  │                                   │
   │                    ▼                  ▼                                   │
   │            Repair State Machine (HLD 05)                                  │
   │     • camera continuously updates RepairState (silently)                  │
   │     • resolves "it/this" against live visual state                        │
   │     • emits prefetch on visual change; query on intent                    │
   │                    │                                                      │
   │                    ▼                                                      │
   │            Moss retrieve (HLD 11) ── <10 ms ──┐                           │
   │     prefetched for what the user is LOOKING AT │                          │
   │                    ▼                           │                          │
   │            FACT verify (HLD 11) ── only verified, fresh chunks            │
   │                    ▼                                                      │
   │            ┌─ SPEAK GATE (HLD 05) ─ speak only if: danger │ clear next    │
   │            │                         step │ user asked │ confirm needed   │
   │            ▼  (else stay silent, keep watching)                           │
   │     Claude (TrueFoundry gateway + output guardrails, HLD 06)              │
   │                    ▼                                                      │
   │     MiniMax / Nova Sonic TTS (urgency mode, HLD 07) ──▶ speaker           │
   │                    │                                                      │
   └────────────────────┴──────── UI: viewfinder + cards + chips (HLD 09) ─────┘

  OFFLINE (per model): manual → Unsiloed → repair graph (HLD 10) → FACT-sign + Moss index (HLD 11)
```

The orchestrator (13) routes into the loop; the camera path runs continuously; the mic path
fires only when the user speaks; and the **speak gate** keeps the agent quiet unless speaking is
useful, dangerous, or unambiguous. Retrieval + verification are owned by **HLD 11**.

## 4. Component map

| Component | Responsibility | HLD |
|---|---|---|
| Ingestion & Repair Graph | manual → structured, signed, indexed repair knowledge | 01 |
| Retrieval (Moss) | sub-10 ms context lookup keyed on live state | 02 |
| Voice & Realtime (LiveKit) | the hands-free call: STT, turn-taking, barge-in | 03 |
| Perception (Qwen + KriNein) | KriNein pHash gate → camera frame → structured observation + queries | 04 |
| Repair State Machine | the in-session brain: fuse signals, track state, decide what to say | 05 |
| Safety & Control | **output** guardrails (TrueFoundry) + routing/MCP — *FACT input verify is in HLD 11* | 06 |
| Voice Persona (MiniMax) | expressive TTS with urgency modes | 07 |
| Infrastructure (AWS) | enterprise knowledge backbone + fallback | 08 |
| Frontend / UX | camera view, context cards, trust/latency chips, receipt | 09 |

> **Above these components** sit the **Session Orchestrator (HLD 13)** — the entry controller
> that decides onboard-vs-live, owns the session lifecycle, and re-routes — and the **flow HLDs
> 10–12** (Onboarding, FACT × Moss, Live Loop) that compose components 01–09 into the cold/hot
> phases. The signing/indexing/retrieval/verification of FACT × Moss live in **HLD 11**.

## 5. Shared contracts (the spine)

These data shapes are the integration boundaries. Define them once here; every workstream
builds and mocks against them so teams can work in parallel.

### 5.1 RepairState — the single source of truth
```jsonc
RepairState {
  "session_id": "uuid",
  "object": { "type": "printer", "model": "HP LaserJet-style", "confidence": 0.8 },
  "goal": "clear persistent paper jam",
  "current_node_id": "jam_rear_panel",         // position in the repair graph
  "phase": "diagnosing|guiding|warning|confirming|done",
  "user_observations": [ { "text": "looks shiny", "ts": 0 } ],
  "visible_parts":     [ { "name": "pickup roller", "confidence": 0.78, "ts": 0 } ],
  "hazards":           [ { "type": "hot_surface", "part": "fuser", "confidence": 0.6 } ],
  "actions_taken":     [ "jam_open_rear" ],
  "constraints":       [ "do not touch transfer roller" ],
  "rolling_summary":   "compact 'session so far' — fed to compose with recent observations"
}
```
**Recall model:** the agent recalls via `RepairState` (structured) + the graph cursor
(`current_node_id`) + the **recent-N `user_observations`** + the **`rolling_summary`**, all fed
into the compose prompt. `user_observations` is **salient-only** (deduped state changes, not raw
frames). The conversation is **not** indexed in Moss at runtime — Moss holds the manual; recall
is in-memory state. (Optional Tier-2 semantic memory = a *separate ephemeral per-session* Moss
index, never the shared manual index — see HLD 05 §recall.)

### 5.2 RepairNode — a node in the repair graph (output of HLD 01)
```jsonc
RepairNode {
  "id": "jam_rear_panel",
  "trigger_phrases": ["paper jam", "already removed paper", "still says jam"],
  "preconditions": ["jam persists after paper removal"],
  "instructions": ["Open the rear access panel.", "Check the lower paper path."],
  "warnings": ["Do not touch the fuser assembly; it can be hot."],
  "parts": ["rear access panel", "pickup roller", "transfer roller", "paper sensor"],
  "hazards": [{ "type": "hot_surface", "part": "fuser" }],
  "conditions": ["torn fragment visible", "roller looks glossy"],
  "next": ["jam_fragment_removal", "roller_cleaning"],
  "source": { "section": "3.2", "page": 14 }
}
```

### 5.3 RetrievalQuery → Chunk[] (HLD 02)
```jsonc
RetrievalQuery {
  "object": "printer", "current_node_id": "jam_rear_panel",
  "user_observation": "black rubber wheel looks shiny",
  "visible_parts": ["pickup roller"],
  "need": ["part_identity", "warning", "next_step"]
}
Chunk {
  "id": "...", "type": "step|warning|part|error|diagram|branch",
  "text": "...", "part": "pickup roller", "risk_level": "low|medium|high",
  "source": { "section": "3.2", "page": 14 },
  "score": 0.86,
  "verdict": "ACT|HEDGE|ESCALATE|REFUSE"   // attached by the FACT gate (HLD 06)
}
```

### 5.4 VisualObservation (HLD 04)
```jsonc
VisualObservation {
  "visible_object": "printer rear paper path",
  "candidate_parts": [ { "name": "pickup roller", "confidence": 0.78 } ],
  "hazards": [ { "type": "hot_surface", "part": "fuser", "confidence": 0.6 } ],
  "next_retrieval_queries": ["pickup roller cleaning", "transfer roller warning"]
}
```

### 5.5 SafetyDecision (HLD 06) and Speak (HLD 07)
```jsonc
SafetyDecision { "allowed": true, "reason": "", "safe_rewrite": null, "required_warnings": [] }
speak(text: string, mode: "normal|safety|diagnosis|confirm|expert")
```

## 6. End-to-end sequence (the winning scene) — ambient

The point of the demo is **the user never has to name the part**. Fixalong already knows
what they're looking at from the live video.

**Beat A — ambient vision takes over (the user says nothing):**
```
user opens the rear panel  (silent)
  → camera frame passes KriNein pHash gate (real change) → Qwen
  → VisualObservation{ panel_state: rear_open, candidate_parts:[pickup_roller 0.74,
                       transfer_roller 0.51], hazards:[fuser_area 0.60] }
  → StateMachine: current_node=jam_rear_open; visible_parts=[…]; hazard=hot_surface_possible
  → Moss PREFETCH (driven by vision): rear-path diagram, pickup-roller inspection, fuser warning
  → FACT verifies chunks (ACT)
  → SPEAK GATE: new region + nearby hazard → speaking is useful → allow
  → Agent: "Good — I can see the rear paper path. Look at the black rubber wheel near the
            bottom. Don't touch the roller just below it."
  → UI: rear-path diagram appears automatically, pickup roller highlighted
```

**Beat B — voice resolves against live state (the real ambient moment):**
```
user: "It looks shiny."
  → StateMachine resolves the pronoun "it" using live visual state → referent = pickup_roller
  → RetrievalQuery{ part: pickup_roller, symptom:"shiny", need:[fault, step, warning] }
  → Moss (prefetched, ~7 ms): [symptom: glossy roller loses grip][step: clean w/ IPA]
                              [warning: avoid transfer roller]
  → FACT: all ACT · TrueFoundry guardrail: transfer-roller warning required → present
  → Agent (diagnosis mode): "That's the pickup roller. When it gets glossy it loses grip and
            keeps triggering jam errors even after the paper's gone. Clean it gently with
            isopropyl alcohol — but don't touch the black transfer roller just below it."
  → UI: part card + ✅ verified §3.4 + "7 ms" badge
```

> Beat B is not a chatbot answering a repair question — it's a copilot **resolving language
> against the live physical world**. The user said "it"; Fixalong knew what "it" was.

## 7. Execution plan (day-of)

Broad ambition, **spine-first**. A demo must exist at every hour; every integration has a
stub fallback (see each HLD §"Failure modes & fallbacks").

| Window | Goal |
|---|---|
| Define contracts (§5) + spine | `RepairState` + Moss retrieval + LiveKit voice loop over a pre-signed manual; state drivable by **either** vision or voice (same `update`) |
| Stand up the camera stream early | continuous frames → KriNein pHash gate → Qwen → `VisualObservation` → silent `RepairState` updates (this is the product, not a late add-on) |
| Each workstream real | Unsiloed ingest, vision-driven prefetch, speak-gate, safety gate, persona, UI |
| Integrate | the ambient winning scene end-to-end (Beat A vision-takeover + Beat B pronoun resolution) |
| Overnight | tune speak-gate + gate thresholds, persona urgency, polish, repair receipt |
| Morning | rehearse the ambient scene, record fallback, finalize receipt |

> Vision is **core**, brought up early — not an overnight add-on. The spine is built so
> `RepairState` can be updated by vision *or* voice through the same interface, so if vision
> degrades on stage the user can narrate and the rest of the loop is unchanged (see §8).

## 8. Failure modes & fallbacks (system level)

| If this breaks | Fallback |
|---|---|
| Live Unsiloed parse | pre-parsed manual JSON committed to repo |
| Qwen vision (primary input) | **graceful degradation:** user narrates what they see → same `RepairState.update` + retrieval path runs unchanged (demo survives, loses the "it already knows" wow) |
| Moss cloud | local in-memory cosine over the same chunks |
| TrueFoundry gateway | call Claude directly + the local safety wrapper |
| MiniMax TTS | LiveKit default TTS / Nova Sonic |
| Wi-Fi on stage | recorded canonical run |

## 9. Open questions
- Single printer model for the demo (lock the manual + repair graph early).
- Demo length target (protect the safety-pivot beat regardless).
- How much of the repair graph is auto-built (Unsiloed) vs hand-authored for the demo.
