# Fixalong LLD 07 — Voice Persona (MiniMax)
**Implements:** HLD 07 · **Module(s):** agent/voice/persona.py · **Build tier:** 1 (MiniMax) / 2 (Nova Sonic) · **Status:** Draft v0.1

## 1. Responsibility
Turn a finished utterance (`text`) plus a `PersonaMode` into spoken audio that LiveKit (03)
plays into the room. This module owns **only** the text→speech step: it maps the mode to
MiniMax TTS voice params, streams synthesis for low first-audio latency, enforces a hard
max-length on `safety` mode, and exposes `stop()` so barge-in (03) can flush mid-utterance.
It does **not** decide *what* to say (05) or *whether it is safe* (06) — those are upstream.
On any synth failure it degrades through a fallback chain (Nova Sonic → LiveKit default TTS)
rather than going silent on the hot path.

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/voice/persona.py` | `Persona` (class), `say()`, `stop()`, `MODE_PARAMS` (table), `VoiceParams` (dataclass), `PersonaError` |
```python
class Persona:
    def __init__(self, cfg: PersonaConfig | None = None) -> None: ...
    async def say(self, text: str, mode: PersonaMode) -> AudioStream: ...   # primary entry (HLD §5)
    async def stop(self) -> None: ...                                       # barge-in flush (03)
    @property
    def active(self) -> bool: ...                                           # is a stream live?
```
`PersonaMode` (`normal|safety|diagnosis|confirm|expert`) is the shared contract (`agent/contracts.py`).

## 3. Dependencies
- **SDKs:** `minimax` HTTP/WebSocket TTS (`T2A v2`, streaming); `livekit-agents` (`tts.TTS`,
  `AudioFrame`, `SynthesizeStream`); `livekit-plugins-aws` (Nova Sonic) for Tier-2 fallback.
- **Internal:** `agent/contracts.py` (`PersonaMode`); `agent/voice/livekit_agent.py` is the
  sole caller. No retrieval/safety imports — this module is downstream of both.
- **stdlib:** `asyncio`, `dataclasses`, `os`, `logging`, `time`.
- **Env vars:** `MINIMAX_API_KEY`, `MINIMAX_GROUP_ID`, `MINIMAX_BASE_URL`
  (default `https://api.minimax.io`), `MINIMAX_MODEL` (default `speech-02-turbo`),
  `MINIMAX_TIMEOUT_MS` (default `4000`), `MINIMAX_SAFETY_MAX_CHARS` (default `90`),
  `PERSONA_FALLBACK` (`nova|default|both`, default `both`). AWS creds for Nova come from the
  standard AWS chain used by infra (08).

## 4. Data structures
```python
@dataclass(frozen=True)
class VoiceParams:
    voice_id: str        # MiniMax system/cloned voice id
    style: str           # emotion/style tag passed to T2A: calm|urgent|serious|warm|professional
    speed: float         # 0.5–2.0 (MiniMax range); 1.0 = natural
    vol: float           # 0–10; loudness bias (safety speaks up)
    pitch: int           # -12..12 semitones; 0 = neutral
    emphasis: float      # 0–1 prosody intensity hint baked into style/SSML
    max_chars: int | None  # hard cap; None = no cap

@dataclass
class PersonaConfig:
    model: str; timeout_ms: int; safety_max_chars: int
    fallback: Literal["nova","default","both"]
    sample_rate: int = 24000; fmt: str = "pcm"   # LiveKit-friendly raw PCM

# AudioStream = livekit.agents.tts.SynthesizeStream  (async-iterable of AudioFrame)
```

## 5. Key functions

