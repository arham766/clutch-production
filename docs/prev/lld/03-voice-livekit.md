# Fixalong LLD 03 — Voice / LiveKit loop
**Implements:** HLD 03 · **Module(s):** agent/voice/livekit_agent.py · **Build tier:** 0 · **Status:** Draft v0.1

## 1. Responsibility
Own the realtime LiveKit room and the audio↔control surface of one repair call. This module
wires `AgentSession` (STT + VAD + turn detection + LLM-shim + TTS), subscribes to the user's
**mic** and a **continuous video track**, and dispatches three callbacks: `on_video_frame`
(→ perception 04), `on_final_transcript` (→ state machine 05), and `on_user_interrupt`
(barge-in → stop TTS + note interrupt). It publishes agent→UI events as JSON over the room
data channel, and exposes `say(text, mode)` which delegates synthesis to persona (07). It does
**not** decide *what* to say (05), retrieve (02), or pick a voice (07). The "LLM" slot of the
SDK pipeline is intercepted so our deterministic orchestrator, not a raw model, produces speech.

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/voice/livekit_agent.py` | `entrypoint(ctx)`, `RepairAgent(Agent)`, `VoiceLoop`, `build_session()`, `publish_event(room, type, data)` |

`VoiceLoop` is the object the orchestrator (13) holds. Public methods:
`start_session(object_hint) -> SessionState`, `on_final(cb)`, `on_partial(cb)`,
`on_frame(cb)`, `on_interrupt(cb)`, `say(text, mode)`, `stop()`, `attach_video(track)`,
`end_session()`. Callbacks are async; defaults are wired in §6.

## 3. Dependencies
- **SDK:** `livekit-agents>=0.11`, `livekit-rtc`; plugins `livekit-plugins-deepgram` (STT),
  `livekit-plugins-silero` (VAD), `livekit-plugins-anthropic` (LLM swap, §5), turn-detector
  plugin (`livekit.plugins.turn_detector`). TTS is **not** an SDK plugin — see §5/§8.
- **Internal:** `agent/contracts.py` (`SessionState`, `PersonaMode`, `Phase`);
  `agent/voice/persona.py` (07, `say`/`stop`); `agent/perception/vision.py` (04, via cb);
  `agent/state/machine.py` (05, via cb); `agent/orchestrator.py` (13, owner).
- **Env:** `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`,
  `ANTHROPIC_API_KEY` (or `TRUEFOUNDRY_GATEWAY_URL` + key), `FRAME_SAMPLE_HZ`.

## 4. Data structures (beyond shared contracts)
```python
@dataclass
class UIEvent: type: str; data: dict        # serialized to JSON, reliable=True on data channel
# event types: "agent_speech", "agent_silent", "phase", "hazard", "frame_observed",
#              "interrupt", "moss_context", "session_end", "error"
@dataclass
class VoiceCallbacks:
    on_frame:     Callable[[rtc.VideoFrame], Awaitable[None]]
    on_partial:   Callable[[str], Awaitable[None]]
    on_final:     Callable[[str], Awaitable[None]]
    on_interrupt: Callable[[], Awaitable[None]]
PersonaMode = Literal["normal","safety","diagnosis","confirm","expert"]   # from contracts
```
`mode` is chosen by 05 from `Phase`; this module only forwards it to 07.

## 5. Key functions

```python
def build_session() -> AgentSession:
    return AgentSession(
        stt=deepgram.STT(model="nova-2", interim_results=True, endpointing_ms=180),
        llm=AnthropicShim(),                       # see below — NOT a real model call
        tts=None,                                  # persona (07) drives MiniMax directly
        vad=silero.VAD.load(min_silence_duration=0.18),
        turn_detection="vad",                      # snappy; bias to interrupt (HLD 03 §5)
        allow_interruptions=True,
        min_interruption_duration=0.10,            # 100ms of speech => barge-in
    )
