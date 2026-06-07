# Section D — Fardin · Inference Layer (Retrieval, Grounding & Voice Serving)

**Engineer:** Fardin — ML Inference Engineer (serving TTS / STT models + LLMs; inference optimization)
**Owns HLDs:** [03 — Retrieval & Grounding](../03-retrieval-grounding.md) · [05 — Realtime, Voice & Widget](../05-realtime-voice-widget.md) (**server side**)
**Owns directories:** `src/retrieval/` · `src/speech/` (`stt.py`, `tts.py`) · `src/realtime/` (LiveKit agent loop, frame sampler, event publisher) — *publishes* the Spine `src/events.py` schema
**Seams you own:** **S4** (Agent⇄Retrieval) · **S2** (Realtime⇄Agent) · **S1** (Browser⇄Realtime, server half) · **S8** (Realtime→Perception) — [10 §4](10-work-division-and-fusing.md)

> **Independence ([10 §5](10-work-division-and-fusing.md)).** `src/retrieval/`, `src/speech/`,
> `src/realtime/` import only `src/contracts.py` / `src/events.py` — never a sibling subfolder. **You
> expose:** `retrieve_for` / `compose`, `get_stt` / `get_tts`, `run_session` (the voice loop). **You
> receive (passed in):** the `agent` instance and `identify` (into `run_session`), `gateway_client`.
> **Build against:** `mock_agent`, `canned_identify`, `mock_gateway` (retrieval runs on the real
> `clutch-demo` index now). **Connect later:** `src/app.py` passes Tonmoy's real `agent` + `identify`
> in; Arman's widget subscribes to the events you publish.

---

## 1. Your mission
You serve **the read path and the real-time speech path** — the two latency-critical surfaces. **(03)**
the company-scoped Moss `retrieve()` in sub-10 ms + the **grounded cited `compose()`** that *is* the
accuracy guarantee. **(05-server)** the LiveKit voice pipeline — STT → Agent → TTS — with barge-in,
the **`STTProvider`/`TTSProvider` swap adapters**, frame sampling for See, and publishing the typed
data-channel events the widget renders. You serve the inference; Tonmoy decides *what* to say, Arman
decides *how it looks*.

## 2. Scope
**In — Retrieval & Grounding (03):**
- `load_index("clutch-<company>")` (load-once, warm, <10 ms reads); `retrieve()` hybrid
  semantic+keyword (`alpha≈0.7`, `top_k≈5`) with `product_id` metadata filter; `retrieve_for(query)`.
- **Grounded `compose()`** — `Answer.text` built only from retrieved chunks, each claim cited
  `{doc, section, score}`; empty / all-below-`min_score≈0.35` → refuse ("not in docs") / flag escalate.
- The compose LLM is reached **only** via gateway `reason.compose` (S5) — model-portable.
- Local-cosine fallback behind the same `retrieve()` interface (03 §6).

**In — Voice & Realtime (05-server):**
- The LiveKit Agents pipeline (STT → Agent → TTS) where the "LLM" step is **Tonmoy's Agent**, not a
  raw model (mirrors `livekit-moss-agent` scaffold).
- **`src/speech/{stt,tts}.py`** — the `STTProvider`/`TTSProvider` protocols (the voice swap point,
  00 §7), each a thin wrapper over a LiveKit plugin; **Cartesia default** + a deterministic mock;
  `get_stt(cfg)`/`get_tts(cfg)` pick by config. VAD/turn-detection biased to fast endpointing;
  **barge-in mandatory** (`tts.stop()` on user speech). This is your STT/TTS serving-optimization edge.
- **Frame sampling** (~1–2 fps) off the video track → `frames: list[bytes]` handed to Perception (S8).
- **Event publishing** — `realtime/` *produces* the typed data-channel events defined by the Spine
  `src/events.py` schema (`answer`/`confirm`/`voice_state`/`latency`, with the **real** Moss
  `time_taken_ms`). The schema is shared (Spine); the publisher is yours.

**Out:** dialogue/state decisions (Tonmoy 04 — you call its `on_*`); vision inference (Tonmoy 02 — you
feed it frames); the widget UI (Arman 05-client); token minting/tenancy (Arham 06); index *building*
(Arham 01 — you *read* the index it produces).