### 5.1 `MODE_PARAMS` — the authoritative mode→params table
```python
MODE_PARAMS: dict[PersonaMode, VoiceParams] = {
  # mode        voice_id            style          speed vol pitch emph max_chars
  "normal":   VoiceParams("male-qn-qingse","calm",        1.00, 5.0,  0, 0.3, 220),
  "safety":   VoiceParams("male-qn-jingying","serious",   1.08, 8.0,  0, 0.9, 90),   # short+firm
  "diagnosis":VoiceParams("male-qn-qingse","calm",        0.92, 5.0, -1, 0.4, 320),  # slower/explanatory
  "confirm":  VoiceParams("male-qn-qingse","warm",        1.02, 5.0,  1, 0.3, 140),  # reassuring/brief
  "expert":   VoiceParams("presenter-male","professional",0.98, 6.0,  0, 0.5, 300),  # technician tone
}
```
Single base voice with style/speed/pitch variation; `safety` and `expert` use distinct ids
(HLD §8 open question resolved toward "mostly one voice, two exceptions"). Changing a row is
the only place persona behavior is tuned — no logic branches per mode elsewhere.

### 5.2 `say(text, mode) -> AudioStream`
```
def say(text, mode):
    p = MODE_PARAMS[mode]
    text = enforce_max(text, p.max_chars or cfg.safety_max_chars if mode=="safety" else p.max_chars)
    await self.stop()                         # never overlap; flush any prior stream (03)
    req = build_request(text, p, cfg)         # voice_id, style→emotion, speed, vol, pitch, stream=True
    try:
        stream = await self._minimax_stream(req)   # opens WS/chunked HTTP, yields frames as they arrive
    except (TimeoutError, MiniMaxError) as e:
        stream = await self._fallback(text, mode, cause=e)
    self._active = stream
    return stream                             # caller awaits-iterates frames into the room
```
`enforce_max`: if `mode=="safety"` and `len(text) > max_chars`, **truncate at the last
sentence boundary ≤ cap** (never mid-word); if no boundary, hard-cut + ensure terminal
punctuation so the clipped instruction still reads as a complete stop command. For non-safety
modes over cap, log a warning and pass through (length is 05's job; we only *guarantee* safety
brevity). The cap exists so a "Stop. Unplug it." is never buried (HLD §3, §7).

### 5.3 `stop()` — barge-in flush
```
async def stop():
    s = self._active
    if not s: return
    self._active = None
    s.cancel()                # stop pulling MiniMax frames
    await s.aclose()          # close WS/HTTP; drop any buffered/in-flight audio immediately
```
Must flush the synth buffer (not drain it) so audio cuts within one frame of interrupt
(HLD §7 "voice clipping on interrupt"). Idempotent and safe to call when nothing is active.

### 5.4 `_minimax_stream` / `_fallback`
- `_minimax_stream`: POST/WS to `T2A v2` with `stream=true`; first audio chunk resolves the
  awaited stream so the room starts playing before synthesis completes.
- `_fallback(text, mode)`: per `cfg.fallback` — try Nova Sonic via `livekit-plugins-aws`
  (re-derive a coarse style from mode: safety→serious, else neutral), then LiveKit default
  TTS. Returns a `SynthesizeStream` of the same shape so the caller is agnostic.

## 6. Control flow / sequence
```
05 (text, mode) ─▶ livekit_agent ─▶ Persona.say
   say → stop(prev) → enforce_max → MODE_PARAMS[mode] → build_request
       → MiniMax stream  ──first frame──▶ LiveKit room (speaker)
   (user speaks) ─▶ 03 barge-in ─▶ Persona.stop() ─▶ buffer flushed, audio cut
   (MiniMax error/timeout) ─▶ _fallback → Nova Sonic → default TTS ─▶ room
```

## 7. Config & tuning
| Param | Default | Notes |
|---|---|---|
| `MINIMAX_MODEL` | `speech-02-turbo` | turbo = lowest latency; `speech-02-hd` for quality demos |
| `MINIMAX_TIMEOUT_MS` | `4000` | wall clock to *first* audio chunk before fallback |
| `MINIMAX_SAFETY_MAX_CHARS` | `90` | hard cap for `safety`; ≈ one short imperative sentence |
| `sample_rate` / `fmt` | `24000` / `pcm` | raw PCM avoids decode latency into LiveKit |
| `PERSONA_FALLBACK` | `both` | `nova` → `default` order; `default` skips Nova |
| per-mode `speed/vol/pitch/max_chars` | §5.1 table | only tuning surface for tone |

