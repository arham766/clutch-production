# Fixalong HLD 05 — Repair State Machine (the brain)

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** — (orchestrates all)
**Depends on:** Voice (03), Vision (04), Retrieval (02), Safety (06), TTS (07)
**Consumed by:** the whole loop

---

## 1. Purpose

The orchestrator. It is the single owner of `RepairState`, **continuously updated by the
camera** (the primary stream) and intermittently by voice (the control surface). It decides
what to retrieve, **whether to speak at all**, what to say, and in what tone — walking the
user through the repair graph one safe step at a time. This is what makes Fixalong an
*ambient* copilot that watches the repair unfold, not a chatbot that waits for questions.

## 2. Responsibilities
1. Own `RepairState` (HLD 00 §5.1), **updated continuously and silently by vision** (04).
2. Treat **voice as control**: intent, observations, danger — and **resolve referents**
   ("it"/"this") against the live visual state.
3. Run the **speak gate** (§5): stay silent unless speaking is *useful, dangerous, or the
   next action is clear*.
4. Decide retrieval: **prefetch** on visual change; **query** on intent (→ 02).
5. Decide the next utterance + persona mode (07), using retrieved + verified context (06).
6. Manage graph traversal: current node → next node based on observations/conditions.
7. Enforce phase transitions, especially **hazard → warning** (from vision *or* voice).

## 3. State model

```
phase:  diagnosing → guiding → confirming → done
                  ╲        ↑         ↑
                   ╲       │         │
                    ╰──▶ warning ◀───╯     (any phase can jump to warning on a hazard)
```

- **diagnosing** — figure out the object + symptom + entry node.
- **guiding** — deliver the current step; await observation.
- **confirming** — verify the user did the step / saw the expected thing.
- **warning** — a hazard was observed or a high-risk step is next; speak the stop/caution
  first, in safety mode, before anything else. Pre-empts all other phases.
- **done** — goal reached → trigger repair receipt (09).

## 4. The update cycle — two entry points

Vision drives the loop continuously; voice interjects. **Both** update state, then consult
the **speak gate** — the agent is silent by default.

```python
# PRIMARY: fires on every gated VisualObservation (04) — continuous, usually silent
def on_visual_observation(obs):
    state.update_from_vision(obs)                  # visible_parts, panel_state, hazards
    if obs.hazards:            state.phase = "warning"
    moss.prefetch(build_query(state))             # warm context for what's in view (02)
    advance_node()                                 # graph traversal on visual conditions
    if should_speak(trigger="vision"):            # §5 speak gate
        return emit_utterance()
    return None                                    # stay silent, keep watching

# CONTROL: fires when the user speaks (intent / observation / danger)
def on_final_transcript(text):
    intent = classify(text)                        # question | observation | danger | ack
    referent = resolve_referent(text, state)       # "it"/"this" → live visible part
    state.add_observation(text, referent)
    if intent == "danger":     state.phase = "warning"
    advance_node()
    if should_speak(trigger="voice", intent=intent):
        return emit_utterance()
    return None

def emit_utterance():
    query  = build_query(state)                    # → 02 (often a prefetch cache hit)
    chunks = [c for c in retrieve(query) if gate(c) != REFUSE]   # 02 + FACT (06)
    draft  = compose(state, chunks)                # template or Claude, grounded only
    safe   = guardrail(draft, state, chunks)       # TrueFoundry (06)
    return safe.text, persona_mode(state.phase)    # → 07
```

## 5. The speak gate (`should_speak`) — *when* to talk

Ambient = mostly silent. The agent speaks **only** when at least one holds:
- **Danger** — a hazard appeared (vision or voice) → always speak, `safety` mode, pre-empts all.
- **Clear next step** — the state advanced to a node with a confident, single next action
  the user hasn't done yet.
- **User asked / expressed intent** — a question or explicit request.
- **Confirmation needed** — ambiguous/low-confidence state where acting blind is risky.

