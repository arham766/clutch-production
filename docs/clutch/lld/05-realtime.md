# Clutch LLD 05 — Realtime & Voice (server side)

**Layer:** `src/realtime/` (Section D · Fardin) · **Implements:** [HLD 05](../hld/05-realtime-voice-widget.md) server half + [HLD 06](../hld/06-gateway-infra.md) token consumption
**Depends only on the Spine** (`src/contracts.py` + `src/events.py`). Everything else (`agent`, `identify`, `stt`, `tts`, tenancy) is **received as a passed-in argument** — this layer imports no sibling subfolder.
**Seams owned:** S1 (Browser⇄Realtime, server half) · S2 (Realtime⇄Agent) · S8 (Realtime→Perception). See [10 §4](../hld/engineers/10-work-division-and-fusing.md).

---

## 0. Scope of this LLD

This document specifies **only** `src/realtime/`: the LiveKit Agents worker that joins one room per
session, runs the **STT → Agent → TTS** loop with **barge-in**, **samples camera frames** for "See",
and **publishes the typed data-channel events** the widget renders. It does **not** specify `speech/`
(STT/TTS adapters — sibling, LLD 05b), `retrieval/`, `agent/`, `perception/`, tenancy, or the widget.
Those appear here only as **typed boundaries** (received callables / Spine shapes).

The independence contract (from [14 §intro](../hld/engineers/14-fardin-inference-retrieval-voice.md)):

```
realtime/ imports:      contracts.py, events.py            (Spine only)
realtime/ receives:     agent, identify, stt, tts, scope_session   (via run_session args)
realtime/ exposes:      run_session(...)  +  build_worker(...)     (the only public surface)
realtime/ tested-by:    scripts/realtime_mini.py (§9) — the ONE way to test the whole submodule:
                        boots a real local LiveKit + worker + Cartesia and runs the loop. No mocks.
```

> **Independence = no dependency on other engineers' code, not "no real services."** Testing the
> submodule means **running it** via `scripts/realtime_mini.py` (§9), which brings the whole layer up
> against **real infrastructure it owns** (LiveKit, Cartesia, the data channel). The two cross-layer
> seams (`agent` S2, `identify` S8) can't import `agent/`/`perception/`, so the script supplies
> **real, self-contained implementations** of those two callables — genuine working code that exercises
> the real seam, **not** `unittest.mock` objects or canned stubs. There is **no separate test suite** —
> a `--check` run of the script is the bare-minimum acceptance gate.

---

## 1. Module layout

```
src/realtime/
├── __init__.py        # re-exports: run_session, build_worker, FrameSampler, EventPublisher
├── session.py         # run_session(ctx, *, agent, identify, stt, tts, vad, scope_session, cfg)
├── bridge.py          # ClutchAgent(Agent): overrides llm_node → calls the passed-in agent (the "brain")
├── frames.py          # FrameSampler: video track (rtc.VideoStream) → raw JPEG bytes at ~1–2 fps (S8)
├── publisher.py       # EventPublisher: typed JSON on the "clutch" data topic (publish_data; events.py)
├── intents.py         # on_control_data(): decode custom "clutch"-topic control msgs (modality escalate)
└── worker.py          # build_worker(): WorkerOptions(entrypoint_fnc=…) + cli.run_app; binds deps via partial

scripts/
├── realtime_mini.py   # the ONLY test of the submodule (§9): stands up real local LiveKit + worker +
│                      #   test caller + real Cartesia STT/TTS; `--check` is the bare-minimum gate
└── _harness.py        # REAL self-contained agent + identify (the S2/S8 stand-ins) used by realtime_mini
```

Everything is `async`. Target **LiveKit Agents 1.x** (Python). The layer ships **no `mocks.py`** and
**no `tests/` suite** — testing the whole submodule = **running `scripts/realtime_mini.py`** (§9).

---

## 2. The Spine surface this layer needs (`src/events.py` — MUST be created)

`events.py` does not exist yet. It is a **Spine artifact** (frozen, governed by Arham; mirrored by
`widget/src/events.ts`), but `realtime/` is its **sole producer**, so the schema is specified here for
Day-0 freeze. It carries **no logic** — just the four typed shapes + a `dumps()` helper.

```python
# src/events.py  (Spine — frozen day 0; mirrored in widget/src/events.ts)
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal
import json

EventType = Literal["answer", "confirm", "voice_state", "latency"]
VoiceState = Literal["listening", "speaking", "thinking", "idle"]

@dataclass
class AnswerEvent:                                   # 04/03 → widget: a grounded, cited card
    text: str
    citations: list[dict] = field(default_factory=list)   # [{doc, section, score}]
    type: Literal["answer"] = "answer"

@dataclass
class ConfirmEvent:                                  # 04 → widget: "Is this the <X>?"
    prompt: str
    options: list[str] = field(default_factory=list)
    type: Literal["confirm"] = "confirm"

@dataclass
class VoiceStateEvent:                               # realtime → widget: mic/speaker indicator
    state: VoiceState
    type: Literal["voice_state"] = "voice_state"

@dataclass
class LatencyEvent:                                  # realtime → widget: REAL Moss ms (or omitted)
    time_taken_ms: int
    type: Literal["latency"] = "latency"

Event = AnswerEvent | ConfirmEvent | VoiceStateEvent | LatencyEvent

def dumps(e: Event) -> bytes:                        # the wire format the publisher writes
    return json.dumps(asdict(e), separators=(",", ":")).encode("utf-8")
```

