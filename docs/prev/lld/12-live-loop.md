# Fixalong LLD 12 — Live loop wiring
**Implements:** HLD 12 · **Module(s):** agent/voice/livekit_agent.py + agent/state/machine.py (glue) · **Build tier:** 0 · **Status:** Draft v0.1

## 1. Responsibility
The **composition layer** of the hot path: it owns *no domain logic of its own* — it wires the
already-specified component modules (LLDs 02 retrieve, 03 LiveKit/persona transport, 04
perception, 05 state machine + speak gate, 06 guardrail, 07 persona, 11 retrieve/verify) into a
single ambient loop. Concretely it: (a) establishes session preconditions (load signed index,
pin `trusted_issuers`, init `RepairState`); (b) registers the two LiveKit async entry points and
routes them into `machine.py`; (c) implements the shared `emit()` path that threads
retrieve→verify→speak-gate→compose→guardrail→persona→data-channel; (d) handles barge-in,
graceful degradation, and the per-turn latency budget. Internals of each stage live in the
referenced LLD — this file must **not** duplicate them.

## 2. Files & public surface
| File | Exports | Role |
|---|---|---|
| `agent/voice/livekit_agent.py` | `entrypoint(ctx)`, `LiveAgent` | LiveKit job: subscribes mic+camera, owns transport + entry points, holds DI container |
| `agent/state/machine.py` | `on_visual_observation(obs)->Emission?`, `on_final_transcript(text)->Emission?`, `emit(state)->Emission` | the two entry-point bodies + shared emit (logic from LLD 05; wiring here) |
| `agent/live/wiring.py` | `Deps` (dataclass), `build_deps(session)->Deps` | dependency-injection container assembled at session start |
| `agent/live/emission.py` | `Emission` (dataclass) | the `(text, persona_mode, ui_payload)` result |

## 3. Dependencies
- **Internal:** `knowledge/retrieve.retrieve` + `retrieve.prefetch` (02), `knowledge/verify.verify_chunk` (11 read — stamps `Chunk.verdict` as a string), `knowledge/index.load_session_index` (11), `perception/gate` + `perception/vision.observe` (04), `state/machine` + `state/referent` (05), `safety/guardrail.truefoundry_guardrail` (06), `voice/persona.speak` + `persona.persona_mode` (07), `contracts` (RepairState, VisualObservation, Chunk, Verdict, SessionState).
- **Libs/SDKs:** `livekit.agents` (JobContext, AgentSession, VAD/STT, VideoStream, interruption), `asyncio`.
- **Env:** `KB_ISSUER` (signing key id — pinned as the sole trusted issuer), `TRUEFOUNDRY_*`, `LIVEKIT_*`, `VLM_RATE_HZ` (default 2). Routed/read by the underlying LLDs; this layer only passes them through `Deps`.

## 4. Data structures (beyond shared contracts)
```python
# agent/live/emission.py
@dataclass
class Emission: text: str; persona_mode: PersonaMode; ui_payload: dict   # None ⇒ stay silent

# agent/live/wiring.py — the DI container (one per session, immutable)
@dataclass
class Deps:
    index: str                                  # from load_session_index (11)
    trusted_issuers: set[str]                    # {KB_ISSUER} — pinned once
    retrieve: Callable[[RepairState], list[Chunk]]      # 02
    prefetch: Callable[[str], None]                      # 02 (fire-and-forget)
    verify:   Callable[[str, dict, set[str]], Verdict]   # 11 read
    observe:  Callable[[VideoFrame], Awaitable[VisualObservation]]  # 04
    guardrail: Callable[[str, RepairState, list[Chunk]], SafeText]  # 06
    speak:    Callable[[str, PersonaMode], Awaitable[None]]         # 07
    publish:  Callable[[dict], Awaitable[None]]          # data-channel → UI (09)
```
`machine.py` reads `deps` and `state` from a per-session context object set by `livekit_agent.py`
(no globals). All component callables are bound at session start so the hot path does zero import
or config work.

## 5. Key functions

### 5.1 Session preconditions — `entrypoint(ctx)` (cold, once)
```python
async def entrypoint(ctx: JobContext):
    session: SessionState = ctx.proc.userdata["session"]   # handed off by orchestrator (LLD 13)
    deps = build_deps(session)                              # see 5.2
    state = session.repair                                  # RepairState, phase="diagnosing"
    agent = LiveAgent(deps, state)
    await ctx.connect()                                     # room: mic + CAMERA ON
    agent.bind(ctx.room)                                    # register entry points (5.3)
```
```python
# agent/live/wiring.py
def build_deps(session) -> Deps:
    slug = f"fixalong-{session.identity.model}"
    rc = RetrieveCtx(client=moss_client, index=load_session_index(slug))  # 02/11; in-proc 3–5 ms queries
    trusted = {session.kb_issuer}                                          # pin the index's issuer once
    return Deps(rc=rc, trusted_issuers=trusted,
                retrieve=lambda s, obs=None: retrieve(rc, s, obs=obs),     # 02: retrieve(rc, state, obs)
                prefetch=lambda s, obs=None: prefetch(rc, s, obs=obs),
                verify=lambda c: verify_chunk(c, trusted_issuers=trusted), # 11: stamps c.verdict (str)
                observe=vision.observe, guardrail=truefoundry_guardrail,
                speak=persona.speak, publish=lambda p: room.local_participant.publish_data(json.dumps(p)))
```