Otherwise: **stay silent and keep watching.** Anti-chatter guards: don't repeat the last
utterance; debounce (don't speak twice within N seconds unless danger); don't narrate every
visual change — only ones that change the *advice*.

## 6. Signal fusion & referent resolution
- **Hazard from either source wins** → jump to `warning`.
- **Vision is primary** for *what is present*; **voice tie-breaks** when they disagree on a part.
- **Referent resolution:** "it / this / that one" → the highest-confidence current
  `visible_part` (Beat B of the winning scene). If ambiguous → `confirming`.
- **Low-confidence vision** → `confirming` ("is that the shiny black wheel?") rather than acting.
- **Memory:** salient observations + actions accumulate **in `RepairState`** (in-memory), folded
  into `rolling_summary`, and fed to compose — so later turns recall earlier context ("you
  mentioned a torn fragment earlier") **without** a vector index. See §8.5. (Moss `remember()` is
  an optional Tier-2 ephemeral session index, not the runtime path.)

## 7. Graph traversal

`advance_node()` chooses the next `RepairNode` by matching observations/conditions (from
**vision or voice**) against the current node's `conditions` → `next[]` edges. `preconditions`
let it **skip** branches already handled (rear panel already open — seen on camera — → skip
to the persistent-jam node). Unknown observations → stay, ask a clarifying question, or widen
retrieval.

## 8. Composition (what to say)
- Tier 0: **templated** utterances filled from retrieved chunks (deterministic, fast, safe).
- Tier 1+: **Claude via TrueFoundry** (06) drafts from the **recall context + chunks** (§8.5),
  constrained to only use retrieved/verified content (no outside knowledge), then guardrailed.

### 8.5 Recall model — how the agent "remembers" the conversation
Recall is **in-memory structured state**, not a Moss index. What's fed to `compose()` each turn:
1. **`RepairState`** (structured) — current `phase`, `visible_parts`, `hazards`, `constraints`.
2. **Graph cursor** — `current_node_id` (where we are) + the active node's step/warnings.
3. **Recent-N `user_observations`** — the last N salient observations (default N≈8).
4. **`rolling_summary`** — a compact "session so far" (problem, cause-so-far, steps done,
   warnings shown), updated as the repair progresses.

This is ample for a repair's scale (minutes; dozens of *salient* items → a few K tokens → fits
the model context easily) and is **exact** for the categorical recall that matters ("what step",
"what have you done"). Two rules keep recall sharp:
- **Salient-only observations.** Store deduped *state changes* (new part, new symptom, hazard,
  user action) — **never raw per-frame vision output** (the gate already drops ~80–95%). Raw
  frame spam would poison recall.
- **Bounded.** `user_observations` is capped; older detail is folded into `rolling_summary`.

**Conversation is NOT indexed in Moss at runtime.** Moss holds the *manual*; the chat lives in
`RepairState`. Optional **Tier-2** semantic memory (long/enterprise sessions): a **separate,
ephemeral per-session** Moss index (`session-<id>`), debounce-batched, deleted at session end,
multi-index-queried alongside the manual — **never** written into the shared `fixalong-<model>`
manual index. Not needed for the demo.

## 9. Interfaces
```python
class StateMachine:
    # primary: continuous, silent unless the speak gate fires
    def on_visual_observation(obs: VisualObservation) -> Optional[(text, mode)]
    # control surface: voice intent / observation / danger
    def on_final_transcript(text: str) -> Optional[(text, mode)]
    def resolve_referent(text, state) -> Optional[part]   # "it"/"this" → live visible part
    def should_speak(trigger, intent=None) -> bool        # the speak gate (§5)
    def add_observation(text, referent=None) -> None      # store SALIENT, deduped (§8.5)
    def recent_observations(n=8) -> list[dict]            # last-N salient → compose
    def update_rolling_summary() -> None                  # fold older detail into state.rolling_summary
    def note_interrupt() -> None                          # barge-in → phase=warning consideration
    def is_done() -> bool
```

## 10. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Ambiguous state | ask one clarifying question; stay in current node |
| No retrieval hit | widen query; if still empty, say "the manual doesn't cover that — let me flag it" (never invent) |
| Vision/voice conflict | vision primary for *presence*; voice tie-breaks; confirm verbally if unsure |
| Referent unresolved ("it" with no clear visible part) | ask "which part — the shiny wheel?" instead of guessing |
| Speak gate too chatty / too quiet | tune triggers + debounce window; bias toward speaking on danger, silence otherwise |
| Claude composition unavailable | fall back to templated utterances from chunks |

## 11. Open questions
- Hand-authored FSM vs LLM-driven planning for traversal. Default: deterministic FSM over the
  repair graph for the demo (predictable on stage); LLM only for phrasing.
- Speak-gate tuning: how aggressively to pre-empt to `warning` (false positives annoy; misses
  are dangerous — bias to caution), and how talkative to be in `guiding`.