## 3. What you build against (mock-first, day 1)
- **Retrieval on real Moss** immediately — Arham's `clutch-demo` index is your dev target; `compose`'s
  LLM uses Arham's **mock route** until the real gateway lands. Local-cosine fallback needs no network.
- **Voice loop against a mock Agent** — a stub returning canned `Answer`s lets you build the full
  STT→Agent→TTS loop + barge-in + event publishing before Tonmoy's FSM is wired.
- **Mock STT/TTS** (no keys) for CI; Cartesia plugins flipped in via config at integration.

## 4. The single paths you connect through
- **S4 (Agent⇄Retrieval):** you expose `retrieve_for(RetrievalQuery) -> Chunk[]` and
  `compose(query, chunks) -> Answer`; Tonmoy's Agent calls them. This is the *only* way the brain
  reaches Moss.
- **S2 (Realtime⇄Agent):** your voice loop calls `agent.on_user_turn(text)` / `agent.on_identify(...)`
  and renders the returned `Answer` to TTS + an `answer` event. You hold the Agent instance; you never
  reach into its state.
- **S1 (Browser⇄Realtime, server half):** subscribe to the widget's audio/video tracks + intents;
  publish the four typed events. The **schema is Spine** (`events.py` ↔ Arman's `events.ts`) — changes
  are all-hands PRs (10 §3).
- **S8 (Realtime→Perception):** hand sampled `frames` to `identify()`; consume the `IdentifyResult`
  via the Agent's `on_identify` (you route it, the Agent interprets it).
- **S5 (Gateway):** `reason.compose` via `route` + `gateway_client` (Arham) — no LLM SDK in your code.
  STT/TTS are the **exception**: LiveKit plugins, *not* gateway-routed — swapped via your adapters.
- **S6 (Tenancy):** scope every `retrieve` by the `company_id` on the session; `load_index` per tenant.

## 5. Definition of done
- [ ] `retrieve()` returns product-filtered, company-scoped `Chunk[]` from `clutch-<company>` in
      <10 ms after `load_index`; filter verified to bind on the locally-loaded index.
- [ ] `compose()` emits an `Answer` whose every sentence is backed by a citation; empty/low-score →
      refusal flag, **never** invents (03 §3).
- [ ] Local-cosine fallback returns results through the identical `retrieve()` interface when Moss is
      unreachable.
- [ ] LiveKit voice loop runs STT→Agent→TTS end-to-end on a mock Agent, then on Tonmoy's real Agent.
- [ ] **Barge-in works:** user speech stops TTS immediately; partial transcript flows to the Agent.
- [ ] `STTProvider`/`TTSProvider` swap by config alone (Cartesia ↔ mock ↔ alternate) — no pipeline,
      Agent, or widget change (00 §7).
- [ ] Frame sampler delivers 1–2 fps JPEG frames to `identify()`; See path produces an `IdentifyResult`.
- [ ] Data-channel events emit on the Spine schema with a **real** `time_taken_ms` (hidden if missing).

## 6. Failure modes you own (03 §6, 05 §6)
Moss unreachable → local-cosine fallback. Filter ignored (warning) → re-`load_index`. Empty/low-score
→ widen (lower `alpha`, drop `product_id`); still empty → return `[]`, let Agent refuse. STT mishears →
push-to-talk/Type in same room. TTS down → swap `tts_provider`, render text card meanwhile. Turn
detection eager/lazy → tune silence threshold + push-to-talk override.

## 7. Handoffs
- **To Tonmoy:** the `retrieve_for`/`compose` callables injected into the Agent (S4); you call its
  `on_*` (S2) and feed it frames (S8).
- **To Arman:** the authoritative `events.py` schema + the live data channel + track subscription (S1).
- **From Arham:** `clutch-demo` Moss index + `route`/`gateway_client` for `reason.compose` (S5);
  `company_id`/`index_name` scoping (S6); LiveKit creds via `Config`.

## 8. Stretch / open questions
`min_score` floor + `alpha` per modality; tighter `top_k` for spoken (Talk) answers (03 §8).
Speech-to-speech (lower latency) vs discrete STT→LLM→TTS (default discrete Tier 0); where the company
key becomes a LiveKit token (05 §8, with Arham).