### 5.2 Entry point A — camera (PRIMARY, continuous, usually silent)
```python
def bind(self, room):
    @room.on("track_subscribed")
    def _on(track, *_):
        if track.kind == VIDEO: asyncio.create_task(self._video_reader(track))
    self.stt.on("final_transcript", lambda ev: asyncio.create_task(self._on_voice(ev.text)))

async def _video_reader(self, track):
    async for frame in VideoStream(track):
        if not gate.passes(frame):           continue        # 04 KriNein pHash, sub-ms, drops 80–95%
        if not self.vlm_limiter.allow():     continue        # rate-limit ~1–2 VLM calls/s
        try:    obs = await self.deps.observe(frame)          # 04 Qwen → VisualObservation (off critical path)
        except VisionError:  self._degrade("vision"); continue   # → 8
        emission = on_visual_observation(obs)                # 05 body, below
        if emission: await self._render(emission)            # speak + UI
```
```python
def on_visual_observation(obs) -> Emission | None:           # logic = LLD 05; wiring here
    state.update_from_vision(obs)                            # parts/panel/hazards (vision primary)
    if obs.hazards: state.phase = "warning"
    for q in obs.next_retrieval_queries: deps.prefetch(q)    # warm Moss cache for what's in view (02/11)
    advance_node(state)
    return emit(state) if should_speak("vision") else None   # 05 gate
```

### 5.3 Entry point B — voice (CONTROL, intermittent)
```python
async def _on_voice(self, text):
    emission = on_final_transcript(text)
    if emission: await self._render(emission)

def on_final_transcript(text) -> Emission | None:
    intent   = classify(text)                               # question|observation|danger|ack (05)
    referent = resolve_referent(text, state)                # 05 → highest-conf visible_part
    state.add_observation(text, referent)
    if intent == "danger": state.phase = "warning"          # pre-empts (barge-in path, 5.5)
    advance_node(state)
    return emit(state) if should_speak("voice", intent) else None
```

### 5.4 Shared `emit()` — the retrieve→verify→guardrail→persona thread
```python
def emit(state) -> Emission:
    chunks = []
    for c in deps.retrieve(state):                          # 02 Moss, <10 ms (usually warm prefetch)
        deps.verify(c)                                      # 11 verify_chunk: stamps c.verdict (str), <1 ms
        if c.verdict == "REFUSE": continue                 # drop unprovable
        chunks.append(c)                                    # ESCALATE/HEDGE flagged, kept
    if not chunks: return None                              # nothing verified → stay silent, keep watching
    draft = compose(state, chunks)                          # grounded ONLY in verified chunks (templated T0)
    safe  = deps.guardrail(draft, state, chunks)            # 06 → SafetyDecision (may force "unplug first")
    if not safe.allowed: return None                        # blocked & unrewritable → stay silent / escalate
    text  = safe.safe_rewrite or draft                      # SafetyDecision.safe_rewrite (not .text)
    mode  = persona_mode(state.phase)                       # 07: warning→safety, diagnosing→diagnosis…
    ui    = _ui_payload(state, chunks, safe)                # trust chip + source§ + latency badge + banner
    return Emission(text, mode, ui)
```
```python
async def _render(self, e: Emission):
    await self.deps.publish(e.ui_payload)                   # push card/chip/banner to UI first (perceived latency)
    await self.deps.speak(e.text, e.persona_mode)           # 07 streaming TTS → speaker
```
**Two-stage trust:** FACT `verify` is the INPUT check; TrueFoundry `guardrail` is the OUTPUT
check — both must pass before any audio. Neither is re-specified here.

### 5.5 Barge-in (safety pivot)
```python
@room.on("user_started_speaking")
def _barge(_):
    self.deps.speak_stop()                                  # 07/03: LiveKit interruption stops TTS instantly
# the interrupting utterance then flows through _on_voice → if intent=="danger":
#   phase=warning → emit() retrieves risk_level=high stop-condition →
#   guardrail forces "unplug first" → persona_mode==safety → "Stop. Unplug it now."
```
Barge-in never blocks: stop-speech is synchronous, the new transcript is a normal voice turn.

## 6. Control flow / sequence
```
camera frame ─▶ gate(04) ─drop?─▶ [discard]
                 │pass + rate-ok
                 ▼  observe(04, off-path) ─▶ on_visual_observation(05) ─▶ update+prefetch+advance
mic final ──────────────────────────────▶ on_final_transcript(05) ─▶ classify+referent+advance
                 │                                   │
                 └────────────── should_speak? ──────┘  no ─▶ silent
                                   │ yes
                                   ▼ emit(): retrieve(02) ─▶ verify(11, drop REFUSE) ─▶
                                            compose ─▶ guardrail(06) ─▶ persona_mode(07)
                                   ▼ _render(): publish UI(09) ─▶ speak(07) ─▶ speaker
barge-in ─▶ speak_stop ─▶ (interrupting transcript re-enters on_final_transcript)
```