> **Freeze note:** the JSON keys here are the S1 contract with Arman's `events.ts`. Any field change is
> an all-hands PR (10 §3). `latency` is **omitted entirely** when the real number is unknown — never 0
> (HLD 05 §6: "hide the badge rather than show a fake number").

> **Transport split (verified against LiveKit docs).** These four events are **Clutch application
> events** and ride a **dedicated data channel** — `room.local_participant.publish_data(..., reliable=True,
> topic="clutch")` — which the widget reads via `RoomEvent.DataReceived` filtered on `topic == "clutch"`.
> They are **separate** from the framework's own **text streams**: LiveKit Agents publishes the spoken
> transcript on the `lk.transcription` topic and accepts typed user text on the `lk.chat` topic
> automatically. We do **not** put `answer`/`confirm`/`voice_state`/`latency` on those reserved topics —
> the transcript stream is the agent's words; our data topic is the structured UI payload. (See
> [Text & transcriptions](https://docs.livekit.io/agents/build/text/).)

---

## 3. Public entrypoint — `run_session`

`run_session` is the LiveKit Agents **job entrypoint**: it is invoked once per room (one room per
`SupportSession`). It receives all cross-layer dependencies as arguments — it constructs nothing it
doesn't own.

```python
# src/realtime/session.py
from livekit.agents import JobContext, AgentSession, RoomInputOptions, RoomOutputOptions
from livekit import rtc
from contracts import SupportSession, IdentifyResult, Answer, CatalogEntry
from .bridge import ClutchAgent            # our Agent subclass (the "brain" node) — §4
from .frames import FrameSampler
from .publisher import EventPublisher
from .intents import on_control_data

# Duck-typed boundaries (NOT imported — passed in). Documented here for the reader:
#   AgentLike:   on_user_turn(text: str) -> Answer | None       (async)   [S2]
#                on_identify(r: IdentifyResult) -> Answer | None (async)   [S2]
#   IdentifyFn:  identify(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult  (async)  [S8]
#   ScopeFn:     scope_session(company_key: str, modality: str) -> SupportSession              [S6]
#   STT/TTS:     LiveKit plugin instances returned by speech.get_stt/get_tts                   (05b)

async def run_session(
    ctx: JobContext,        # the LiveKit Agents job entrypoint receives this
    *,
    agent,                  # AgentLike  (S2)   — held, never introspected
    identify,               # IdentifyFn (S8)
    stt,                    # livekit STT plugin (from speech.get_stt; this layer just plugs it in)
    tts,                    # livekit TTS plugin (from speech.get_tts)
    vad,                    # livekit VAD plugin (silero.VAD.load()) — endpointing
    scope_session,          # ScopeFn    (S6)   — resolves company_key → SupportSession
    cfg,                    # realtime config slice (§7)
) -> None:
    ...
```

> **API note (verified).** `run_session` is registered as the worker's entrypoint via
> `WorkerOptions(entrypoint_fnc=...)` (§9.1). Because the entrypoint LiveKit calls takes only
> `(ctx)`, the cross-layer deps are bound with `functools.partial(run_session, agent=…, identify=…, …)`
> in `build_worker` — keeping `run_session` itself dependency-injected and testable. (The newest SDK
> also exposes the `AgentServer()` + `@server.rtc_session(...)` decorator form; `build_worker` adapts to
> whichever the pinned `livekit-agents` exposes — the body below is identical.)

### 3.1 Body (the wiring, step by step)

1. **Connect & resolve session.** `await ctx.connect()`. Read `company_key` + `modality` from the
   room metadata / participant attributes (set by the token minted at `/connection-details`, 06).
   `session = scope_session(company_key, modality)` → a `SupportSession` (company-scoped; this layer
   never cross-checks tenancy).
2. **Build the publisher.** `pub = EventPublisher(ctx.room)` — the single writer to the `"clutch"`
   data topic (§5).
3. **Build the brain node.** `brain = ClutchAgent(agent, pub)` (§4) — an `Agent` subclass whose
   `llm_node` calls **Tonmoy's agent** instead of a raw model. This is the key decision: the LLM step
   in the LiveKit pipeline *is* the agent.
4. **Construct the AgentSession** (the LiveKit voice pipeline). Verified constructor params:
   ```python
   sess = AgentSession(
       stt=stt, llm=None, tts=tts, vad=vad,     # llm lives in the Agent via llm_node; None here is fine
       allow_interruptions=cfg.barge_in,        # barge-in (mandatory; native TTS-stop on user speech)
       min_interruption_duration=cfg.min_interruption_ms / 1000,   # how much speech counts as a barge-in
       min_endpointing_delay=cfg.endpoint_min_ms / 1000,           # fast endpointing (HLD 05 §3)
       max_endpointing_delay=cfg.endpoint_max_ms / 1000)
   ```
5. **Wire voice-state events.** `sess.on("agent_state_changed", …)` → `pub` (§6). The framework's
   agent-state machine *is* our voice indicator — no hand-rolled state.
6. **Start** the session, choosing `RoomInputOptions` by modality (verified field names —
   `audio_enabled` / `video_enabled` / `text_enabled`):
   ```python
   await sess.start(agent=brain, room=ctx.room,
       room_input_options=RoomInputOptions(
           audio_enabled=(modality in ("talk", "see")),
           video_enabled=(modality == "see"),
           text_enabled=True),                  # lk.chat text input always on (Type works in every mode)
       room_output_options=RoomOutputOptions(audio_enabled=(modality != "type"),
                                              transcription_enabled=True))
   ```
   Because modality escalates **within the same room** (HLD 05 §3), the session starts once and the
   widget publishes more tracks on escalation — `history`/`product_id` on `session` carry over. Track
   enable/disable is toggled live via `sess.input.set_audio_enabled(...)` / `set_video_enabled(...)`.
7. **Attach inbound handlers** (§3.2). Then `await` until the room disconnects.

### 3.2 Inbound handlers (verified against the LiveKit event/stream model)

| Source | Mechanism (verified) | Handler |
|---|---|---|
| **Type** (text) | framework **`lk.chat` text stream** → triggers a turn | flows through the same `ClutchAgent.llm_node` → `agent.on_user_turn`; `answer` event published, **no TTS** because `room_output_options.audio_enabled=False` in Type |
| **Talk** (audio) | STT → `user_input_transcribed` (`transcript`, `is_final`) → turn | the pipeline invokes `llm_node` automatically on the final transcript |
| **Talk** barge-in | `allow_interruptions=True`; `user_state_changed → "speaking"` | framework stops TTS natively; the next final transcript flows to `agent.on_user_turn`; `agent_state_changed` re-emits `voice_state: listening` |
| **See** (video) | `RoomEvent.TrackSubscribed` (video) → `rtc.VideoStream` | start `FrameSampler` (§8) → batches → `identify` → `agent.on_identify` → `pub` confirm/answer |
| **Modality escalate** | custom `"clutch"` data topic `{"t":"escalate","modality":"talk\|see"}` decoded by `on_control_data` | call `sess.input.set_audio_enabled(...)` / `set_video_enabled(...)` on the live session |

---

## 4. The brain node — `ClutchAgent(Agent)` overriding `llm_node` (S2)

LiveKit Agents 1.x exposes the pipeline as overridable **nodes** on an `Agent` subclass. The verified,
idiomatic way to make "the LLM step is the Agent" is to override **`llm_node`** — the node the pipeline
invokes where a raw model would normally run — and have it `yield` the text returned by Tonmoy's agent.
The framework streams that text straight into the TTS node, so we get spoken output for free. (Verified
signature: `async def llm_node(self, chat_ctx, tools, model_settings)` yielding `str` / `llm.ChatChunk`
— see [Pipeline nodes](https://docs.livekit.io/agents/logic/nodes/).)

```python
# src/realtime/bridge.py
from livekit.agents import Agent
from contracts import Answer, IdentifyResult
from events import AnswerEvent, LatencyEvent

class ClutchAgent(Agent):
    """The 'brain' node. llm_node delegates to the passed-in agent; holds it, never reads its state (S2)."""
    def __init__(self, agent, pub):
        super().__init__(instructions="")     # instructions unused — we don't call a raw model
        self._agent = agent                   # AgentLike — on_user_turn / on_identify
        self._pub = pub                       # EventPublisher

    async def llm_node(self, chat_ctx, tools, model_settings):
        text = chat_ctx.items[-1].text_content or ""          # the latest user turn (Talk STT or lk.chat)
        ans: Answer | None = await self._agent.on_user_turn(text)    # S2 — the only call into the brain
        spoken = self._emit(ans)
        if spoken:
            yield spoken                                       # → framework streams this into TTS + transcript

    async def handle_identify(self, result: IdentifyResult):  # See path — called by the FrameSampler glue
        ans: Answer | None = await self._agent.on_identify(result)  # S2
        spoken = self._emit(ans)
        if spoken:
            await self.session.say(spoken)                     # speak the grounded step (interruptible)

    def _emit(self, ans: Answer | None) -> str:
        if ans is None:
            return ""                                          # agent chose silence → no TTS, no card
        self._pub.send(AnswerEvent(text=ans.text, citations=ans.citations))    # card to widget
        if getattr(ans, "time_taken_ms", None):                # REAL Moss number, if present
            self._pub.send(LatencyEvent(time_taken_ms=ans.time_taken_ms))
        # a confirm-shaped answer (See) sets needs_confirmation upstream → emit a ConfirmEvent instead/also
        return ans.text                                        # → TTS speaks exactly the cited text
```

> **Why `llm_node`, not a custom `llm.LLM`.** Both exist, but overriding `llm_node` on the `Agent` is
> the documented hook for "replace what the model does at this turn" — it keeps STT, TTS, turn-detection,
> interruption and transcription all wired by the framework, and gives us the user transcript in
> `chat_ctx` directly. A full `llm.LLM` subclass (implementing `chat() -> LLMStream`) would re-implement
> streaming we don't need. The **contract that matters** is unchanged: one user turn in, one
> `Answer|None` out, publisher fires on every non-null answer. **The agent decides what to say; this
> layer only transports it.**

---

## 5. Event publisher — `EventPublisher` (S1)

Single writer to the LiveKit **reliable** data channel; serializes via `events.dumps`. Reliable
delivery so `answer`/`confirm` are never dropped (HLD 05 §6).

```python
# src/realtime/publisher.py
from livekit import rtc
from events import Event, dumps, ConfirmEvent, VoiceStateEvent

class EventPublisher:
    def __init__(self, room: rtc.Room, topic: str = "clutch"):
        self._room, self._topic = room, topic

    def send(self, e: Event) -> None:
        # reliable so cards/confirms survive a hiccup; fire-and-forget task (publisher never blocks the loop)
        self._room.local_participant.publish_data(dumps(e), reliable=True, topic=self._topic)

    # convenience wrappers used by the session/state machine:
    def confirm(self, prompt: str, options: list[str]) -> None: self.send(ConfirmEvent(prompt, options))
    def voice(self, state: str) -> None: self.send(VoiceStateEvent(state))   # type: ignore[arg-type]
```

---

## 6. Voice state — mapped from the framework's `agent_state_changed` (no hand-rolled FSM)

The LiveKit AgentSession already runs the exact state machine our widget indicator needs. Verified
states emitted on `agent_state_changed`: **`initializing` · `idle` · `listening` · `thinking` ·
`speaking`** ([Events](https://docs.livekit.io/agents/build/events/)). We subscribe and forward — we do
**not** maintain our own state:

```python
@sess.on("agent_state_changed")
def _on_state(ev):
    state = ev.new_state                      # one of the framework states above
    if state in ("listening", "thinking", "speaking", "idle"):
        pub.voice(state)                       # → VoiceStateEvent on the "clutch" topic
```

```
 initializing ─▶ idle ─▶ listening ─(final transcript → llm_node)─▶ thinking ─(answer)─▶ speaking ─▶ idle
                            ▲                                                                  │
                            └──────────────── barge-in (user speaks; native TTS-stop) ─────────┘
```

- **Barge-in (mandatory):** `allow_interruptions=True` + `min_interruption_duration` make the session
  stop TTS natively the instant qualifying user speech is detected (`user_state_changed → "speaking"`).
  The framework then transcribes the interruption and runs the next turn through `llm_node` — so the
  brain sees the new text; `agent_state_changed` re-emits `listening → thinking → speaking`. No manual
  `tts.stop()` or partial-forwarding plumbing is needed (the framework owns it); this layer only relays
  the resulting state to the widget.
- **`VoiceState` Literal** in `events.py` therefore matches the framework's vocabulary
  (`listening|speaking|thinking|idle`).

---

## 7. Config changes needed (`src/config.py` — MUST be extended)

`src/config.py` does not exist yet (Spine-adjacent, owned by Arham; 10 §8.6). The realtime layer reads
a **role-named** slice off it — **never** vendor names (00 §7). Adding these keys is a Spine-adjacent PR
(Arham reviews). Required keys:

| Key (role-named) | Type | Default | Used by | Notes |
|---|---|---|---|---|
| `livekit_url` | str | — | `worker.build_worker` | from `LIVEKIT_URL` env (06 mints tokens against it) |
| `livekit_api_key` | str | — | worker | `LIVEKIT_API_KEY` |
| `livekit_api_secret` | str | — | worker | `LIVEKIT_API_SECRET` |
| `stt_provider` | str | `"cartesia"` | `speech.get_stt` (05b) | `cartesia` \| `deepgram`… — **role, not vendor**; `realtime_mini` runs the real Cartesia plugin |
| `tts_provider` | str | `"cartesia"` | `speech.get_tts` (05b) | same |
| `see_fps` | float | `1.5` | `FrameSampler` | 1–2 fps sample rate off the video track |
| `see_batch` | int | `2` | `FrameSampler` | frames per `identify` call (perception picks best) |
| `see_min_interval_ms` | int | `1500` | `FrameSampler` | floor between `identify` calls (cost guard) |
| `endpoint_min_ms` | int | `300` | `AgentSession.min_endpointing_delay` | fast endpointing (HLD 05 §3) |
| `endpoint_max_ms` | int | `2000` | `AgentSession.max_endpointing_delay` | lazy-speech ceiling |
| `min_interruption_ms` | int | `200` | `AgentSession.min_interruption_duration` | how much user speech counts as a barge-in |
| `barge_in` | bool | `True` | `AgentSession.allow_interruptions` | mandatory on; exposed only for the failure-drill toggle |
| `latency_badge` | bool | `True` | publisher | off ⇒ never emit `LatencyEvent` |

Suggested shape so the layer takes a typed slice, not the whole config:

```python
# in src/config.py (Arham), role-named — realtime reads only this slice
@dataclass
class RealtimeConfig:
    livekit_url: str; livekit_api_key: str; livekit_api_secret: str
    stt_provider: str = "cartesia"; tts_provider: str = "cartesia"
    see_fps: float = 1.5; see_batch: int = 2; see_min_interval_ms: int = 1500
    endpoint_min_ms: int = 300; endpoint_max_ms: int = 2000; min_interruption_ms: int = 200
    barge_in: bool = True; latency_badge: bool = True
```

### 7.1 `pyproject.toml` dependency additions

Current deps are `openai`, `pillow`, `python-dotenv`. The realtime layer adds (main group):

```toml
"livekit-agents>=1.0",            # the AgentSession pipeline + worker
"livekit-plugins-silero>=1.0",    # VAD / endpointing
"livekit-plugins-cartesia>=1.0",  # default STT + TTS plugins (selected by stt_provider/tts_provider)
"livekit-plugins-turn-detector>=1.0",  # optional: better turn detection than raw VAD
```

`pillow` is already present (the `FrameSampler` re-encodes/validates JPEG frames). **No test-only Python
deps:** the only test is `scripts/realtime_mini.py` (§9), which runs the same main-group LiveKit +
Cartesia packages for real. It additionally needs a `livekit-server` docker image and `CARTESIA_API_KEY`
at runtime — infra, not a Python dependency.

### 7.2 `.env` additions

```
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
CARTESIA_API_KEY=...        # only when stt_provider/tts_provider = cartesia (05b reads it)
```

---

## 8. Frame sampler — `FrameSampler` (S8)

Pulls frames off the subscribed video track at `see_fps`, throttled by `see_min_interval_ms`, batches
`see_batch` of them, and hands **raw JPEG `list[bytes]`** to `identify`. It does **not** rank frames —
best-frame selection (`select_best_frames`) lives in `perception/frames.py` (Tonmoy's dir); this layer
stays on its side of S8.

```python
# src/realtime/frames.py
from livekit import rtc

class FrameSampler:
    def __init__(self, identify, on_result, *, fps, batch, min_interval_ms, clock):
        self._identify, self._on_result = identify, on_result   # identify=S8 fn; on_result=agent.on_identify path
        self._fps, self._batch, self._min_ms = fps, batch, min_interval_ms
        self._clock = clock                # injected monotonic clock (testable; no Date.now in tests)
        self._buf: list[bytes] = []; self._last = 0.0

    async def run(self, track: rtc.VideoTrack, catalog) -> None:
        stream = rtc.VideoStream(track)                   # verified: VideoStream(track) is async-iterable
        async for ev in stream:                           # yields VideoFrameEvent; the frame is ev.frame
            now = self._clock()
            if now - self._last < 1.0 / self._fps:        # sample-rate gate (~1–2 fps)
                continue
            self._last = now
            self._buf.append(_to_jpeg(ev.frame))          # rtc.VideoFrame → JPEG bytes (pillow)
            if len(self._buf) >= self._batch:
                frames, self._buf = self._buf, []
                if now - getattr(self, "_last_call", 0) >= self._min_ms / 1000:   # cost floor
                    self._last_call = now
                    result = await self._identify(frames, catalog)   # S8 → IdentifyResult
                    await self._on_result(result)                    # → ClutchAgent.handle_identify (S2)
        await stream.aclose()                             # release decoder buffers (verified: aclose())
```

> **Verified video-input contract.** The camera track is only delivered to the worker when the session
> is started with `RoomInputOptions(video_enabled=True)` (See mode); the sampler attaches on
> `RoomEvent.TrackSubscribed` for the video track. We do **not** use the framework's
> "inject latest frame into the LLM context" helper — our See path runs a **separate** vision call
> (`identify`, S8), so we own the `VideoStream` directly and hand raw JPEG `list[bytes]` across S8.

`on_result` routes to `ClutchAgent.handle_identify` → `agent.on_identify` → if the result
`needs_confirmation`, the agent returns a confirm-driving `Answer` and the session emits a
`ConfirmEvent` ("Is this the *X*?"); if `resolved`, retrieval proceeds and a grounded `answer` flows
(spoken via `session.say`).

---

## 9. Testing — `scripts/realtime_mini.py` is the whole test

There is **one** way to test this submodule and it is this script: it **stands up, with real APIs, the
smallest complete instance of everything the layer needs to run**, drives one turn of each modality
through it, and exits non-zero if the expected events don't come out. No `tests/` suite, no
`unittest.mock`, no separate integration harness — running the script *is* the test. It composes only
things this layer owns (or the real infra it sits on): a real LiveKit server, the real worker, real
Cartesia speech, and the real S2/S8 stand-ins it carries.

### 9.1 What it brings up (five real pieces)

| # | Piece | How the script provisions it (real, with API) |
|---|---|---|
| 1 | **LiveKit server** | starts a local `livekit-server` (docker, dev keys `devkey`/`secret`) on `ws://localhost:7880`, OR reads `LIVEKIT_URL/KEY/SECRET` for a Cloud sandbox. A readiness probe waits for the WS port. |
| 2 | **A room + tokens** | mints two JWTs with the **real** server API — `api.AccessToken(key, secret).with_identity(...).with_grants(api.VideoGrants(room_join=True, room="clutch-mini"))` — one for the agent worker, one for the test caller. Sets room metadata `{"company_key":"demo","modality":"see"}`. |
| 3 | **The realtime worker** | builds the real `cfg` (RealtimeConfig §7), real `stt=get_stt(cfg)`/`tts=get_tts(cfg)` (Cartesia plugins), `vad=silero.VAD.load()`, then `build_worker(run_session, agent=…, identify=…, stt, tts, vad, scope_session=…)` and runs it against the room. |
| 4 | **The two seam stand-ins** | injects the **real** `EchoComposeAgent` (S2) and `pixel_identify` (S8) from `scripts/_harness.py` — genuine code, not mocks (§9.4). `scope_session` is a 3-line real function returning a `SupportSession` from the room metadata. |
| 5 | **A test caller participant** | a second LiveKit participant (the "browser") that joins with the caller token and can: publish a real WAV as a mic track, publish a real JPEG as a video frame, send `lk.chat` text, send a `"clutch"`-topic escalate message, and **subscribe to the `"clutch"` data topic** to collect the four events. |

### 9.2 The API surface the script exposes (CLI + the `--check` gate share one path)

```python
# scripts/realtime_mini.py  (illustrative signatures — the contract, not an implementation)
async def boot_mini(cfg=None, *, use_docker=True) -> MiniInstance: ...
#   → starts pieces 1–4, returns a handle: { url, room, worker_task, caller_token, stop() }

class MiniCaller:                      # piece 5 — the real "browser" side
    async def join(url, token): ...
    async def say_wav(path_or_bytes): ...           # publish a real audio track (Talk)
    async def show_jpeg(path_or_bytes): ...         # publish a real video frame (See)
    async def type_text(text): ...                  # send on the lk.chat text stream (Type)
    async def escalate(modality): ...               # send {"t":"escalate",...} on the "clutch" topic
    async def events(timeout) -> list[dict]: ...    # collect decoded events off the "clutch" topic

# CLI (dev run-loop):
#   uv run python -m scripts.realtime_mini --talk "how do I clear a jam"   # speak a turn, print events
#   uv run python -m scripts.realtime_mini --see frame.jpg                 # push a frame, print events
# THE TEST (bare-minimum acceptance gate — exits 0/non-zero):
#   uv run python -m scripts.realtime_mini --check
```

### 9.3 The bare-minimum check (`--check`)

`--check` boots the instance, runs **one turn per modality**, asserts the minimum that proves the loop
is wired, prints a pass/fail line per check, and returns a non-zero exit code on any failure. That is
the entire acceptance gate — nothing more:

```
boot_mini() → caller joins
[1] TALK : caller.say_wav("how do I clear a paper jam")
           PASS if an `answer` event (non-empty citations) AND a `latency` event (>0 ms) arrive
[2] STATE: PASS if `voice_state` went listening → thinking → speaking (from agent_state_changed)
[3] BARGE: caller.say_wav(burst) during TTS → PASS if outbound TTS audio stops within the window
[4] SEE  : caller.show_jpeg(frame) → PASS if a `confirm` OR `answer` event arrives (frame-dependent)
[5] TYPE : caller.type_text("...") → PASS if an `answer` event arrives with NO TTS audio
mini.stop()  → exit 0 iff all five passed
```

Checks read the **real bytes off the `"clutch"` data topic** (decoded with `events.py`) and the **real
audio frames** off the room — nothing is patched in-process. These five lines cover every `realtime/`
DoD row in [14 §5](../hld/engineers/14-fardin-inference-retrieval-voice.md): real STT→Agent→TTS loop,
barge-in, frame sampler → `identify`, and all four events with a real `time_taken_ms`.

### 9.4 The real S2/S8 stand-ins (`scripts/_harness.py`)

`realtime/` may not import `agent/` or `perception/` (independence, 10 §8.4), so the script carries its
own `agent` and `identify`. They are **genuine implementations, not mocks** — `EchoComposeAgent` builds
a real `Answer` with citations and a measured `time_taken_ms`; `pixel_identify` actually decodes the
JPEG and decides from real pixels. Deterministic and dependency-free, but real logic through the real
seam — no `unittest.mock`, no patching:

```python
# scripts/_harness.py — REAL stand-ins for the two cross-layer seams (no mock library)
from contracts import Answer, IdentifyResult, ProblemSignals
from PIL import Image; import io, time

class EchoComposeAgent:                       # a real, deterministic ConversationAgent-shaped object (S2)
    def __init__(self): self.turns = []
    async def on_user_turn(self, text: str) -> Answer | None:
        self.turns.append(text)
        t0 = time.monotonic()
        ans = f"To handle '{text}', open the rear tray and clear the jam."   # real composed text
        return Answer(text=ans, citations=[{"doc": "manual.pdf", "section": "3.2", "score": 0.81}],
                      time_taken_ms=int((time.monotonic() - t0) * 1000))
    async def on_identify(self, r: IdentifyResult) -> Answer | None:
        if r.needs_confirmation: return None                 # → session emits ConfirmEvent
        return Answer(text="I can see the paper-jam indicator. Open the rear tray.",
                      citations=[{"doc": "manual.pdf", "section": "4.1", "score": 0.77}])

async def pixel_identify(frames: list[bytes], catalog) -> IdentifyResult:   # real S8 implementation
    img = Image.open(io.BytesIO(frames[0])).convert("L")      # actually decode the published frame
    bright = sum(img.getdata()) / (img.width * img.height)
    return IdentifyResult(product_id=(catalog[0].product_id if catalog else "p1"),
                          confidence=0.9, model_source="visual_match",
                          needs_confirmation=bright < 40,      # decision from REAL pixels
                          problem=ProblemSignals(indicators=["amber blinking light"],
                                                 summary="paper-jam indicator on"))
```

### 9.5 Running it

- **Local:** `boot_mini(use_docker=True)` starts `livekit-server` in docker (dev keys) — no cloud
  account needed locally. Needs `docker` + the §7.2 `.env` keys (`LIVEKIT_*`, `CARTESIA_API_KEY`).
- **No creds → skip, don't fail:** if `CARTESIA_API_KEY`/`LIVEKIT_*` are absent, `--check` prints
  `SKIP (no creds)` and exits 0, so it never blocks a keyless checkout of the other layers.
- **CI:** one job runs `uv run python -m scripts.realtime_mini --check` with a `livekit-server` service
  container + the Cartesia secret. No pytest markers, no `tests/` lane to maintain.
- **Teardown:** `stop()` cancels the worker task, disconnects the caller, and stops the docker
  container (skipped against a Cloud sandbox).

---

## 10. Failure modes (HLD 05 §6 — this layer's handling)

| Failure | This layer's behavior |
|---|---|
| STT mis-hears / silence | surface push-to-talk via a `voice_state` hint; widget can fall back to Type **in the same room** (data channel still open) — no reconnect |
| TTS down | `ClutchAgent._emit` still publishes the `answer` card (text), so the answer renders even with no audio; swap is `tts_provider` (05b), no realtime change |
| Turn detection too eager/lazy | tune `endpoint_min_ms`/`endpoint_max_ms`; `barge_in` + push-to-talk override |
| Camera/vision unavailable | `FrameSampler` never starts (no video track) → See degrades to Talk; cards still render from voice retrieval |
| Data-channel hiccup | reliable delivery for `answer`/`confirm`; `voice_state`/`latency` are best-effort, widget holds last-known |
| Latency number missing | `latency_badge` / null `time_taken_ms` ⇒ **no** `LatencyEvent` (never a fake number) |
| Bad/expired company key | the room is never created (token refused upstream at 06); `run_session` is simply never invoked for that key |

---

## 11. Independence & seam checklist (acceptance)

- [ ] `realtime/` imports only `contracts` + `events` (grep: no `from agent`, `from perception`, `from retrieval`, `from speech`).
- [ ] `agent`, `identify`, `stt`, `tts`, `scope_session` all arrive via `run_session(...)` args — none constructed here.
- [ ] `ClutchAgent.llm_node`/`handle_identify` call **only** `on_user_turn` / `on_identify` (S2) — never read agent internals.
- [ ] `scripts/realtime_mini.py` brings the whole layer up with real LiveKit + Cartesia and no `agent/`/`perception/` import.
- [ ] `FrameSampler` emits raw `list[bytes]` to `identify` (S8) — no frame ranking on this side.
- [ ] All four events validate against `events.py` (S1); `latency` omitted when unknown.
- [ ] `scripts/realtime_mini.py --check` passes against a **real** LiveKit room + **real** Cartesia STT/TTS — no `unittest.mock`, no stubs; the S2/S8 stand-ins are real working code.
- [ ] Barge-in: a real second audio burst during TTS stops the outbound audio and forwards the partial to the agent.

---

## 12. Summary of required config / Spine changes (the "what's new" list)

1. **Create `src/events.py`** (§2) — the four event dataclasses + `dumps()`. Spine; freeze day 0; mirror to `widget/src/events.ts`.
2. **Extend `src/config.py`** (§7) — add the role-named `RealtimeConfig` slice (LiveKit creds, `stt_provider`/`tts_provider`, frame-sampling, endpointing, toggles). No vendor names.
3. **Add LiveKit deps to `pyproject.toml`** (§7.1) — `livekit-agents`, `livekit-plugins-silero`, `livekit-plugins-cartesia` (+ optional `turn-detector`).
4. **Add `.env` keys** (§7.2) — `LIVEKIT_URL/API_KEY/API_SECRET`, `CARTESIA_API_KEY`.
5. **`src/app.py` wiring line** (Arham, append-only) — pass the real `agent`/`identify`/`stt`/`tts`/`scope_session` into `run_session` (10 §7). The layer is validated **before** that wiring by `scripts/realtime_mini.py --check` (§9), which supplies its own real `EchoComposeAgent`/`pixel_identify` for the S2/S8 seams — so `realtime/` reaches "done + tested" with zero dependency on Tonmoy's code and **no mocks**.
6. **Add `scripts/realtime_mini.py` + `scripts/_harness.py`** (§9) — the only test of the submodule: `boot_mini` + `MiniCaller` stand up real local LiveKit + worker + Cartesia with the real S2/S8 stand-ins; `--check` is the bare-minimum acceptance gate (and the same script is the dev run-loop). Needs `docker` (local server) + the §7.2 `.env` keys. **No `tests/` suite, no pytest markers.**

---

## 13. LiveKit API contract — verified against the docs

Every framework call in this LLD was checked against the LiveKit Agents 1.x docs (reviewed Jun 2026).
The decisions that changed as a result:

| LLD element | Verified contract | Source |
|---|---|---|
| The "brain" = `ClutchAgent.llm_node` | `Agent` subclass overriding `async def llm_node(self, chat_ctx, tools, model_settings)` that **yields** `str`/`ChatChunk`; the latest user turn is in `chat_ctx`. Chosen over a custom `llm.LLM` (keeps STT/TTS/turn-detection/transcription framework-wired). | [Pipeline nodes](https://docs.livekit.io/agents/logic/nodes/) |
| `voice_state` = framework states | `session.on("agent_state_changed")` with states `initializing/idle/listening/thinking/speaking`; `user_state_changed`, `user_input_transcribed(transcript,is_final)` drive barge-in. **No hand-rolled FSM.** | [Events](https://docs.livekit.io/agents/build/events/) |
| Event transport split | Agent transcript rides the framework **text streams** (`lk.transcription` out, `lk.chat` in); our 4 UI events ride a **separate** `publish_data(reliable=True, topic="clutch")` channel. | [Text & transcriptions](https://docs.livekit.io/agents/build/text/) |
| Session construction & start | `AgentSession(stt, llm, tts, vad, allow_interruptions, min_interruption_duration, min_endpointing_delay, max_endpointing_delay)`; `await session.start(agent=, room=, room_input_options=RoomInputOptions(audio_enabled, video_enabled, text_enabled), room_output_options=RoomOutputOptions(audio_enabled, transcription_enabled))`. | [Anatomy](https://docs.livekit.io/agents/build/anatomy/) · [Session](https://docs.livekit.io/agents/build/session/) |
| Worker / entrypoint | `entrypoint(ctx: JobContext)` → `await ctx.connect()`, `ctx.room`; `WorkerOptions(entrypoint_fnc=…)` + `cli.run_app(...)`. Newest SDK also offers `AgentServer()` + `@server.rtc_session(...)` — `build_worker` adapts. Deps bound via `functools.partial`. | [Job lifecycle](https://docs.livekit.io/agents/server/job/) |
| Video / See path | `RoomInputOptions(video_enabled=True)`; subscribe on `RoomEvent.TrackSubscribed`; `rtc.VideoStream(track)` async-iterates `VideoFrameEvent` (`ev.frame`), `aclose()` to free buffers. Our See path runs `identify` (S8) directly, not the LLM-context frame helper. | [Images & video](https://docs.livekit.io/agents/build/vision/) |
| Speech as plugins (not Inference) | Cartesia STT/TTS via `livekit.plugins.cartesia` and `silero.VAD.load()` — **not** the hosted `agents.inference` router, because HLD 05 §3 requires STT/TTS to be **LiveKit plugins behind the `STTProvider`/`TTSProvider` swap point**, not gateway/Inference-routed. | [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/) |
| Token / room provisioning (bootstrap) | `api.AccessToken(key, secret).with_identity(...).with_grants(api.VideoGrants(room_join=True, room=...)).to_jwt()`; `LIVEKIT_URL/API_KEY/API_SECRET`. | [Server API](https://docs.livekit.io/reference/python/livekit/api/) |

**One open API risk to confirm at build time:** the exact `llm_node` streaming shape and the
`agent_state_changed` event field name (`new_state` vs `state`) differ slightly across 1.x point
releases — pin `livekit-agents` in `pyproject.toml` and confirm both against the installed version's
SDK reference before wiring `bridge.py`/§6. Everything else above is stable across the 1.x line.
