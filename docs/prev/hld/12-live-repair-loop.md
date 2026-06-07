# Fixalong HLD 12 — Live Repair Loop (the hot path)

**Status:** Draft v0.1 · **Build tier:** 0 · **Phase:** Flow 3 of 3 — *routed by [HLD 13 Session Orchestrator](13-session-orchestrator.md)* (hot, per session)
**Sponsors:** LiveKit · Qwen · KriNein · MiniMax · TrueFoundry (guardrails) · (+ Moss/FACT via **HLD 11**)
**Latency:** end-to-end perceived < a few hundred ms; retrieval <10 ms, verify <1 ms
**Consumes:** a signed Moss index `fixalong-<model>` produced by **HLD 10 → HLD 11**
**Reuses:** `ingest/fact_moss.py` (verify), `livekit-moss-vercel` scaffold patterns

> **Self-contained.** Restates the schemas it uses. It does **not** cover how the manual was
> acquired/indexed (HLD 10) or how chunks are signed/retrieved/verified internally (HLD 11) —
> it *calls* HLD 11's `retrieve` + `verify_document`. This is the **ambient** loop: the camera
> is the primary stream; voice is the control surface; the agent is mostly silent.

---

## 1. Purpose

Run the **ambient repair session**: watch the repair unfold via the camera, silently track
state, retrieve verified manual context as the scene changes, and **speak only when it's
useful, dangerous, or the next action is clear**.

> **The camera is the state. Voice is just the control surface.**

## 2. Scope
**In:** continuous perception, state tracking, referent resolution, retrieval+verify (via
HLD 11), the **speak gate**, grounded composition + TrueFoundry output guardrail, persona TTS,
barge-in safety, the UI surface, the repair receipt.
**Out:** onboarding/manual acquisition (HLD 10); signing/indexing/retrieval internals + the
verdict mechanics (HLD 11).

## 3. Preconditions (session start)
1. HLD 10→11 has produced a signed index `fixalong-<model>` (or onboarding runs first).
2. `load_session_index("fixalong-<model>")` (HLD 11) → in-process, ~3–5 ms queries.
3. `trusted_issuers = {KB_ISSUER}` (the key that signed the index).
4. Init `RepairState` (phase=`diagnosing`); open the LiveKit room (mic + **camera on**).

## 4. The ambient loop

```
   ┌──────────────────────────── LIVE LOOP (per session) ─────────────────────────────┐
 cam ═══▶ KriNein pHash gate ═══▶ Qwen → VisualObservation     ── ALWAYS ON (primary) ──│
 mic ───▶ LiveKit STT ───▶ intent / observation / "danger"     ── intermittent (control)│
   │                 │                       │                                          │
   │                 ▼                       ▼                                          │
   │        Repair State Machine: update RepairState (silent) · resolve "it"/"this"     │
   │                 │  prefetch on visual change · build query on intent               │
   │                 ▼                                                                   │
   │        HLD 11.retrieve(state) ── <10 ms ──▶ Chunk[]                                 │
   │                 ▼                                                                   │
   │        HLD 11.verify_document(...) ── <1 ms ──▶ verdict per chunk                   │
   │                 │  drop REFUSE · flag ESCALATE/HEDGE                                │
   │                 ▼                                                                   │
   │        ┌─ SPEAK GATE ─ speak only if: danger │ clear next step │ asked │ confirm ─┐ │
   │        ▼  (else: stay silent, keep watching)                                       │ │
   │   compose (grounded-only) → TrueFoundry guardrail → safe text                      │ │
   │        ▼                                                                           │ │
   │   MiniMax/Nova Sonic TTS (persona mode) ──▶ speaker   ·   barge-in: stop + warning │ │
   └──────────────────────────── UI: viewfinder + cards + chips + banner ───────────────┘
```

Two entry points, both ending at the speak gate:

```python
# PRIMARY: continuous, usually silent
def on_visual_observation(obs):
    state.update_from_vision(obs)                 # parts, panel state, hazards
    if obs.hazards: state.phase = "warning"
    HLD11.prefetch(build_query(state))            # warm context for what's in view
    advance_node()
    return emit() if should_speak("vision") else None

# CONTROL: voice intent / observation / danger
def on_final_transcript(text):
    intent   = classify(text)                     # question | observation | danger | ack
    referent = resolve_referent(text, state)      # "it"/"this" → live visible part
    state.add_observation(text, referent)
    if intent == "danger": state.phase = "warning"
    advance_node()
    return emit() if should_speak("voice", intent) else None

def emit():
    chunks = [c for c in HLD11.retrieve(state)
              if HLD11.verify_document(c.text, c.metadata, trusted_issuers={KB_ISSUER}) != REFUSE]
    draft  = compose(state, chunks)               # grounded ONLY in verified chunks
    safe   = truefoundry_guardrail(draft, state, chunks)   # output safety (HLD 06)
    return safe.text, persona_mode(state.phase)
```

## 5. Continuous perception (camera = primary)

Camera is always on. Each frame passes the **KriNein pHash gate** (sub-ms, runs on every
frame; drops ~80–95% redundant frames; SSIM/LPIPS excluded as too slow; optional CLIP semantic
tier on survivors), then surviving frames go to Qwen, rate-limited to ~1–2 VLM calls/sec.

```jsonc
VisualObservation {
  "visible_object":"rear paper path",
  "candidate_parts":[{"name":"pickup roller","confidence":0.74}],
  "hazards":[{"type":"hot_surface","part":"fuser","confidence":0.6}],
  "next_retrieval_queries":["pickup roller cleaning","fuser warning"]
}
```
Qwen **describes and routes** (parts, hazards, queries) — it never gives repair advice. If the
camera drops, the loop **degrades** to voice narration through the same `update` interface.