## 7. Config & tuning
| Param | Default | Owner |
|---|---|---|
| `VLM_RATE_HZ` | 2 | this layer (limiter) — keeps Qwen off the per-turn critical path |
| `DEBOUNCE_S` | 4 | speak gate (05) — no double-speak within N s **unless** danger |
| `trusted_issuers` | `{KB_ISSUER}` | pinned at 5.1; never regenerated mid-session |
| pHash threshold | per LLD 04 | gate — not re-tuned here |
Hot path does zero config reads after `build_deps`.

## 8. Error handling & fallbacks (fail-safe)
| Failure | Behavior (where) |
|---|---|
| camera/vision down | `_degrade("vision")` → add to `session.degraded`, stop `_video_reader`; voice narration drives `update_from_vision` via the **same** interface — loop logic unchanged (HLD 12 §15) |
| Moss/FACT hiccup | handled inside 02/11 (local cosine / REFUSE-empty); `emit()` sees empty/REFUSE chunks → returns None → **agent stays silent** rather than guess |
| guardrail/TTS down | 06/07 fallbacks (local safety wrapper / LiveKit default TTS); if guardrail unavailable, `emit()` returns None (no unchecked speech) |
| `kb_issuer ∉ trusted` | assertion at 5.1 aborts session → orchestrator (13) re-routes to onboarding |
| entry-point task raises | caught per-task; logged; loop continues (one bad frame/turn never kills the session) |
The invariant: **on any uncertainty, no audio.** Speech requires verified chunks AND a passed guardrail.

## 9. Latency / perf notes (per-turn budget)
| Stage | Budget | On critical path? |
|---|---|---|
| pHash gate / frame | sub-ms | yes (cheap) |
| Qwen VLM (gated ~1–2/s) | model-bound | **no** — async, off per-turn path |
| Moss retrieve (02) | <10 ms | yes (usually warm via prefetch) |
| FACT verify/chunk (11) | <1 ms | yes |
| compose + guardrail (06) | model-bound, kept short | yes |
| TTS first audio (07) | streaming, low | yes |
| **Perceived end-to-end** | **< a few hundred ms** | — |
Prefetch on visual change is what keeps retrieve warm; UI payload is published before TTS so the
card/chip appear before speech.

## 10. Test plan (drive the loop with scripted frames + transcripts)
**Mock at the `Deps` seam** (inject fakes): `observe` returns canned `VisualObservation`s,
`retrieve` returns canned `Chunk`s, `verify` returns scripted verdicts, `speak`/`publish` record
calls. State machine, speak gate, and `emit()` wiring run for real.
- **Integration — clear next step:** feed frame→obs(parts, no hazard) → assert prefetch called with `next_retrieval_queries`, `emit` retrieves+verifies, `speak` called once in `diagnosis`/`normal` mode, UI payload has trust chip + source §.
- **Integration — danger pre-emption:** obs with `hazards` → assert `phase==warning`, `speak` mode `safety`, banner in UI payload, fires even mid-`diagnosing`.
- **Integration — REFUSE drop:** `verify` returns REFUSE for all chunks → assert `emit()→None`, `speak` **never** called (silent).
- **Integration — ambient silence:** benign visual change, `should_speak`→False → no speak, no UI card (anti-chatter); debounce: two speak-worthy turns within `DEBOUNCE_S` (non-danger) → only first speaks.
- **Integration — barge-in:** start TTS, inject `user_started_speaking` → assert `speak_stop` called; feed "I smell burning" → assert retrieve of `risk_level=high`, guardrail forces "unplug first", `safety` mode.
- **Integration — referent:** obs(pickup roller conf .74) then transcript "it looks shiny" → assert observation referent == pickup roller; ambiguous (two equal parts) → `phase==confirming`, clarifying question.
- **Integration — vision degrade:** `observe` raises → assert `_degrade("vision")`, video reader stops, a voice "the panel is open" still drives `update_from_vision` and the loop emits.
- **Unit:** `build_deps` pins `trusted_issuers`; `kb_issuer` mismatch aborts. `_render` publishes UI before speak.
- **Scripted canonical run** (Wi-Fi fallback): a fixture sequence of `(frame|transcript)` events → asserted ordered list of `(verdict-set, speak text, persona_mode, ui_payload)` — the demo regression.

## 11. Build checklist (Tier-0 first)
1. `emission.py` + `Deps` dataclass; `wiring.build_deps` (load_session_index, pin issuers).
2. `livekit_agent.entrypoint` + `LiveAgent.bind`: subscribe camera/mic, register both entry points.
3. `_video_reader` with gate (04) + rate limiter; `_on_voice` hookup.
4. `on_visual_observation` / `on_final_transcript` bodies calling into LLD 05; `emit()` thread (02→11→compose→06→07).
5. `_render` (publish UI then speak); `_ui_payload` builder (chip/source/latency/banner).
6. Barge-in handler (`speak_stop` + re-entry).
7. Graceful degradation (`_degrade`, voice-narration path).
8. Integration harness with `Deps` fakes; scripted canonical run fixture.
