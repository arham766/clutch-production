# Clutch LLD — Speech Layer (`src/speech/`)

**Status:** Draft v0.5 · **Layer of:** HLD [05 — Realtime, Voice & Widget](../hld/05-realtime-voice-widget.md)
**Section:** D — Inference (Fardin), brief [14](../hld/engineers/14-fardin-inference-retrieval-voice.md)
**Scope of this LLD:** *only* `src/speech/` — the STT/TTS **swap point**. The realtime voice loop
(`src/realtime/`), the Agent (04), Perception (02), Retrieval (03), the widget (05-client) and
Tenancy/Gateway (06) are **out of scope** and appear here only as the boundary this layer is built
*independent of*.

> **v0.5 change (align to sibling LLDs 03/05-realtime):** **all e2e moves to `scripts/`** — the
> standalone check is now **`scripts/speech_e2e.py`** (was `src/speech/integration_check.py`), matching
> `scripts/retrieval_mini.py` / `scripts/realtime_mini.py`. **Unit tests cut to the bare minimum** —
> one micro-file covers only the pure selection logic (default → Cartesia, unknown → raise, missing key
> → raise); everything real is the script. New **dedicated §7.2 (API key)** and **§7.3 (environment-
> variable configuration, persistable between users — committed `.env.example` vs per-user `.env`)**.
>
> **v0.4 change (Cartesia API reconciliation):** distinguished the **two Cartesia surfaces** — the
> LiveKit *plugin* (`livekit.plugins.cartesia`, what our providers return for `AgentSession`) vs. the
> *raw* Cartesia SDK (`from cartesia import Cartesia`, websocket). New §4.7 reconciles them. Real
> divergences captured: **model ids** (raw `sonic-3.5`/`sonic-turbo`/`ink-2` vs plugin defaults
> `sonic-3`/`ink-whisper`) and **audio formats** (raw TTS `pcm_f32le@44100`, raw STT `pcm_s16le@16000`).
> Production path is **unchanged** (we ship the plugin); the raw scripts become an *optional* no-LiveKit
> vendor smoke test (§8). Sources in §11.
>
> **v0.3 change (LiveKit sync):** verified every snippet against the current **LiveKit Agents v1**
> docs (June 2026). Corrections: Cartesia TTS default model is **`sonic-3`** (was `sonic-2`);
> **`sample_rate` is not a TTS constructor param** — the plugin emits its native rate and the
> room/`AudioSource` negotiates output, so it's dropped from construction; the standalone
> `synthesize()`/`stream()` round-trip in §4.4 is confirmed against the real API; the `AgentSession`
> wiring in §5 is updated (turn detection now lives in **`TurnHandlingOptions`**, VAD via
> `silero.VAD.load()`, `session.start(room=, agent=, room_options=…)`). New §4.6 notes the hosted
> `inference.*` backing as an additive option behind the same protocol. Sources in §11.
>
> **v0.2 change:** the mock STT/TTS provider is **removed**. We build **primarily on Cartesia** and
> verify the layer with a **real, runnable integration test script** that drives the actual Cartesia
> plugins standalone (§4.4, §8) — no fake/mock impl. The layer stays **extensible** to other LiveKit
> speech providers (Deepgram, ElevenLabs, …) via one new `providers/*.py`, but Cartesia is the
> primary, default, and only shipped impl.

---

## 1. Purpose & boundary (why this layer exists alone)

STT and TTS are the one model edge in Clutch that is **not** routed through the TrueFoundry gateway —
they run as **LiveKit plugins** inside the voice pipeline for latency (00 §6, 05 §3). So the
plug-and-play guarantee (00 §7) cannot be a gateway logical-name swap here; it has to be a small
**provider-adapter layer**. `src/speech/` *is* that layer — the voice analogue of Perception's
`VisionProvider` (02). It does exactly one job: **given config, hand back an STT plugin and a TTS
plugin**, built primarily on Cartesia, extensible to any LiveKit-supported speech vendor.

This layer is deliberately **construction-only** — it builds and returns plugin instances and holds
**no business logic**:

| Concern | Owner | Not here because |
|---|---|---|
| barge-in (`tts.stop()` on user speech) | `src/realtime/` voice loop | it's pipeline control, not plugin construction |
| VAD / turn detection / endpointing | `src/realtime/` (Silero VAD + LiveKit turn plugin) | not part of the frozen `STT/TTS` seam (05 §4) |
| STT→Agent→TTS wiring, frame sampling, events | `src/realtime/` | speech only *produces the plugins* the loop drives |
| what to say / dialogue state | Agent (04) | speech transports audio, decides nothing |
| token minting, room creation, company key | Tenancy (06) + realtime | scoping is enforced upstream |

**Independence contract (adapted from [10 §5](../hld/engineers/10-work-division-and-fusing.md)):**
- **Imports only** `src/contracts.py`. Never a sibling subfolder (`realtime/`, `agent/`, …).
- **Exposes** `get_stt(cfg)`, `get_tts(cfg)`.
- **Receives (passed in)** exactly one argument: `cfg`.
- **Self-test (replaces the mock):** a standalone **integration script**
  (`scripts/speech_e2e.py`, §4.4) drives the **real Cartesia** STT+TTS round-trip with
  nothing else in `src/` present. This is how the layer reaches "done + tested" alone — by exercising
  the actual vendor, not a stand-in.

