# Fixalong HLD 13 — Session Orchestrator (the Controller)

**Status:** Draft v0.1 · **Build tier:** 0 · **Role:** the entry point / router **above** the three flows
**Depends on:** HLD 10 (onboard), HLD 11 (FACT × Moss), HLD 12 (live) · **Consumed by:** the app entry point
**Reuses:** glue over the built `ingest/` + `fact/`; optionally TrueFoundry observability

> **Self-contained.** Restates the lifecycle and decision contracts it owns. This is the
> **central system** that decides *which flow to run*. It is **not** the in-session repair
> brain — that's HLD 05 (what to say *within* live). 13 picks the flow; 05/12 run the live loop.

---

## 1. Purpose

One controller that, **per session and continuously**, answers: *"do we need to onboard this
machine first, or can we go straight to the live loop?"* — and owns the **session lifecycle**,
the **cold→hot handoff**, **mid-session re-routing** (the user points at a different machine),
and **cross-cutting degradation**.

```
            ┌──────────────── SESSION ORCHESTRATOR (HLD 13) ────────────────┐
 app open ─▶│  identify → known model?                                       │
            │     ├─ yes ─────────────────────────────▶ LIVE (HLD 12)        │
            │     └─ no  ─▶ ONBOARD (HLD 10 → HLD 11 write) ─▶ LIVE (HLD 12)  │
            │  …and keep watching: object/model changed? manual inadequate?   │
            │     → pause, re-route to ONBOARD for the new model, resume LIVE  │
            └────────────────────────────────────────────────────────────────┘
```

## 2. Scope
**In:** the route decision (onboard vs live), the session-lifecycle FSM, cold→hot transition &
its UX state, **continuous re-routing**, cross-cutting degradation routing, concurrency/cache
coordination, session-level observability.
**Out:** the flows themselves (HLD 10/11/12); *what to say* in a live repair (HLD 05); manual
acquisition internals (HLD 10); sign/index/retrieve/verify internals (HLD 11).

## 3. Two nested controllers (avoid the overlap)

| Controller | Scope | Decides | Doc |
|---|---|---|---|
| **Session Orchestrator** | the whole session | **which flow** (onboard / live / clarify), lifecycle, re-route, degradation | **HLD 13 (this)** |
| **Repair State Machine** | within the live loop | what to retrieve, **whether/what to speak**, graph traversal | HLD 05 / HLD 12 |

13 wraps 12; 12 contains 05. The orchestrator never decides repair content — it decides
*context*: which model's index is loaded and trusted, and whether we're cold or hot.

## 4. The session-lifecycle FSM

```
        ┌─────────┐   point camera   ┌─────────────┐  known model   ┌────────┐
        │  IDLE   │ ───────────────▶ │ IDENTIFYING │ ─────────────▶ │ READY  │
        └─────────┘                  └─────────────┘                └────────┘
                                        │ unknown                       │ load index
                                        ▼                               ▼
                                   ┌────────────┐  index built    ┌──────────┐
                                   │ ONBOARDING │ ───────────────▶│   LIVE   │◀─┐
                                   └────────────┘                 └──────────┘  │
                                        │ fail → degrade               │        │ re-identify
                                        ▼                               └────────┘ (new model)
                                   ┌──────────┐                          │
                                   │ DEGRADED │◀── any cross-cut fault ──┘
                                   └──────────┘            │ user ends
                                                           ▼
                                                       ┌────────┐
                                                       │ ENDED  │ → repair receipt (HLD 12 §13)
                                                       └────────┘
```
`DEGRADED` is an overlay, not a dead end — the session keeps running with reduced capability
(see §8) and recovers when the fault clears.

## 5. The route decision (the core "onboard vs live")

```python
def route(identity: ProductIdentity) -> Route:
    if identity.confidence < ID_THRESHOLD:
        return CLARIFY                      # ask the user which machine/model
    if HLD11.index_exists(slug(identity.model)):
        return LIVE                         # cache hit → straight to the hot loop
    return ONBOARD                          # cold path first, then live

def run_session(stream):
    identity = HLD10.identify_product(stream.frames)        # vision ID + OCR model
    match route(identity):
        case CLARIFY:  identity = ask_user_for_model(); return run_session_with(identity)
        case ONBOARD:  index = onboard_then_ready(identity) # HLD 10 → HLD 11 (write)
        case LIVE:     index = HLD11.load_session_index(slug(identity.model))
    go_live(index, identity)                                # HLD 12, trusted_issuers={KB_ISSUER}
```

- **The decision input** is the `ProductIdentity` from vision (HLD 10 §4). The **decision rule**
  is the cache check against HLD 11. That's the whole "when to onboard vs live."
- **Single-flight onboarding:** if two sessions hit the same unknown model at once, only one
  onboarding runs; the other awaits the same index (concurrency, §9).

## 6. Cold→hot transition (UX of the wait)

Onboarding takes seconds; the orchestrator owns that interval so it never feels broken:
- enter `ONBOARDING` → UI state "Identifying… getting the manual… reading it…" (mirrors the
  HLD 10 steps).
