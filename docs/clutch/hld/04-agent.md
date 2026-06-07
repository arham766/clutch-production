# Clutch HLD 04 — Conversation Agent (the multi-modal brain)

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** TrueFoundry (reasoning LLM routing) · LiveKit (turns)
**Depends on:** Perception (02), Retrieval & Grounding (03), Gateway (06)
**Consumed by:** Realtime, Voice & Widget (05)

---

## 1. Purpose
The integrator. One agent serves **Type / Talk / See** over a single `SupportSession`: it owns
session state, and on every turn decides *whether to retrieve*, composes a **grounded** answer
(cited from the company's docs), and responds in the active modality. Its signature move is
**modality escalation** — when text/voice can't resolve a clearly physical problem, it proactively
says *"hit See and show me"*, consumes the resulting `IdentifyResult`, and scopes retrieval to the
identified product. Grounding is non-negotiable: it states only retrieved facts and escalates
rather than invent.

## 2. Scope
**In:** `SupportSession` ownership (state, phase, history, rolling summary); per-turn decide
retrieve→compose→respond; modality-escalation logic; consuming `IdentifyResult` (incl.
`needs_confirmation`); grounding/safety discipline; in-memory recall.
**Out:** transport / STT / TTS / camera (05); vision inference (02); the Moss query + cited compose
*mechanics* (03 — the agent calls them); model hosting/routing (06).

## 3. Design (how it works; key decisions)
The agent is a deterministic **FSM** with a **speak gate** + in-memory recall — a *multi-modal
support agent*. Key shift: Type/Talk are **turn-driven** (user speaks →
agent answers, like a chatbot), while See is **ambient** (camera streams; agent mostly silent,
speaks on the speak gate) — the same gate logic, scoped per modality.

**Phase model** (per session):
```
greeting → assisting → confirming → resolved
                 ╲         ↑
                  ╰──▶ escalating ──▶ (See | human)
```
- **assisting** — answer text/voice questions, grounded.
- **confirming** — product ambiguous (`IdentifyResult.needs_confirmation`) → "is this the X or the Y?".
- **escalating** — text/voice insufficient *or* problem clearly physical → suggest **See**; if still
  unresolvable (no doc coverage / unsafe) → hand to a human.
- **resolved** — issue closed.

**Per-turn loop** (`on_user_turn` for type/voice text; `on_identify` when the user hits See):
1. fold input into `SupportSession.history` (salient, deduped) + rolling summary.
2. **decide retrieve?** — small talk / acks → no retrieval; anything answerable from docs → yes.
3. build `RetrievalQuery{company_id, text, product_id?, need[]}` → 03 returns `Chunk[]`/`Answer`.
4. **compose grounded** — answer only from chunks, with citations; if chunks empty/low-score →
   escalate (don't invent).
5. **maybe escalate modality** (§ below) → emit `Answer` in active modality to 05.

**Modality escalation (the killer UX).** After composing, evaluate an *escalation trigger*:
- text/voice answer is **insufficient** (empty/low-score retrieval, or the problem is described in
  physical/visual terms — "the light is blinking red", "a part fell off"), **and** modality ≠ video
  → respond with the grounded partial answer **plus** the nudge *"Want to just show me? Hit **See**
  and point your camera at it."*
- When the customer hits See, **consume `IdentifyResult`** (02):
  - `needs_confirmation == True` → phase `confirming`, ask "is this the *X* or the *Y*?" (from
    `candidates`); **don't scope retrieval yet**.
  - else → set `session.product_id`, fold `IdentifyResult.problem` (`ProblemSignals.to_query()`)
    into the next query, scope all retrieval to company+product, continue in `assisting`.

**Grounding & safety discipline.** Compose strictly over retrieved `Chunk`s; every claim cited
(`Answer.citations`). Not in docs → say so and escalate to a human (HLD 00 §4). For hardware steps,
**never** emit a step without its doc's warnings (unplug/power-off/PPE) — if a chunk implies a
dangerous action and the warning isn't present, withhold and escalate.

**Reasoning LLM** (default **MiniMax**) is the **Tier-1 composer**, reached only via the gateway
`reason.compose` logical name on the **TrueFoundry AI Gateway** (06) — so the brain is plug-and-play
(00 §7): swapping models is a route/config change, the FSM and `compose` call are untouched. Tier-0
fallback = template fill from the top chunk (deterministic, demo-safe).

## 4. Data & interfaces (reference 00 contracts)
**Owns:** `SupportSession` (00 §3) — `{session_id, company_id, modality, product_id?, phase,
history}`, plus in-memory scratch (`rolling_summary`, last-spoken, debounce ts — not persisted to Moss).
**Consumes:** `IdentifyResult` (02), `Chunk[]` / `Answer` (03).
**Produces:** `RetrievalQuery` (→ 03), `Answer{text, citations}` turns (→ 05).
```python
class ConversationAgent:
    def __init__(self, session: SupportSession, retrieve, compose, escalate, clock=...): ...
    def on_user_turn(self, text: str) -> Answer | None        # type/voice text → grounded turn
    def on_identify(self, result: IdentifyResult) -> Answer | None  # See: confirm or scope+answer
    def should_speak(self, trigger, intent=None) -> bool      # speak gate (ambient in See; on-turn else)
    def should_escalate_modality(self, answer, query, chunks) -> bool  # the "hit See" trigger
    def add_turn(self, role, text, refs=None) -> None         # SALIENT, deduped recall
    def recall_context(self) -> dict                          # state + recent-N + rolling_summary → compose
    def is_resolved(self) -> bool
```

## 5. Sequence / flow
1. **Type/Talk turn** → `on_user_turn(text)` → fold history → decide-retrieve → 03 `retrieve` →
   compose grounded (cited) → `should_escalate_modality?` → return `Answer` (+ optional See-nudge) → 05.
2. **User hits See** → 02 emits `IdentifyResult` → `on_identify`:
   `needs_confirmation` → ask which product (`confirming`); else scope `product_id` + fold `problem`
   → retrieve company+product → grounded step → 05 (voice + on-feed).
3. **In See (ambient)** → subsequent vision/voice signals fold into state; agent speaks only when
   the **speak gate** fires (clear next step / user asked / confirm needed / safety), silent otherwise.
4. **No coverage / unsafe** → phase `escalating` → "the docs don't cover that — connecting you to a
   person" (never invent).

## 6. Failure modes & fallbacks (fail-safe)
| Failure | Fallback |
|---|---|
| Retrieval empty / low-score | say "the docs don't cover that," offer See (if not video) or human; never invent |
| `IdentifyResult.needs_confirmation` | ask "is this the X or the Y?" before scoping — don't guess product |
| `IdentifyResult.product_id == None` | stay company-scoped (no product filter); ask user to reposition / name the product |
| Composer (MiniMax) down | Tier-0 template fill from top chunk via 06 fallback route |
| Dangerous step without doc warning | withhold the step, escalate to human (safety > completeness) |
| Speak gate too chatty (See) | debounce + no-repeat; bias to speak on safety, silence otherwise |
| Any exception in a turn | swallow → return `None` (no turn) so the session never crashes mid-support |

## 7. Sponsor mapping
- **TrueFoundry AI Gateway** — all reasoning-LLM (MiniMax) calls for compose route through 06
  (fallback, cost, guardrails). Agent never holds provider keys directly.
- **LiveKit** (05) — delivers the agent's `Answer` turns over chat/voice/video; supplies user turns.
- **MiniMax** — the reasoning brain (compose), routed via 06.
- **Qwen** (02) — vision `IdentifyResult` feeds the agent. **Cartesia** (05) — voice STT/TTS. Agent is provider-agnostic.

## 8. Reuse + Open questions
**Reuse:** built atop the LiveKit agent loop (05) and the Moss retrieval (03); the agent itself is
new code. Design pillars: deterministic **FSM**, `should_speak` **speak-gate** + anti-chatter
(debounce/no-repeat), `resolve_referent`, and the **recall model** — in-memory `SupportSession`
state + recent-N salient turns + rolling summary; **no Moss conversation index** (Moss holds the
*docs*). Grounding-via-citations is the accuracy guarantee (HLD 00 §4).
**Open questions:** (1) escalation-trigger tuning — how aggressively to push See (false nudges
annoy; missed ones lose the magic). (2) Hand-authored phase FSM vs LLM-planned turns — default FSM
for stage predictability, LLM for phrasing only. (3) Human handoff path (Agent Gateway) — Tier-2.

---
*Confirmation: HLD 04 — Conversation Agent written per the README template and 00 contracts; it integrates Perception (02), Retrieval (03), and Realtime (05) into one grounded, escalation-aware multi-modal support brain.*