```

**LLM swap to Claude / Anthropic.** The SDK expects an `llm=`; we never want a free-running
model on the live path. Two options, both documented:
- *Real plugin* (if ever needed for phrasing): `llm=anthropic.LLM(model="claude-opus-4-...")`,
  or point its `base_url` at the TrueFoundry gateway with the Anthropic-compatible route.
- *Shim (default, Tier 0):* subclass `Agent` and override `llm_node` so the SDK's "LLM" step
  yields **our** orchestrator text instead of generating. `on_final_transcript` already drove
  05 and stashed the result; `llm_node` simply streams that text (or nothing → silent).

```python
class RepairAgent(Agent):
    def __init__(self, loop): super().__init__(instructions=""); self._loop = loop
    async def llm_node(self, chat_ctx, tools, settings):
        text, mode = self._loop._pending_utterance or ("", "normal")
        self._loop._pending_utterance = None
        if not text:                      # speak gate (05) returned None → stay silent
            await publish_event(self._loop.room, "agent_silent", {})
            return                         # empty async-gen => no speech
        async for tok in self._loop.say(text, mode):   # 07 streams audio + yields text
            yield tok
```

**Getting video frames.** Subscribe to the remote participant's first video track and pull
frames from its stream, sampling at `FRAME_SAMPLE_HZ` (the heavy frame-diff gate lives in 04,
not here — we only throttle the firehose):
```python
async def _consume_video(self, track: rtc.VideoTrack):
    stream = rtc.VideoStream(track); interval = 1.0 / FRAME_SAMPLE_HZ; last = 0.0
    async for ev in stream:                       # ev.frame is an rtc.VideoFrame
        now = time.monotonic()
        if now - last < interval: continue
        last = now
        await self.cb.on_frame(ev.frame)          # → vision.observe_gated (04)
```

**say** — pure delegation to persona (07), no synthesis logic here:
```python
async def say(self, text: str, mode: PersonaMode):
    await publish_event(self.room, "agent_speech", {"text": text, "mode": mode})
    self._active_tts = persona.say(text, mode)    # AudioStream (07); MiniMax or fallback
    async for chunk in self._active_tts:
        yield chunk                                # streamed into the room by the SDK
