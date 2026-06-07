# `src/speech/` — Speech adapters (Section D · Fardin)

The voice swap point (HLD [05](../../docs/clutch/hld/05-realtime-voice-widget.md), 00 §7). Thin
wrappers over LiveKit STT/TTS plugins so the speech vendor is chosen by config — Cartesia is the
default, replaceable with no change to the pipeline, agent, or widget.

- **Files:** `stt.py`, `tts.py`, `providers/` (`base.py`, `cartesia.py`)
- **Exposes:** `get_stt(cfg)`, `get_tts(cfg)` (default = Cartesia LiveKit plugins)
- **Receives (passed in):** `cfg` (`stt_provider` / `tts_provider`)
- **Imports only:** `src/contracts.py` — no sibling subfolder
- **No mock impl:** verified by a **real** Cartesia round-trip — selection works keyless

**Run & API keys:** `python scripts/speech_e2e.py --check` (keyless ⇒ selection passes, round-trip
SKIPs). For a real run set `CARTESIA_API_KEY` (+ optional `CARTESIA_*` model/voice). Full setup:
[LLD README §2.3](../../docs/clutch/lld/README.md#23-speech-layer-srcspeech--lld-05-speech) ·
spec: [LLD 05-speech](../../docs/clutch/lld/05-speech-layer.md).

See the brief: [14 — Fardin](../../docs/clutch/hld/engineers/14-fardin-inference-retrieval-voice.md)
and the connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
