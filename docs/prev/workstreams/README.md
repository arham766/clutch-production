# Fixalong — Work Split (4 parts)

Four ownership areas so the team builds in parallel without stepping on each other. The only
things that cross part boundaries are the shared contracts (defined + interface-audited in
[`../lld/README.md`](../lld/README.md)).

## The four parts

| Part | Name | Owns (LLDs) | Skills | Doc |
|---|---|---|---|---|
| **1** | **Perception & Knowledge** *(camera → verified knowledge)* | 04, 10, 01, 02, 11 | ML / vision / data | [1](1-perception-knowledge.md) |
| **2** | Agent Core + Integrations *(the integrator)* | 13, 05, 12, 06 (guardrail **+ MCP `find_manual`**), **Unsiloed parse**, contracts, config | backend / systems | [2](2-agent-core.md) |
| **3** | Voice *(realtime + TTS)* | 03, 07 | realtime / voice | [3](3-voice.md) |
| **4** | Frontend & Infra | 09, 08 | frontend / deploy | [4](4-frontend-infra.md) |

```
                ┌──────────── 2 — AGENT CORE (integrator) ───────────┐
   3 ◀──────────┤ orchestrator · state machine · speak gate ·         ├──────────▶ 1
 VOICE          │ live wiring · output guardrail · contracts · config │     PERCEPTION &
 (LiveKit, TTS) │   run_session · route · emit                        │     KNOWLEDGE
   say()        └───────▲──────────────────────────▲─────────────────┘   observe/identify/
   callbacks            │ data-channel events       │                      onboard · retrieve/
                        ▼                            │                      verify · sign/index
                 4 — FRONTEND & INFRA               (consumes 1 + 3)
                 (UI cards/chips, deploy, env/secrets)
```
Part 2 is the hub: it consumes **1** (senses + memory) and **3** (voice), and feeds **4** (UI).

## The interface seam matrix (the contract between people — keep STABLE)

| Seam | Producer | Consumer | Signature (from the audited LLDs) |
|---|---|---|---|
| `observe` | 1 | 2, 3 | `observe(frame, hint) -> VisualObservation` |
| `identify_product` | 1 | 2 | `identify_product(frames) -> ProductIdentity` |
| `onboard` | 1 | 2 | `onboard(frames, *, session, emit) -> OnboardResult` |
| `find_manual` | **2** | 1 (onboard) | `find_manual(brand, model, object_type, *, scope=None) -> ManualRef` |
| `parse_manual` | **2** | 1 (onboard) | `parse_manual(manual_ref) -> UnsiloedResult` (TrueFoundry MCP + Unsiloed live in Part 2) |
| `retrieve` | 1 | 2 | `retrieve(state, *, obs=None) -> list[Chunk]` |
| `verify_chunk` | 1 | 2 | `verify_chunk(c, *, trusted_issuers) -> Chunk` (stamps `c.verdict` **str**) |
| `load_session_index` | 1 | 2 | `load_session_index(model_slug, *, client=None) -> str` |
| `index_exists` / `persist` | 1 | 1, 2 | `index_exists(slug) -> bool` / `persist(chunks, slug) -> index_name` |
| `contracts` / `config` | 2 | all | `agent/contracts.py`, `agent/config.py` (Moss/FACT/LLM singletons) |
| `run_session` / `route` | 2 | entry | `run_session(stream)` / `route(identity) -> ONBOARD\|LIVE\|CLARIFY` |
| `on_visual_observation` / `on_final_transcript` | 2 | 3 | `(obs)\|(text) -> Optional[(text, mode)]` |
| `guardrail` | 2 | 2 | `guardrail(draft, state, chunks) -> SafetyDecision` |
| `say` / `persona_mode` | 3 | 2 | `say(text, mode) -> AudioStream` / `persona_mode(phase) -> PersonaMode` |
| LiveKit callbacks / `publish` | 3 | 2, 4 | `on_video_frame`/`on_final_transcript`/`on_interrupt`; `publish(payload)` |
| UI events | 2, 3 | 4 | data-channel JSON: `context` / `state` / `hazard` / `receipt` |
| deploy / env+secrets | 4 | all | single-instance run, `.env` wiring |

A "verdict" anywhere in the agent is one of the 4 **strings** (`ACT/HEDGE/ESCALATE/REFUSE`).
`SafetyDecision` has `.allowed`/`.safe_rewrite` (not `.verdict`/`.text`). See the Verdict note
in [`../lld/README.md`](../lld/README.md).

## Hour-0 shared kickoff (~30 min, together)
1. **Part 2** writes `agent/contracts.py` (dataclasses from `../lld/README.md`) + `agent/config.py`
   (env + Moss/FACT/LLM singletons). Push immediately — one import for everyone.
2. Everyone **stubs the seams they consume** (return canned data) → all four build from minute one.
3. **Part 1** runs `ingest.py --sign` on one manual → a real signed Moss index `fixalong-<model>`
   + publishes `KB_ISSUER`. Unblocks Part 2 with a real `retrieve`/`verify` target and de-risks
   Unsiloed+Moss+FACT early.

## Tier order (spine-first)
- **Tier 0:** 1's index + `retrieve`/`verify` + `observe`/`identify` · 2's route-stub + state
  machine + `emit` · 3's LiveKit loop + `say` + barge-in · 4's UI shell (cards + trust chip +
  real latency badge). The pre-baked-machine demo runs.
- **Tier 1:** 1's full `onboard` + `find_manual` + repair-graph + cache · 2's guardrail + Claude
  compose · 3's persona urgency modes · 4's viewfinder + warning banner + receipt.
- **Tier 2:** live onboard of a 2nd machine + re-route · CLIP Tier-B gate · Nova Sonic · deploy.

Each part doc ends with a **Definition of Done** and its **demo moment**.