> **Deviation from 10 §5, on purpose.** The work-division plan lists `mock_stt`/`mock_tts` as the
> build-against stand-ins. Per this LLD we drop them: a faked audio stream proves nothing about STT/TTS
> serving (the actual risk is latency/format/streaming behaviour of the real plugin). We replace the
> mock with a **real integration harness** gated on a Cartesia key. `src/app.py` therefore wires the
> speech plugins **unconditionally** (no `cfg.real("speech")` mock branch).

---

## 2. The frozen seam (do not widen without an all-hands PR)

From HLD 05 §4 — the public surface, frozen at the **signature** level:

```python
class STTProvider(Protocol):
    def stream(self) -> "livekit.STT": ...     # a LiveKit STT plugin instance
class TTSProvider(Protocol):
    def stream(self) -> "livekit.TTS": ...     # a LiveKit TTS plugin instance
def get_stt(cfg) -> STTProvider: ...           # picks impl by cfg.stt_provider (default cartesia)
def get_tts(cfg) -> TTSProvider: ...           # picks impl by cfg.tts_provider (default cartesia)
```

> The HLD's original comment said "mock in dev"; this LLD supersedes that — the dev/CI path is the
> **integration script against real Cartesia** (§8), not a mock. The protocol *signatures* are
> unchanged, so the seam is **not** widened.

Contract notes the impls below must honour:
- `get_stt`/`get_tts` return a **provider** (the selector object), not the plugin. The realtime loop
  calls `provider.stream()` to obtain a **fresh LiveKit plugin per session** (one room → one STT and
  one TTS stream; no cross-session sharing of buffers/turn state).
- `stream()` is **synchronous and cheap** — it constructs/returns the plugin; it does not open the
  network stream itself (LiveKit Agents opens the websocket when the pipeline starts the node).
- The returned TTS plugin **must support streaming + interruption** (`aclose()`/cancel mid-utterance)
  so the realtime loop's barge-in can stop it. This is a *property of the plugin we return*, not logic
  we add.
