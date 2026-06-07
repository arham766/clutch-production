# `src/realtime/` — Realtime & Voice, server side (Section D · Fardin)

The LiveKit voice pipeline (HLD [05](../../docs/clutch/hld/05-realtime-voice-widget.md), server half).
STT → Agent → TTS with barge-in, frame sampling for See, and publishing the typed data-channel events.

- **Exposes:** `run_session(...)` (the voice loop), the frame sampler, the event publisher
- **Receives (passed in):** the `agent` instance, `identify`, `stt`, `tts` (never imports `agent/`
  or `perception/`)
- **Imports only:** `src/contracts.py` + `src/events.py` (the shared event schema it *publishes*)
- **Build-against mock:** `mock_agent`, `canned_identify`
- **Reuses:** the `livekit-moss-vercel` scaffold in `moss-research/`

**Run & API keys:** `python -m scripts.realtime_mini --check` (keyless ⇒ SKIP). For a real run set
`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` + `CARTESIA_API_KEY` (LiveKit Cloud works; or
`docker` for a local server). Full setup:
[LLD README §2.2](../../docs/clutch/lld/README.md#22-realtime--voice-srcrealtime--lld-05-realtime) ·
spec: [LLD 05-realtime](../../docs/clutch/lld/05-realtime.md).

> **Status (2026-06-07):** transport verified live — the worker dispatches, the agent joins the room,
> `session.start` completes, `lk.chat` text reaches the agent, and the data channel is wired. **One
> seam remains for Tonmoy (04/S2):** livekit-agents 1.5.17 requires a non-None `session.llm` (it raises
> `trying to generate reply without an LLM model` at `agent_activity.py:1157` *before* `ClutchAgent.llm_node`
> runs). So turn→reply needs the Agent/brain injected as the session LLM in `app.py`. Once wired,
> `realtime_mini.py --check` runs the full TALK/TYPE/BARGE/SEE/STATE gate **as written** (left unchanged
> for that integration test). See [LLD README §0](../../docs/clutch/lld/README.md#0-build-status--whats-left-live-verified-2026-06-07).

See the brief: [14 — Fardin](../../docs/clutch/hld/engineers/14-fardin-inference-retrieval-voice.md)
and the connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