## 6. State & referent resolution

```jsonc
RepairState {
  "object":{"type":"printer","model":"laserjet-pro-m404"},
  "current_node_id":"jam_rear_open", "phase":"diagnosing|guiding|confirming|warning|done",
  "visible_parts":[{"name":"pickup roller","confidence":0.74}],
  "user_observations":[{"text":"looks shiny","referent":"pickup roller"}],
  "hazards":[{"type":"hot_surface","part":"fuser"}], "actions_taken":[...], "constraints":[...]
}
```
- **Vision is primary** for *what's present*; **voice tie-breaks** on disagreement.
- **Referent resolution:** "it/this/that one" → highest-confidence current `visible_part`
  (the "it looks shiny" → pickup roller moment). Ambiguous → `confirming`.
- **Hazard from either source** → jump to `warning` (pre-empts everything).
- **Memory:** observations/actions upserted to the session index (HLD 11) for later recall.

## 7. Retrieve + verify (delegated to HLD 11)
`emit()` calls **HLD 11**: `retrieve(state)` (Moss, <10 ms; usually a warm prefetch hit) then
`verify_document(...)` per chunk (FACT, <1 ms). **Drop REFUSE**, flag ESCALATE/HEDGE. The
agent is grounded **only** in verified chunks — it cannot speak an unprovable fact.

## 8. The speak gate (`should_speak`) — *when* to talk
Ambient ⇒ mostly silent. Speak only when at least one holds:
- **Danger** — hazard from vision or voice → always, `safety` mode, pre-empts all.
- **Clear next step** — state advanced to a node with a confident, not-yet-done action.
- **User asked / intent** — a question or explicit request.
- **Confirmation needed** — ambiguous/low-confidence where acting blind is risky.

Else: stay silent and watch. Anti-chatter: no repeats; debounce (no double-speak within N s
unless danger); don't narrate every visual change — only ones that change the *advice*.

## 9. Compose + output guardrail
- **Compose** from verified chunks only (templated for Tier 0; Claude via TrueFoundry for
  Tier 1+, constrained to retrieved content — no outside knowledge).
- **TrueFoundry guardrail** (HLD 06, output safety): block/`safe_rewrite` unsafe instructions
  (e.g., require "unplug first" near wiring; require the hot-surface warning near the fuser).
  This is the OUTPUT check; HLD 11's FACT verify is the INPUT check — two stages.

## 10. Speak (persona) + barge-in
- **Persona modes** (HLD 07, MiniMax): `normal` (calm/short), `safety` (firm/clipped),
  `diagnosis` (explanatory), `confirm`, `expert`. Mode derived from `phase`. Streaming TTS for
  low first-audio latency; keep `safety` utterances short.
- **Barge-in (safety pivot):** LiveKit interruption stops speech instantly; "I smell burning"
  → `phase=warning` → retrieve `risk_level=high` stop-condition → guardrail forces "unplug
  first" → `safety` mode: *"Stop. Unplug it now."*

## 11. Latency budget
| Stage | Budget |
|---|---|
| pHash gate (per frame) | sub-ms |
| Qwen VLM (gated, ~1–2/s) | off the per-turn critical path |
| Moss retrieve (HLD 11) | <10 ms (often warm-cache via prefetch) |
| FACT verify (HLD 11) | <1 ms/chunk, offline |
| compose + guardrail | model-bound (kept short) |
| TTS first audio | streaming, low |

## 12. UI surface (HLD 09)
Camera viewfinder (part highlight) · auto-appearing context cards · **trust chip** (✅/⚠️/🛑/🚫
+ source §) from the verdict · **latency badge** (real Moss `time_taken_ms`) · red **warning
banner** on hazard · repair-progress · push-to-talk + Stop. Pushed agent→UI over the LiveKit
data channel.

## 13. Session end — repair receipt
On `phase=done`: emit a sourced, verified summary (problem, likely cause, steps completed,
warnings shown, verified sources §). The enterprise hook — a defensible record of the fix.

## 14. Data models (restated)
`RepairState` (§6), `VisualObservation` (§5), `Chunk` + `Verdict` (from HLD 11), persona
`mode` ∈ {normal, safety, diagnosis, confirm, expert}.

## 15. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| camera/vision drops | **degrade to voice narration** via the same `update` interface; loop unchanged |
| Moss/FACT (HLD 11) hiccup | HLD 11's own fallbacks (local cosine / REFUSE-empty); agent stays silent rather than guess |
| speak gate too chatty/quiet | tune triggers + debounce; bias to speak on danger, silent otherwise |
| referent unresolved | ask "which part — the shiny wheel?" instead of guessing |
| TTS/guardrail down | LiveKit default TTS / local safety wrapper (HLD 06/07) |
| Wi-Fi on stage | recorded canonical run |

## 16. Sponsor mapping
| Concern | Sponsor |
|---|---|
| voice room, STT, turn-taking, barge-in | LiveKit |
| vision observations | Qwen |
| frame gate (pHash) | KriNein (ours) |
| retrieve + verify | Moss + FACT (via HLD 11) |
| output guardrail + model routing | TrueFoundry |
| expressive persona TTS | MiniMax (+ Nova Sonic fallback) |

## 17. Open questions
- Deterministic FSM vs LLM planning for traversal (default FSM; LLM for phrasing only).
- Speak-gate tuning: warning pre-emption aggressiveness; talkativeness in `guiding`.
- Realtime speech-to-speech (Nova Sonic) vs discrete STT→LLM→TTS (discrete eases the output
  guardrail; default discrete for Tier 0).
