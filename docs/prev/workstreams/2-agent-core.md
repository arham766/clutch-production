# Workstream 2 — Agent Core + Integrations  *(the integrator)*

**Owner:** ____ · **Skills:** backend / systems · **Mission:** the brain + the glue. Decide
onboard-vs-live, track repair state, decide *when/what* to speak, wire everyone together, and own
the **TrueFoundry gateway** (MCP `find_manual`, model routing, output guardrail) + **Unsiloed**
parsing.

> Write `agent/contracts.py` + `agent/config.py` FIRST (hour 0) — one import + one config source
> for all four parts. Copy the dataclasses from [`../lld/README.md`](../lld/README.md).

## You own
- **LLDs:** [13 orchestrator](../lld/13-orchestrator.md), [05 state machine](../lld/05-state-machine.md),
  [12 live loop](../lld/12-live-loop.md), [06 safety](../lld/06-safety.md) (**guardrail + MCP**),
  the Unsiloed-parse integration.
- **Modules:** `agent/contracts.py`, `agent/config.py` (env + Moss/FACT/LLM/Unsiloed singletons),
  `agent/orchestrator.py`, `agent/session.py`, `agent/state/machine.py`, `agent/state/referent.py`,
  `agent/safety/guardrail.py`, `agent/integrations/mcp.py` (`find_manual`, `discover_tools`,
  model `route`), `agent/integrations/unsiloed.py` (`parse_manual`, wraps built `unsiloed_client`),
  `agent/live/wiring.py` (the `Deps` seam).
- **You own the recall model** (LLD 05 §5.6.5): recall = `RepairState` (structured) + graph
  cursor (`current_node_id`) + **recent-N salient `user_observations`** + **`rolling_summary`**,
  all fed into `compose()`. Keep observations **salient-only** (deduped state changes, never raw
  frames) and bounded (fold older detail into `rolling_summary`). Conversation is **not** indexed
  in Moss at runtime; that's enough recall for a repair's scale.

## Interfaces you PRODUCE
```python
run_session(stream)                                  # top-level entry
route(identity) -> ONBOARD | LIVE | CLARIFY          # cache-check decision
on_visual_observation(obs) -> Optional[(text, mode)] # state-machine entry points (Part 3 wires these)
on_final_transcript(text) -> Optional[(text, mode)]
should_speak(trigger, intent=None) -> bool           # the speak gate
guardrail(draft, state, chunks) -> SafetyDecision    # output safety (.allowed/.safe_rewrite)
# integrations Part 1's onboard consumes:
find_manual(brand, model, object_type, *, scope=None) -> ManualRef     # TrueFoundry MCP
parse_manual(manual_ref) -> UnsiloedResult                            # Unsiloed
route(task) -> ModelHandle                                            # model routing via gateway
# shared:
agent/contracts.py   # dataclasses   |   agent/config.py   # singletons + env
```

## Interfaces you CONSUME (stub on hour 0, swap when ready)
- From **Part 1:** `retrieve(state, obs)`, `verify_chunk(c)`, `load_session_index(slug)`,
  `index_exists(slug)`, `persist(chunks, slug)`, `observe(frame)`, `identify_product(frames)`,
  `onboard(...)`, `build_repair_graph`/`graph_to_chunks`.
- From **Part 3:** `say(text, mode)`, `persona_mode(phase)`, the LiveKit room (Part 3 wires your
  entry points to its callbacks).

## External deps / keys
TrueFoundry gateway (`TRUEFOUNDRY_*`) — MCP + model routing + guardrail. Unsiloed
(`UNSILOED_API_KEY`). Claude (via the gateway) for compose + guardrail.

## Build order
- **Tier 0:** `contracts.py` + `config.py`; `route` stub→LIVE on the pre-baked model; the state
  machine `emit()` thread (retrieve→verify→**templated** compose→`(text,mode)`); `should_speak`
  + anti-chatter; `resolve_referent`.
- **Tier 1:** `find_manual` (MCP discovery) + `parse_manual` (Unsiloed) so Part 1's `onboard`
  runs; `guardrail` (local rules + TrueFoundry); Claude compose (grounded-only); `advance_node`;
  full lifecycle FSM; `onboard_then_ready`.
- **Tier 2:** continuous re-route (`on_identity_change` debounce/hysteresis); degradation routing;
  single-flight onboarding; model routing per task.

## Critical correctness (from the interface audit — don't regress)
- `verify_chunk(c)` **stamps `c.verdict` as a string**; compare `c.verdict == "REFUSE"`.
- `SafetyDecision` has `.allowed`/`.safe_rewrite` (NOT `.verdict`/`.text`).
- `emit` calls `retrieve(self.state, obs=...)` (state, not a query object).
- `onboard(...)` returns an `OnboardResult` (`.identity/.model_slug/.chunks/.cache_hit`).

## Definition of done
`run_session` routes correctly; the live loop turns an `observe`/`transcript` into a verified,
guardrailed `(text, mode)` only when the speak gate fires; `find_manual`+`parse_manual` let
Part 1's `onboard` build a new index end to end. Integration test: scripted frames+transcripts →
expected speech/silence.

## Your demo moment
The flow feeling alive — **silence until it matters**, **pronoun resolution**, the **safety
pivot** (barge-in → "Stop, unplug it"), the **REFUSE**, and the **manual fetch** (`find_manual`)
during the live onboard of the 2nd machine.