- optionally let the user **start describing the problem** while it loads (buffer observations
  into `RepairState` so the first live turn is already warm).
- on `index ready` → `load_index` (pre-warm) → seamless switch to `LIVE`.
- demo: known model ⇒ this interval is ~0 (pre-onboarded); a 2nd machine shows the real flow.

## 7. Continuous re-routing — the ambient superpower

The camera is **always** identifying (cheap, via the perception gate). The orchestrator
watches for three re-route triggers and **loops back to onboarding without ending the session**:

| Trigger | Detection | Action |
|---|---|---|
| **Different machine** | `ProductIdentity.model` changes with confidence | pause LIVE → ONBOARD new model (or cache hit) → resume LIVE on the new index |
| **Wrong/inadequate manual** | retrieval persistently empty / low-score for the active model | re-identify; try an alternate manual via MCP (HLD 10 §5) |
| **Stale knowledge** | active model's `fresh_until` lapsed (firmware/bulletin) | re-onboard to refresh the index |

> This is what makes it feel ambient: *"point at the printer, then walk to the espresso machine
> — Fixalong follows you."* Each machine's index is built once and cached (HLD 11), so the
> second visit is instant.

**Anti-flapping:** require N consistent frames + a confidence margin + a debounce before
switching models, so a stray frame doesn't bounce the session between machines.

## 8. Cross-cutting degradation routing

The orchestrator is the single place cross-flow fallbacks are decided (each flow also has local
fallbacks; 13 coordinates them):

| Fault | Orchestrator routing |
|---|---|
| vision down | stay LIVE, tell HLD 12 to use **voice narration** (same `update` interface) |
| Moss/FACT (HLD 11) down | HLD 11 local fallback; if unusable → DEGRADED, agent stays silent vs guessing |
| MCP/manual fetch fails (onboard) | CLARIFY → ask user for the manual / model; or generic same-class manual (flagged) |
| onboarding fails entirely | DEGRADED live with no index → agent can only relay user-narrated facts, no grounded steps |
| signing key mismatch | everything reads ESCALATE → surface "knowledge not trusted," prompt re-onboard |

## 9. Concurrency, caching & observability
- **Cache coordination:** the per-model index (`fixalong-<model>`) is the cache; 13 checks it
  (HLD 11 `index_exists`) before onboarding and reuses across sessions.
- **Single-flight:** in-flight onboarding per model is deduped; concurrent requests await it.
- **Pre-warm:** `load_index` on transition to READY so the first live query is hot.
- **Observability:** emit phase transitions, route decisions, re-routes, degradations
  (route through TrueFoundry's gateway/observability if available) — useful for the demo
  dashboard and the enterprise story.

## 10. Data model

```jsonc
SessionState {
  "session_id":"uuid",
  "lifecycle":"idle|identifying|onboarding|ready|live|degraded|ended",
  "identity": ProductIdentity,                  // HLD 10 §4
  "active_index":"fixalong-laserjet-pro-m404",  // current model's index (HLD 11)
  "kb_issuer":"did:key:z…",                     // trusted issuer for verification
  "degraded":["vision"],                         // active degradation flags (empty = healthy)
  "repair": RepairState                          // owned by HLD 05/12 once live
}
```

## 11. Interfaces
```python
run_session(stream) -> None                      # top-level entry; drives the lifecycle
route(identity) -> ONBOARD | LIVE | CLARIFY      # the core decision (§5)
onboard_then_ready(identity) -> index_name       # HLD 10 → HLD 11 write, single-flight
go_live(index_name, identity) -> None            # enter HLD 12 with trusted_issuers
on_identity_change(new_identity) -> None         # §7 re-route (debounced)
set_degraded(flag) / clear_degraded(flag)        # §8 cross-cut routing
```

## 12. Latency
- Route decision: a cache check — **milliseconds** (the common path is LIVE, instant).
- Onboarding (cold): seconds (HLD 10/11) — only on cache miss / new model.
- Live: <10 ms per turn (HLD 12). The orchestrator adds negligible overhead on the hot path.

## 13. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| identity wrong / low conf | CLARIFY with user before onboarding (avoid fetching wrong manual) |
| model flaps between machines | hysteresis + debounce (§7) |
| onboarding mid-session stalls | keep LIVE on the previous model; surface "still getting the new manual…" |
| cache/registry unavailable | treat as miss → onboard (slower but correct); never serve an untrusted index |
| total knowledge loss | DEGRADED: agent narrates/relays only, never invents grounded steps |

## 14. Sponsor mapping
| Concern | Sponsor |
|---|---|
| route decision input (identify) | Qwen (HLD 10) |
| cache check / index | Moss (via HLD 11) |
| phase/route observability | TrueFoundry (optional) |
| (everything else is our orchestration glue) | — |

## 15. Open questions
- Should the user be able to **lock** the active model (disable auto re-route) for a focused fix?
- Re-route thresholds (frames/confidence/debounce) — tune to avoid flapping vs being sticky.
- Buffer-while-onboarding: how much pre-live user input to accept and replay on go-live.
- Where the orchestrator runs (LiveKit agent process vs a separate controller service).