- `livekit.STT` / `livekit.TTS` are referenced as **string annotations only** — the module never
  imports livekit at top level, so provider *selection* is testable without livekit installed
  (mirrors `qwen_gateway.py`'s lazy `from openai import AsyncOpenAI`).

---

## 3. File layout (mirrors `src/perception/`, mock removed)

Perception is the template the HLD points at twice (05 §3, 14 §2). Match its shape — minus a mock.
**The e2e check lives in `scripts/`, not in the Limb** — same convention as
`scripts/retrieval_mini.py` (LLD 03 §12) and `scripts/realtime_mini.py` (LLD 05-realtime §9):

```
src/speech/                # shipped code only — no test/demo scaffolding inside the Limb
  __init__.py              # re-export get_stt, get_tts, STTProvider, TTSProvider
  stt.py                   # get_stt(cfg) selector (default cartesia)
  tts.py                   # get_tts(cfg) selector (default cartesia)
  providers/
    __init__.py
    base.py                # STTProvider / TTSProvider Protocols  (the Spine of this layer)
    cartesia.py            # CartesiaSTT / CartesiaTTS — the primary impls (LiveKit plugins)
    # (future) deepgram.py / elevenlabs.py / openai.py — add an impl, no caller change

scripts/
  speech_e2e.py            # the ONE real test of the layer (§8): real-Cartesia TTS→STT round-trip;
                           #   `--check` is the bare-minimum gate, keyless ⇒ SKIP, `--raw` ⇒ raw-SDK smoke

tests/
  test_speech_selection.py # bare-minimum unit: pure selection logic only (no livekit, no key) — §8
```

`stt.py`/`tts.py` stay thin (selection + default). The real work — wrapping a LiveKit plugin — lives
in `providers/cartesia.py`, exactly like `perception/providers/qwen_gateway.py`. There is **no
`mock.py`**; the Limb ships **only shipped code**, and `scripts/speech_e2e.py` is the standalone test
entry point (mirrors how `realtime/` keeps its harness in `scripts/`, not in the subfolder).

---

## 4. Detailed design

### 4.1 `providers/base.py` — the protocols

```python
"""STTProvider / TTSProvider protocols — the voice swap point (HLD 05 §3, 00 §7)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:                       # never imported at runtime → selection needs no livekit
    from livekit.agents import stt as lk_stt, tts as lk_tts


class STTProvider(Protocol):
    def stream(self) -> "lk_stt.STT": ...   # fresh LiveKit STT plugin per call


class TTSProvider(Protocol):
    def stream(self) -> "lk_tts.TTS": ...   # fresh LiveKit TTS plugin per call
```

No prompt/schema helper is needed here (unlike `perception/providers/base.py`, which carries
`RESPONSE_SCHEMA`/`build_prompt`) — STT/TTS exchange audio frames, not JSON, so the only shared shape
is the two protocols.

### 4.2 `stt.py` — `get_stt(cfg)`

```python
"""get_stt(cfg) -> STTProvider. Picks a LiveKit STT plugin by role-named config; Cartesia default."""
from __future__ import annotations
from speech.providers.base import STTProvider

DEFAULT = "cartesia"   # primary + only shipped impl; other vendors are additive

def get_stt(cfg) -> STTProvider:
    name = (getattr(cfg, "stt_provider", None) or DEFAULT).lower()
    if name in ("cartesia", "default"):
        from speech.providers.cartesia import CartesiaSTT     # lazy: importing livekit only on use
        return CartesiaSTT(cfg)
    # extension point — one new elif per added vendor, no change above:
    #   if name == "deepgram":
    #       from speech.providers.deepgram import DeepgramSTT
    #       return DeepgramSTT(cfg)
    raise ValueError(f"unknown stt_provider {name!r} (have: cartesia)")
```

`get_tts(cfg)` is the mirror (`CartesiaTTS`, keyed on `cfg.tts_provider`).

Design choices:
- **Default = `cartesia`.** Cartesia is the primary build target; a blank config resolves to it.
- **Selection by role string** (`cartesia`/future `deepgram`/…), never a vendor class imported in
  callers — so adding a vendor is a config value + one new `providers/*.py`, never a caller edit
  (00 §7). The architecture still scales to other protocols; we just ship one.
- **Unknown name fails loud** — a typo in config is a startup error, not a silent wrong-vendor pick.

### 4.3 `providers/cartesia.py` — the primary impl

Thin wrapper over the LiveKit Cartesia plugin. Construction reads vendor creds from **env** (mirrors
`qwen_gateway.py` reading `TRUEFOUNDRY_*` from `os.environ`), so `src/config.py` stays role-named and
holds **no `cartesia_*` keys** (00 §7, 10 §8.6).

Verified against the LiveKit Agents v1 Cartesia plugin docs (June 2026): import `from
livekit.plugins import cartesia`; classes `cartesia.STT` / `cartesia.TTS`. **STT** params:
`model="ink-whisper"` (default), `language="en"`. **TTS** params: `model="sonic-3"` (default),
`voice` (default voice id provided by the plugin), `language`, `speed`, `volume`, `emotion`.
**Auth is via the `CARTESIA_API_KEY` env var** (the plugin reads it); we resolve it ourselves only to
raise a clean error early. **`sample_rate` is *not* a TTS constructor param** in v1 — the plugin emits
its native rate and the LiveKit room/`AudioSource` negotiates the output rate, so we don't set it.

```python
"""Cartesia STT/TTS as LiveKit plugins — the voice primary (HLD 05 §7).

Verified against LiveKit Agents v1 (June 2026). API key is checked BEFORE the lazy livekit import so
a missing key raises a clean RuntimeError even where the plugin isn't installed (keeps the keyless
selection/missing-key tests in §8 livekit-free).
"""
from __future__ import annotations
import os
from typing import Optional

_MISSING = "CARTESIA_API_KEY unset — required to build the Cartesia {kind} plugin"


class CartesiaSTT:
    def __init__(self, cfg=None,
                 api_key: Optional[str] = None, model: Optional[str] = None,
                 language: Optional[str] = None):
        self.api_key  = api_key  or os.environ.get("CARTESIA_API_KEY")
        self.model    = model    or os.environ.get("CARTESIA_STT_MODEL", "ink-whisper")
        self.language = language or os.environ.get("CARTESIA_LANGUAGE", "en")

    def stream(self) -> "livekit.STT":
        if not self.api_key:
            raise RuntimeError(_MISSING.format(kind="STT"))
        from livekit.plugins import cartesia            # lazy: only this path needs the plugin
        return cartesia.STT(api_key=self.api_key, model=self.model, language=self.language)


class CartesiaTTS:
    def __init__(self, cfg=None,
                 api_key: Optional[str] = None, model: Optional[str] = None,
                 voice: Optional[str] = None, language: Optional[str] = None):
        self.api_key  = api_key  or os.environ.get("CARTESIA_API_KEY")
        self.model    = model    or os.environ.get("CARTESIA_TTS_MODEL", "sonic-3")
        self.voice    = voice    or os.environ.get("CARTESIA_VOICE_ID", "")   # "" → plugin default
        self.language = language or os.environ.get("CARTESIA_LANGUAGE", "en")

    def stream(self) -> "livekit.TTS":
        if not self.api_key:
            raise RuntimeError(_MISSING.format(kind="TTS"))
        from livekit.plugins import cartesia
        kw = {"api_key": self.api_key, "model": self.model, "language": self.language}
        if self.voice:                       # omit → use the plugin's default voice
            kw["voice"] = self.voice
        return cartesia.TTS(**kw)             # NB: no sample_rate — see note above
```

(Exact plugin kwargs track the installed `livekit-plugins-cartesia` version; the swap point is what
keeps that detail isolated to this file. `speed`/`volume`/`emotion` are available and can be added the
same way if product wants them — they don't change the protocol.)

### 4.4 `scripts/speech_e2e.py` — the standalone self-test (in `scripts/`, not the Limb)

A **runnable** script (in `scripts/`, like the sibling LLDs' harnesses) that proves the layer
end-to-end against the **real Cartesia** plugins with **nothing else in `src/` present**. This is the
"independently testable" guarantee: build/verify the speech layer in isolation by actually synthesizing
and transcribing audio. It puts `src/` on `sys.path` (matching `pyproject.toml` `pythonpath=["src"]`)
and imports only `from speech import …`.

What it does (a TTS→STT round-trip — the strongest single check that both plugins serve correctly):

```python
"""scripts/speech_e2e.py — standalone e2e for src/speech/: drives REAL Cartesia STT+TTS.

Run:   python scripts/speech_e2e.py            # round-trip via the LiveKit plugin
       python scripts/speech_e2e.py --check    # bare-minimum gate: exits 0 PASS / non-zero FAIL
       python scripts/speech_e2e.py --raw      # optional raw-SDK vendor smoke (no LiveKit) — §4.7/§8
Keyless ⇒ prints SKIP and exits 0 (never blocks a keyless checkout). Requires for a real run:
livekit-agents, livekit-plugins-cartesia, CARTESIA_API_KEY.

API verified against LiveKit Agents v1 (June 2026):
  TTS  tts.synthesize(text) -> ChunkedStream; iterate -> SynthesizedAudio.frame (rtc.AudioFrame)
  STT  stt.stream() -> SpeechStream; push_frame()/end_input(); SpeechEvent.type / alternatives[0].text
"""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))   # bare `python scripts/...`
from speech.stt import get_stt          # noqa: E402  (after sys.path bootstrap)
from speech.tts import get_tts          # noqa: E402

PHRASE = "the printer shows an amber blinking light"


async def _synthesize(tts) -> tuple[bytes, int, int]:
    """Return (s16le PCM, sample_rate, num_channels) for PHRASE — proves TTS streams audio."""
    pcm, sr, ch = bytearray(), 0, 1
    stream = tts.synthesize(PHRASE)             # ChunkedStream
    async for ev in stream:                     # SynthesizedAudio
        f = ev.frame                            # rtc.AudioFrame (native rate; no sample_rate to set)
        sr, ch = f.sample_rate, f.num_channels
        pcm += bytes(f.data)                    # int16 PCM
    await stream.aclose()
    return bytes(pcm), sr, ch


async def _transcribe(stt, pcm: bytes, sr: int, ch: int) -> str:
    """Push PCM through the STT SpeechStream, return the final transcript."""
    from livekit import rtc
    from livekit.agents import stt as lk_stt

    ss = stt.stream()                           # SpeechStream
    step = int(sr * 0.1) * 2 * ch               # 100 ms of s16 audio, in bytes
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        ss.push_frame(rtc.AudioFrame(
            data=chunk, sample_rate=sr, num_channels=ch,
            samples_per_channel=len(chunk) // (2 * ch),
        ))
    ss.end_input()                              # flush — emit the FINAL_TRANSCRIPT

    text = ""
    async for ev in ss:
        if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
            text += ev.alternatives[0].text + " "
    await ss.aclose()
    return text.strip()


def _selection_checks() -> None:
    """Pure, keyless selection logic — the bare-minimum guard, run on every invocation (no deps)."""
    assert type(get_stt(SimpleNamespace())).__name__ == "CartesiaSTT"          # default → Cartesia
    assert type(get_tts(SimpleNamespace())).__name__ == "CartesiaTTS"
    try:
        get_stt(SimpleNamespace(stt_provider="bogus")); raise AssertionError("expected ValueError")
    except ValueError:
        pass
    print("PASS selection (default→cartesia, unknown→raise)")


async def main(argv: list[str]) -> int:
    _selection_checks()                          # always — keyless, fast
    if "--raw" in argv:
        return await _raw_smoke()                # raw Cartesia SDK, no LiveKit (§4.7) — optional

    if not os.environ.get("CARTESIA_API_KEY"):
        print("SKIP: CARTESIA_API_KEY unset (selection checks passed)"); return 0   # keyless ⇒ skip
    cfg = SimpleNamespace(stt_provider="cartesia", tts_provider="cartesia")
    tts = get_tts(cfg).stream()                  # our provider.stream() -> Cartesia TTS plugin
    stt = get_stt(cfg).stream()                  # our provider.stream() -> Cartesia STT plugin

    pcm, sr, ch = await _synthesize(tts)
    assert pcm, "TTS produced no audio"
    print(f"PASS tts: {len(pcm)} bytes PCM @ {sr} Hz x{ch}")

    text = await _transcribe(stt, pcm, sr, ch)
    assert text, "STT produced empty transcript"
    overlap = len(set(text.lower().split()) & set(PHRASE.split()))
    assert overlap >= 2, f"STT transcript far from input: {text!r}"
    print(f"PASS stt: {text!r} ({overlap} words overlap)")
    print("PASS round-trip"); return 0


if __name__ == "__main__":                       # `--check` is just the default run + non-zero on fail
    sys.exit(asyncio.run(main(sys.argv[1:])))
```

Properties:
- **Real vendor, no fakes** — exercises actual Cartesia serving (latency, audio format, streaming),
  which is the real risk a mock would have hidden.
- **`--check` is the bare-minimum gate** — selection asserts (keyless) + the real round-trip (when a key
  is present); exits non-zero on any failure, so it drops straight into CI as the acceptance gate. This
  mirrors `scripts/realtime_mini.py --check` (LLD 05-realtime §9.3): the script *is* the test.
- **Keyless = SKIP, not FAIL** — selection checks still run; the real round-trip is skipped so a
  contributor without a Cartesia key isn't blocked; CI with the secret runs it for real (§8).
- **Self-contained** — imports only `speech.*` + livekit; no other `src/` section, no realtime loop.
- **Round-trip is the assertion** — TTS output fed straight into STT and checked for word overlap
  proves both directions in one run without shipping a sample-audio fixture.

No `tests/speech/` integration suite and no pytest wrapper for the e2e — running the script is the test
(LLD 05-realtime convention). The only `tests/` file is the bare-minimum selection micro-test (§8).

### 4.5 `__init__.py`

```python
from speech.stt import get_stt
from speech.tts import get_tts
from speech.providers.base import STTProvider, TTSProvider
__all__ = ["get_stt", "get_tts", "STTProvider", "TTSProvider"]
```

### 4.6 Two backings for the same protocol — local plugin vs. hosted `inference.*`

LiveKit Agents v1 offers Cartesia **two ways**, and both satisfy our `STTProvider`/`TTSProvider`
protocol unchanged:
- **Local plugin (our default):** `from livekit.plugins import cartesia` → `cartesia.STT/TTS`. The
  vendor key (`CARTESIA_API_KEY`) is ours; lowest-latency; matches HLD 05's "STT/TTS run as LiveKit
  plugins, *not* gateway-routed."
- **Hosted inference (optional):** `from livekit.agents import inference` →
  `inference.TTS(model="cartesia/sonic-3", voice=…)` / `inference.STT(model="…")`. LiveKit hosts the
  call and bills it; no Cartesia key needed. This is a *different* gateway than TrueFoundry (06) and is
  **not** how we route the reasoning brain — it would only ever back speech.

Decision: **ship the local plugin.** If we ever want the hosted path, it's a third provider
(`providers/livekit_inference.py`) selected by `stt_provider="livekit"` — purely additive, no caller or
protocol change. Noted so the choice is explicit, not accidental.

### 4.7 Cartesia plugin vs. raw SDK — what we ship, and reconciling model ids/formats

There are **two distinct Cartesia surfaces**; the LLD uses the first and must not conflate them:

| | **LiveKit plugin** — what `CartesiaSTT/TTS.stream()` return | **Raw Cartesia SDK** — `from cartesia import Cartesia` |
|---|---|---|
| Import | `from livekit.plugins import cartesia` | `from cartesia import Cartesia` |
| Object | `cartesia.STT/TTS` → a `livekit.STT/TTS` | `Cartesia(...).tts` / `.stt` websocket clients |
| Plugs into `AgentSession`? | **Yes** — required by `stream() -> livekit.STT` | **No** — raw websocket/byte client |
| TTS drive | `tts.synthesize(text)` / `tts.stream()+push_text()` | `ws.context(...)` → `push()` / `no_more_inputs()` / `receive()` |
| STT drive | `stt.stream()` + `push_frame()` / `end_input()` | `stt.manual_finalize.websocket(...)` → `send_raw()` / `send("finalize"\|"close")` |
| Transcript | `SpeechEvent.alternatives[0].text` | `event.type=="transcript" and event.is_final → event.text` |
| TTS model id | **`sonic-3`** (plugin default) | **`sonic-3.5`** (latest) / `sonic-turbo` (40 ms TTFA) |
| STT model id | **`ink-whisper`** (plugin default) | **`ink-2`** (native turn detection) / `ink-whisper` |
| Audio format | handled by the plugin + `rtc` resampling | TTS out `pcm_f32le@44100`; STT in `pcm_s16le@16000` |

**Conclusion — the production path does not change.** Our contract is `stream() -> livekit.STT/TTS`
because the plugins must drop into `AgentSession` (barge-in + turn detection, §5). So **we ship the
LiveKit plugin**, which speaks Cartesia's websocket *internally* — the raw SDK is what it wraps, never
something our callers touch.

**The one real reconciliation: model ids.** Plugin and raw API name models differently. Both are
env-overridable (`CARTESIA_TTS_MODEL` / `CARTESIA_STT_MODEL`, §7.2). Rule: **set the id the *installed
`livekit-plugins-cartesia` version* accepts** — the plugin forwards `model=` to Cartesia, so newer ids
(`sonic-3.5`, `sonic-turbo`, `ink-2`) generally pass through, but confirm against the pinned plugin on
integration day rather than assuming. Default stays at the plugin default (`sonic-3`/`ink-whisper`);
prefer `sonic-3.5` (Cartesia's current latest, auto-tracks the stable snapshot) once verified.

The raw SDK API on the right is the **canonical reference for the optional no-LiveKit vendor smoke
test** (§8) — it proves the account/key/models work independent of LiveKit, but it is **not** the
shipped path. Verbatim raw-SDK shapes (for that smoke test):

```python
# raw TTS (websocket) — pcm_f32le @ 44100
with client.tts.websocket_connect() as conn:
    ctx = conn.context(model_id="sonic-3.5", voice={"mode": "id", "id": "<voice_id>"},
                       output_format={"container": "raw", "encoding": "pcm_f32le", "sample_rate": 44100})
    for part in chunks: ctx.push(part)
    ctx.no_more_inputs()
    for r in ctx.receive():
        if r.type == "chunk" and r.audio: audio.write(r.audio)
        elif r.type == "done": break

# raw STT (manual-finalize websocket) — pcm_s16le @ 16000
with client.stt.manual_finalize.websocket(encoding="pcm_s16le", model="ink-2", sample_rate=16000) as conn:
    for chunk in chunks_100ms: conn.send_raw(chunk)
    conn.send("finalize"); conn.send("close")
    text = "".join(e.text for e in conn if e.type == "transcript" and e.is_final)
```

---

## 5. Lifecycle / how the realtime loop consumes it (informational only)

Wiring shown against the **current LiveKit Agents v1 `AgentSession`** API (turn detection lives in
`TurnHandlingOptions`; VAD via `silero.VAD.load()`; `session.start(room=, agent=, room_options=…)`).
All of this is `src/realtime/`'s code — reproduced only to show the two plugins land correctly:

```python
# src/realtime/ (NOT this layer) — speech only supplies stt/tts:
from livekit.agents import AgentSession, TurnHandlingOptions, room_io
from livekit.plugins import silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

stt, tts = get_stt(cfg), get_tts(cfg)              # ← our layer, once at build (Cartesia)
session = AgentSession(
    stt=stt.stream(),                              # fresh Cartesia STT plugin per session
    tts=tts.stream(),                              # fresh Cartesia TTS plugin per session
    llm=tonmoy_agent,                              # the Agent (04) is the "LLM" step
    vad=silero.VAD.load(),
    turn_handling=TurnHandlingOptions(turn_detection=MultilingualModel()),
)
await session.start(room=ctx.room, agent=Assistant(), room_options=room_io.RoomOptions(...))
# barge-in / interruptions are handled BY AgentSession (allow_interruptions) — realtime's concern;
# our only obligation is that the TTS plugin we return is interruptible (Cartesia is). §2.
```

This LLD guarantees the two `stream()` outputs are valid LiveKit v1 plugins with the properties §2
lists; everything in that block is `src/realtime/`'s LLD, not this one. With the mock gone, `app.py`
wires the Cartesia plugins **unconditionally** — there is no mock branch to flip.

---

## 6. Failure modes owned by this layer

| Failure | Behaviour | Source |
|---|---|---|
| `cfg.stt_provider`/`tts_provider` blank | default to `cartesia` | §4.2 |
| Unknown provider name | raise `ValueError` at `get_*` (loud, startup) | §4.2 |
| `CARTESIA_API_KEY` unset | raise `RuntimeError` at `stream()` (no silent degrade) | §4.3 |
| TTS provider down at runtime | **not handled here** — realtime swaps `tts_provider`→alternate plugin and renders a text card meanwhile (05 §6, 14 §6); this layer just makes that swap a config flip |
| STT mis-hears | **not here** — realtime offers push-to-talk / Type in the same room (05 §6) |

Rule of thumb: this layer fails **only** on *construction/config* errors. Stream-time audio failures
are the realtime loop's fallbacks — kept out so the swap point stays pure.

---

## 7. Config changes needed

`src/config.py` is **owned by Arham** and does not exist in the tree yet; its keys are **role-named**
and adding one is a **Spine-adjacent PR Arham reviews** (10 §8.6). This layer needs:

### 7.1 New `Config` fields (role-named — the swap selectors)
| Key | Type | Default | Notes |
|---|---|---|---|
| `stt_provider` | `str` | `"cartesia"` | role name, **never** `cartesia_stt`; selects the impl in `get_stt` |
| `tts_provider` | `str` | `"cartesia"` | same |

These two keys are the **entire** config surface this layer reads. No mock branch / `cfg.real("speech")`
is required (the mock is gone — §4 deviation). No other section's keys change.

### 7.2 Environment variables (vendor creds/tuning — NOT in `config.py`)
Kept out of `config.py` to preserve the "config names roles, not vendors" rule (00 §7); read lazily
inside `providers/cartesia.py` exactly as `qwen_gateway.py` reads `TRUEFOUNDRY_*`:

| Env var | Required? | Default | Purpose |
|---|---|---|---|
| `CARTESIA_API_KEY` | **yes** | — | auth (the plugin reads it; we resolve it to fail early); absent ⇒ `stream()` raises, integration check SKIPs |
| `CARTESIA_STT_MODEL` | no | `ink-whisper` | STT model id; alt `ink-2` (native turn detection) — see §4.7 |
| `CARTESIA_TTS_MODEL` | no | `sonic-3` | TTS model id (plugin default); prefer `sonic-3.5` (latest) / `sonic-turbo` (low-latency) once plugin pass-through is verified — §4.7 |
| `CARTESIA_VOICE_ID` | no | (plugin default) | TTS voice id; blank ⇒ plugin's built-in default voice |
| `CARTESIA_LANGUAGE` | no | `en` | STT + TTS language |

> **Dropped `CARTESIA_SAMPLE_RATE`** — `sample_rate` is not a v1 Cartesia TTS constructor param. The
> TTS plugin emits its native rate and the LiveKit room/`AudioSource` resamples to the room rate; the
> integration check reads the rate **off the produced `AudioFrame`** rather than configuring it.
> (`speed`/`volume`/`emotion` exist as TTS params if product ever wants them — add via env + a `kw`
> line in `cartesia.py`, no protocol change.)

Add these to `.env.example` / deploy secrets (06), and `CARTESIA_API_KEY` to the **CI secret** so the
integration check runs for real. **LiveKit room/transport creds**
(`LIVEKIT_URL`/`API_KEY`/`API_SECRET`) are **realtime/06 config, not speech** — listed only so they
aren't duplicated here.

### 7.3 Dependencies (`pyproject.toml` / `requirements`)
Add a dedicated **`voice`** dependency group so the keyless selection tests (§8) still install nothing:

```toml
[dependency-groups]
voice = [
    "livekit-agents>=1.0",            # AgentSession/inference + stt/tts base classes + rtc.AudioFrame
    "livekit-plugins-cartesia>=1.0",  # the primary STT + TTS plugins (sonic-3 / ink-whisper)
]
```

| Package | Why | Import discipline |
|---|---|---|
| `livekit-agents` | `rtc.AudioFrame`, `stt.SpeechEventType`, the plugin base classes used by the integration check | lazy — only imported inside `stream()` / `scripts/speech_e2e.py` |
| `livekit-plugins-cartesia` | the primary STT+TTS plugins | lazy — only on the `cartesia` path |

Note: `silero` (VAD) and `turn-detector` plugins seen in §5 belong to **`realtime/`**, not here — not
added by this layer. Like `openai` in perception, **neither voice package is imported at module top**
→ `get_stt`/`get_tts` *selection* stays importable with nothing installed (so the §8 selection tests
run anywhere). The integration check and the realtime/CI env install the `voice` group.

---

## 8. Testing (`tests/speech/…` — lives with the Limb, 10 §8.5)

Three tiers, no mock (Tier 3 optional):

**Tier 1 — selection unit tests (no livekit, no key, run anywhere):**
| Test | Asserts |
|---|---|
| `test_get_stt_default_is_cartesia` | blank cfg → `CartesiaSTT` |
| `test_get_tts_selects_cartesia` | `cfg.tts_provider="cartesia"` → `CartesiaTTS` |
| `test_unknown_provider_raises` | bad name → `ValueError` |
| `test_cartesia_missing_key_raises` | no `CARTESIA_API_KEY` → `RuntimeError` at `stream()` |

(These only construct providers / call selection — `livekit` never imported, so they're green on a
bare clone.)

**Tier 2 — the integration check (real Cartesia *via the LiveKit plugin* — the headline self-test):**
- `python scripts/speech_e2e.py --check` — the runnable TTS→STT round-trip (§4.4). Tests the **shipped
  artifact** (the `livekit.STT/TTS` our providers return), so the plugin + `rtc` resampling are covered.
  **`SKIP` when `CARTESIA_API_KEY` unset** (selection checks still run), runs for real in CI where the
  secret is set — no pytest wrapper, the script *is* the test (v0.5; mirrors `scripts/realtime_mini.py`).

**Tier 3 — *optional* raw-SDK vendor smoke test (no LiveKit; §4.7 shapes):** `scripts/cartesia_smoke.py`
using `from cartesia import Cartesia` directly. Proves the **account/key/models** are good independent
of LiveKit — useful to isolate "is it Cartesia or is it our wrapper?" when Tier 2 fails. Caveat: raw TTS
emits **`pcm_f32le@44100`** and raw STT wants **`pcm_s16le@16000`**, so a *raw round-trip* needs an
explicit float32→int16 + 44100→16000 resample. To avoid shipping that conversion, run Tier 3 as **two
independent checks** (TTS writes audio bytes; STT transcribes a checked-in `pcm_s16le@16000` fixture),
not a round-trip. Tier 2 stays the round-trip because the LiveKit plugin handles resampling for us.
Needs the `cartesia` package (an extra dep, kept out of the default install).

Tiers 2–3 are the deliberate replacement for `mock_stt`/`mock_tts`: the layer is verified
**independently** by exercising the actual vendor (through the plugin we ship, and optionally the raw
SDK underneath), not a stand-in. Mirrors perception's standalone test gate, but with a real provider.

---

## 9. Definition of done (the speech rows of brief [14 §5](../hld/engineers/14-fardin-inference-retrieval-voice.md))

- [ ] `STTProvider`/`TTSProvider` protocols in `providers/base.py` (string-annotated, livekit-free at import).
- [ ] `get_stt(cfg)`/`get_tts(cfg)` select by `cfg.stt_provider`/`tts_provider`; **default `cartesia`**; unknown → raise.
- [ ] `CartesiaSTT`/`CartesiaTTS` return real LiveKit plugins; creds from env; interruptible TTS.
- [ ] **No mock impl** — `scripts/speech_e2e.py --check` runs a real Cartesia TTS→STT round-trip standalone.
- [ ] Extensible: adding a second vendor is one `providers/*.py` + one `elif`, no caller change (00 §7).
- [ ] `config.py` gains `stt_provider`/`tts_provider` (Arham PR); env vars + deps + CI secret documented (§7).
- [ ] Tier-1 selection tests green on a bare clone; Tier-2 integration check green in CI with the key.

## 10. Open questions (this layer's slice of 05 §8 / 14 §8)
- **VAD ownership:** keep VAD/turn-detection in `realtime/` (current plan) vs. expose an optional
  `get_vad(cfg)` from speech for symmetry. Default: **realtime** — not on the frozen STT/TTS seam.
- **Speech-to-speech** (single Cartesia/realtime model) vs. discrete STT→LLM→TTS. Default discrete
  (Tier 0); a future `S2SProvider` would be a *third* provider kind here, additive.
- **Per-modality tuning** (e.g. tighter latency profile for Talk vs See) — env/config knob vs. a param
  on `stream()`; deferred, would widen the frozen seam so needs an all-hands PR.
- **Second provider as proof of portability:** add `deepgram.py` purely to demonstrate the swap point,
  or keep Cartesia-only until a real need? Default: Cartesia-only; the `elif` extension point stands ready.

---

## 11. API verification (June 2026)

Every LiveKit- and Cartesia-touching snippet above was checked against live docs. Confirmed / corrected:

**LiveKit Agents v1 (the shipped path):**
| Item | Source | Status |
|---|---|---|
| Cartesia **STT** = `cartesia.STT(model="ink-whisper", language="en")`, key via `CARTESIA_API_KEY` | [STT/Cartesia](https://docs.livekit.io/agents/integrations/stt/cartesia/) | confirmed |
| Cartesia **TTS** default model **`sonic-3`**; params `voice`/`language`/`speed`/`volume`/`emotion`; **no `sample_rate`** | [TTS/Cartesia](https://docs.livekit.io/agents/integrations/tts/cartesia/) | **corrected** (was `sonic-2` + bogus `sample_rate`) |
| Standalone **TTS**: `tts.synthesize(text)` → `ChunkedStream`, iterate → `SynthesizedAudio.frame` | [TTS integrations](https://docs.livekit.io/agents/integrations/tts/) · [py ref](https://docs.livekit.io/reference/python/livekit/agents/tts/index.html) | confirmed |
| Standalone **STT**: `stt.stream()` → `SpeechStream`; `push_frame`/`end_input`; `SpeechEventType.FINAL_TRANSCRIPT` / `alternatives[0].text` | [STT integrations](https://docs.livekit.io/agents/integrations/stt/) · [py ref](https://docs.livekit.io/reference/python/livekit/agents/stt/stt.html) | confirmed |
| `AgentSession(stt=, llm=, tts=, vad=silero.VAD.load(), turn_handling=TurnHandlingOptions(turn_detection=…))` + `session.start(room=, agent=, room_options=…)` | [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/) | **synced** (turn detection → `TurnHandlingOptions`) |
| Hosted alternative `inference.TTS(model="cartesia/sonic-3", voice=…)` / `inference.STT(...)` | [Voice AI quickstart](https://docs.livekit.io/agents/start/voice-ai/) | noted (§4.6), not adopted |
| Non-streaming STT needs `stt.StreamAdapter(stt, vad)` | [STT integrations](https://docs.livekit.io/agents/integrations/stt/) | N/A — Cartesia `ink-whisper` is streaming, direct `stream()` works |

**Raw Cartesia SDK (the layer underneath; optional Tier-3 smoke test, §4.7/§8):**
| Item | Source | Status |
|---|---|---|
| TTS websocket: `client.tts.websocket_connect()` → `ctx.push()` / `no_more_inputs()` / `receive()` (`chunk`/`done`); out `pcm_f32le@44100` | [Realtime TTS quickstart](https://docs.cartesia.ai/get-started/realtime-text-to-speech-quickstart) | confirmed |
| STT websocket: `client.stt.manual_finalize.websocket(encoding, model, sample_rate)` → `send_raw()` / `send("finalize"\|"close")`; events `transcript`+`is_final`; in `pcm_s16le@16000` | [STT manual-finalize example](https://docs.cartesia.ai/examples/stt-manual-finalize-websocket) | confirmed |
| TTS models **`sonic-3.5`** (latest, auto-tracks stable) / `sonic-turbo` (40 ms TTFA); `sonic-3`/`sonic-2` now "older" | [Sonic models](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest) | **reconciled** vs plugin default `sonic-3` (§4.7) |
| STT models **`ink-2`** (native turn detection) / `ink-whisper` (Whisper-based) | [Cartesia Ink](https://www.cartesia.ai/ink) | **reconciled** vs plugin default `ink-whisper` (§4.7) |

**Net:** the two surfaces agree once you treat them as layers — the LiveKit plugin (what we ship) wraps
the raw websocket (what the user verified). The only items needing operator attention are the **model
ids** (set per the installed plugin version) and, for a *raw* round-trip, the **format conversion**
(f32le@44100 → s16le@16000). Re-verify on any `livekit-agents` **or** `cartesia` major bump.