```

## 6. Control flow / sequence
1. `entrypoint(ctx)` → `await ctx.connect()`; `loop = VoiceLoop(ctx.room)`; build callbacks
   (defaults below); `session = build_session()`; `await session.start(room, RepairAgent(loop))`.
2. **Mic path.** STT emits interim → `on_partial` → `moss.prefetch(...)` (02, speculative).
   STT emits final → `on_final` → `result = await state_machine.on_final_transcript(t)`; stash
   `loop._pending_utterance = result`; SDK then calls `llm_node` → `say` (or stays silent).
3. **Video path.** `on('track_subscribed')`: if `track.kind == VIDEO`, `attach_video(track)` →
   `_consume_video` → `on_frame` → `vision.observe_gated(frame)` → if obs,
   `state_machine.on_visual_observation(obs)` → if speak gate fires, `say(*result)`.
4. **Barge-in.** SDK fires `user_started_speaking`/interrupt → `on_user_interrupt` →
   `await persona.stop()` (flush synth buffer) → `await state_machine.note_interrupt()` →
   `publish_event("interrupt")`. The interrupting utterance then flows as a normal final.
5. `end_session()` → `publish_event("session_end", receipt)` → `await session.aclose()`.

Default callbacks bind 04/05 exactly as HLD 03 §4. Both speech and frame paths funnel through
05's **speak gate**; most invocations return `None` and the agent stays quiet (publishes
`agent_silent` for UI affordance).

## 7. Config & tuning
| Param | Default | Why |
|---|---|---|
| `endpointing_ms` (STT) | 180 | snappy finals; terse repair speech |
| `min_silence_duration` (VAD) | 0.18s | fast turn end |
| `min_interruption_duration` | 0.10s | bias to interrupt over finishing (HLD 03 §5) |
| `allow_interruptions` | True | safety-critical |
| `FRAME_SAMPLE_HZ` | 2 | throttle before 04's gate; tune up for fast motion |
| utterance max chars | 220 (`normal`/`safety`) | first-word latency + easy interrupt (07 §4) |

## 8. Error handling & fallbacks
- **TTS down (MiniMax).** `persona.say` raises/empties → catch in `say`, fall back to the SDK
  default TTS path (`tts=silero`/`cartesia` or AWS Nova Sonic, 07 §6); `publish_event("error",
  {"stage":"tts","fallback":"default"})`. Never drop a `safety`-mode utterance — retry on
  fallback synchronously.
- **STT mishears terse part words.** Not corrected here; 05 confirms ("the shiny black wheel —
  the pickup roller?") before acting (HLD 03 §8). This module just forwards finals.
- **Turn detection too eager/lazy.** Tune endpoint silence; UI exposes a push-to-talk button
  (09) that calls `on_final` directly, bypassing VAD.
- **Vision callback throws.** Swallow per-frame, log, `publish_event("error",{"stage":"vision"})`;
  never kill the room over one bad frame.
- **Fail-safe default:** on any uncertainty the agent stays silent — `_pending_utterance` left
  `None` ⇒ empty `llm_node` ⇒ no speech (HLD 00; README §Errors).

## 9. Latency / perf notes
- **Barge-in is the budget that matters.** Target stop-to-silence < ~150ms: `allow_interruptions`
  + `min_interruption_duration=0.10` detects fast; `persona.stop()` must flush the synth buffer
  immediately so no clipped tail plays after a "stop" (07 §7). Measure from VAD interrupt event
  to last audio frame; assert < 200ms in tests.
- STT interim → prefetch makes Moss (02) effectively free by final.
- TTS streamed (07) so first-audio is low; cap utterance length (§7).
- Hot path: frame sample → 04 gate (most frames dropped) → cold only on change.

## 10. Test plan
Unit (no live room; mock SDK):
- **Mock transcripts:** feed strings to `on_final`; assert `state_machine.on_final_transcript`
  called and `_pending_utterance` set; when 05 returns `None`, assert `llm_node` yields nothing
  and `agent_silent` published.
- **Mock frames:** push fake `rtc.VideoFrame`s through `_consume_video`; assert sampling honors
  `FRAME_SAMPLE_HZ` and `on_frame` count matches; assert a throwing vision cb doesn't propagate.
- **Barge-in:** fire interrupt; assert `persona.stop()` awaited, `note_interrupt()` called,
  `interrupt` event published; with a fake clock assert stop-latency < 200ms.
- **say delegation:** stub `persona.say` to yield; assert `agent_speech` published with `mode`;
  stub it to raise → assert default-TTS fallback used and `safety` utterance still spoken.
- **publish_event:** assert JSON-serializable payload, `reliable=True`, on `local_participant`.
Integration: LiveKit sandbox room + recorded mic/video fixtures → assert full loop and the
discrete pipeline swap (set `tts=` real plugin) still drives `say` correctly.

## 11. Build checklist
1. **Tier 0:** `build_session()` + `entrypoint` + `RepairAgent` shim with empty `llm_node`
   (silent agent) — proves room, mic, STT finals.
2. Wire `on_final` → mock 05 returning canned text; verify `say` → persona (07) speaks.
3. Add `attach_video`/`_consume_video` + `on_frame` → 04; verify frame sampling.
4. Implement barge-in (`on_user_interrupt` → `persona.stop` + `note_interrupt`); measure latency.
5. `publish_event` + all UI event types over the data channel (09 consumes).
6. TTS fallback path + `error` events; push-to-talk hook for 09.
7. Optional: swap shim → real Anthropic/TrueFoundry plugin; evaluate Nova Sonic (07 §6).