## 8. Error handling & fallbacks (fail-safe)
| Failure | Behavior |
|---|---|
| MiniMax timeout / 5xx / auth | `_fallback` → Nova Sonic → LiveKit default TTS; log `cause` once |
| Both MiniMax + Nova down | LiveKit default TTS (always-available local-ish path) |
| All TTS down | raise `PersonaError`; 03 surfaces a UI text-only notice (09) — agent text still shown |
| `safety` text too long/soft | `enforce_max` truncates to firm imperative + `vol`/`style` from safety row |
| `stop()` mid-frame | cancel+aclose; never block the event loop; swallow close errors |
| Missing `MINIMAX_*` env | construct fails fast at startup with a clear message (not on hot path) |
Fail-safe principle (README): degrade the *voice*, never the *content* — text remains correct
even if audio drops to default TTS.

## 9. Latency / perf notes
- **Budget:** TTS is hot-path after retrieval+safety; target **first audio < 400 ms** with
  `speech-02-turbo` + streaming. Fallback to Nova/default only after `MINIMAX_TIMEOUT_MS`.
- **Streaming:** play on first chunk; do not await full synthesis. Short `max_chars`
  (normal/safety) directly lowers time-to-first-word and makes interrupts cheap (HLD §4).
- **Cold:** first call pays WS handshake + voice warmup; pre-open a MiniMax session on session
  start (warm pool of 1) so the first real utterance is hot.
- **stop():** O(1), single frame of trailing audio at most.

## 10. Test plan (unit + integration; mock TTS)
Mock the MiniMax/Nova/default backends behind a fake `SynthesizeStream`.
1. **mode→params mapping:** assert `MODE_PARAMS[mode]` exact values for all 5 modes; assert
   `safety.max_chars == cfg.safety_max_chars` and `safety.vol > normal.vol`,
   `diagnosis.speed < normal.speed` (slower), `safety.speed >= normal.speed`.
2. **request building:** `say(text, mode)` passes the row's `voice_id/style/speed/vol/pitch`
   into `build_request` (capture the mock's received args) for each mode.
3. **safety max-length:** long input + `safety` → output ≤ cap, ends on sentence boundary,
   terminal punctuation present; non-safety over cap → passed through + warning logged.
4. **stop() behavior:** start `say`, call `stop()` → mock stream `.cancel()` + `.aclose()`
   called once, `active is False`, no frames yielded after stop; `stop()` with nothing active
   is a no-op.
5. **overlap guard:** second `say` while one is active calls `stop()` on the first.
6. **fallback chain:** mock MiniMax raise timeout → Nova used; Nova raise → default used;
   all raise → `PersonaError`. Assert order honors `PERSONA_FALLBACK`.
7. **integration (manual/CI-gated):** real MiniMax key, measure first-audio latency < 400 ms;
   real LiveKit room plays + a programmatic barge-in cuts audio within one frame.

## 11. Build checklist (Tier-0 first)
1. `VoiceParams`, `PersonaConfig`, `MODE_PARAMS`, env loading + fail-fast validation.
2. `enforce_max` + unit tests (safety truncation edge cases).
3. `Persona.say` against a **mock** MiniMax stream; `stop()` flush + overlap guard + tests.
4. Real MiniMax `T2A v2` streaming client (`_minimax_stream`); first-chunk play; latency check.
5. Wire into `agent/voice/livekit_agent.py` (03): `say` on speak-gate pass, `stop` on barge-in.
6. **Tier 2:** Nova Sonic fallback via `livekit-plugins-aws`; then LiveKit default TTS path.
7. Warm-pool pre-open on session start; tune `MODE_PARAMS` rows from demo recordings.
