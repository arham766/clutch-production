# Clutch HLD 05 — Realtime, Voice & Widget

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** LiveKit · Qwen (voice)
**Depends on:** Agent (04), Perception (02), Gateway/Tenancy (06) · **Consumed by:** the customer

---

## 1. Purpose
The **customer-facing surface**: the one-line embed snippet a company drops on its support page, the
chat widget it renders, and the LiveKit realtime layer that powers all three modes — **Type** (text),
**Talk** (live voice), **See** (live camera). It is the transport + UI shell only: it carries
transcripts/intents/frames *up* to the Agent (04) and Perception (02), and renders answers + UI events
(citations, confirm prompts) pushed *down*. It decides nothing about *what* to say — that is the Agent's job.

## 2. Scope
**In:** embed snippet (carries the company key), widget UI (transcript, voice indicator, camera
viewfinder, cited-answer cards, confirm prompt), LiveKit room per session (text data channel, audio,
audio+video), Cartesia STT/TTS with turn detection + barge-in, gated frame sampling → 02 (FrameGate), modality transitions
within one session, the agent→UI data-channel event protocol.
**Out:** what to say / dialogue state (04), retrieval + grounding (03), product/problem vision (02),
token minting + tenant isolation (06).

## 3. Design (how it works; key decisions)
- **One snippet, one key.** `<script src=".../widget.js" data-clutch-key="..."></script>` loads the
  widget. The key scopes the session to one company (06); the widget exchanges it for a short-lived
  LiveKit token via the connection-details endpoint (reuse the scaffold's `app/api/connection-details`).
- **LiveKit room per session.** One room per `SupportSession`. **Type** uses the data channel only
  (no media); **Talk** adds the mic audio track; **See** adds the camera video track. Modality is a
  property of the same session — escalating Type→Talk→See **reuses the room**, just publishing more
  tracks, so history/`product_id` carry over (HLD 04 may suggest "See" mid-conversation).
- **Voice pipeline (provider-agnostic; Cartesia default).** STT → Agent (04, the brain via 06) → TTS,
  run as a LiveKit Agents pipeline where the "LLM" step is the Agent, not a raw model (mirrors the
  `livekit-moss-agent` scaffold). STT/TTS run as **LiveKit plugins** (not via the TF gateway; the
  gateway routes the reasoning brain + vision). VAD/turn-detection biased toward **fast endpointing**;
  barge-in is mandatory — on user speech the agent's TTS stops immediately (`tts.stop()`) and the
  partial transcript flows to 04.
- **Speech swap point (00 §7).** Because STT/TTS are *not* gateway-routed, plug-and-play here is the
  **`STTProvider` / `TTSProvider` adapter layer** in `src/speech/{stt,tts}.py` — the voice analogue of
  Perception's `VisionProvider`. Each is a thin protocol over the corresponding **LiveKit plugin**, so
  the provider is chosen by config (`stt_provider` / `tts_provider`, 06) and Cartesia (the default) can
  be replaced by any LiveKit-supported STT/TTS (Deepgram, ElevenLabs, OpenAI, Whisper, …) by selecting
  a different plugin — no change in the pipeline, the Agent, or the widget. A deterministic mock backs
  tests/dev (no keys), matching the mock-first pattern in 02.
- **Frame sampling for See (gated).** Sample the video track (~1–2 fps, not every frame) → run each
  through Perception's **`FrameGate.should_process`** (02; rolling pHash + rate cap) and call
  `identify` **only on a meaningful view change** — Qwen isn't hit while the customer holds still. The
  Agent (04) consumes the resulting `IdentifyResult` and drives confirmation. (The gate is Perception's;
  the realtime loop just drives it — seam S8.)
- **Agent→UI events over the data channel.** The Agent publishes typed JSON events (reusing the
  scaffold's `publish_data` + `moss_context` pattern) that the widget renders: cited-answer cards,
  the "is this the *X*?" confirm prompt, voice/listening state, latency badge.

## 4. Data & interfaces (PRODUCES / CONSUMES; see [00](00-overview.md) contracts)
Consumes from **04**: `Answer{text, citations}` (rendered as cards), agent utterances (→ TTS), and
modality-escalation suggestions. Produces to **04**: final/partial transcripts + typed user intents,
plus a `SupportSession{session_id, company_id, modality, product_id, history}`. Produces to **02**:
sampled `frames` (See). Consumes `IdentifyResult` indirectly (via 04, for the confirm prompt).

```python
# speech provider contract — src/speech/{stt,tts}.py (the voice swap point; default = Cartesia)
class STTProvider(Protocol):
    def stream(self) -> "livekit.STT": ...     # returns a LiveKit STT plugin instance
class TTSProvider(Protocol):
    def stream(self) -> "livekit.TTS": ...     # returns a LiveKit TTS plugin instance
def get_stt(cfg) -> STTProvider: ...           # picks impl by cfg.stt_provider (06); mock in dev
def get_tts(cfg) -> TTSProvider: ...           # picks impl by cfg.tts_provider (06); mock in dev

# session/transport surface (LiveKit agent side)
start_session(company_key, modality) -> SupportSession   # token via 06, create room
attach_audio(track); attach_video(track)                 # Talk / See escalation
on_partial(cb); on_final(cb); on_interrupt(cb)           # STT (provider) → 04
say(text)                                                 # → TTS (provider), interruptible

# agent→UI data-channel events (typed JSON; widget renders)
{"type": "answer",  "data": {"text", "citations": [{"doc","section","score"}]}}
{"type": "confirm", "data": {"prompt": "Is this the <X>?", "options": [...]}}
{"type": "voice_state", "data": {"state": "listening|speaking|thinking"}}
{"type": "latency",  "data": {"time_taken_ms"}}          # real Moss number (from 03/scaffold)
```

## 5. Sequence / flow
```
page load → widget.js (data-clutch-key) → /connection-details (key→token, 06) → LiveKit room
  Type:  user text ─data ch─▶ Agent (04) ─▶ Answer ─data ch─▶ cited card
  Talk:  mic ─▶ Cartesia STT ─▶ Agent (04) ─▶ say() ─▶ Cartesia TTS ─▶ speaker
            └ barge-in: user speaks → tts.stop() → partial → 04
  See:   camera ─▶ sampler (~1–2 fps) ─▶ FrameGate (02: pHash change + rate cap) ─▶ identify ON CHANGE
            → IdentifyResult → 04 → confirm event ("is this the X?") → user taps yes
            → 04 retrieves (03) → grounded steps over voice + on-feed guidance
```

## 6. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| STT mis-hears / no audio | push-to-talk button; fall back to Type (data channel) in same room |
| TTS provider down | swap `tts_provider` (06) to an alternate LiveKit TTS plugin; render answer as text card meanwhile |
| Turn detection too eager/lazy | tune endpoint silence threshold; push-to-talk override |
| Camera/vision unavailable | hide viewfinder; degrade See→Talk; cards still render from voice retrieval |
| Data-channel hiccup | UI holds last-known state; voice continues; reliable delivery for answer/confirm |
| Latency number missing | hide the badge rather than show a fake number (credibility) |
| Bad/expired company key | refuse connect; no room created (scoping enforced upstream, 06) |

## 7. Sponsor mapping
- **LiveKit** — room per session, data channel (Type + UI events), audio (Talk), audio+video (See),
  turn detection + barge-in, the embeddable widget itself. Built on the `livekit-moss-vercel` scaffold.
- **Cartesia** — the **default** STT + TTS for Talk/See, as **LiveKit plugins** (low-latency; not
  gateway-routed). Sits behind the `STTProvider`/`TTSProvider` swap point (00 §7) — replaceable by any
  LiveKit-supported speech provider via config, no caller changes.
- **MiniMax** (reasoning brain) + **Qwen** (vision) calls route through **TrueFoundry** (06).

## 8. Reuse + Open questions
**Reuse:** `moss-research/.../livekit-moss-vercel` — `livekit-voice-agent/livekit-moss-agent/agent.py`
(STT→Agent→TTS pipeline, `publish_data` over data channel, real `time_taken_ms`) and `agent-react`
(chat transcript, `moss-match-card`, `connection-details` route, control bar) as the widget base; add
camera viewfinder, voice indicator, confirm prompt, cited-answer cards. New: `src/speech/{stt,tts}.py`
define the `STTProvider`/`TTSProvider` swap point (currently placeholders) with **Cartesia as the first
impl** + a mock — so the voice vendor is plug-and-play (00 §7). Answer cards show **citations** (the
grounding proof) — no trust chip.
**Open questions:**
- Widget bundle delivery — Clutch-hosted JS dropped into an iframe vs. inline mount (iframe = cleaner isolation).
- Where the company key becomes a LiveKit token — public widget key vs. signed token (06, §8).
- Speech-to-speech (lower latency) vs. discrete STT→LLM→TTS (easier to instrument) — default discrete Tier 0.
- Mobile-web (phone camera, authentic for "See") vs. desktop demo — decide on demo staging.

Confirmation: wrote `docs/clutch/hld/05-realtime-voice-widget.md` (Realtime, Voice & Widget HLD).
