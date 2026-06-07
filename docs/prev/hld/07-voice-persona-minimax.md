# Fixalong HLD 07 — Voice Persona (MiniMax + Nova Sonic fallback)

**Status:** Draft v0.1 · **Build tier:** 1 (MiniMax) / 2 (Nova Sonic) · **Sponsor:** MiniMax (+ AWS)
**Depends on:** State Machine (05) for text + mode · **Consumed by:** LiveKit (03) → speaker

---

## 1. Purpose

The agent should not sound like a chatbot reading a manual. It should sound like a calm
expert standing beside you — and it should **change its tone with the stakes**. MiniMax
provides expressive, low-latency speech; we drive it with **urgency modes**.

> "Pause. Don't touch that part yet." · "Good — now look just below the roller." ·
> "That's probably the jam sensor arm."

## 2. Scope
**In:** text → speech, persona/urgency modes, latency-appropriate synthesis, fallback TTS.
**Out:** *what* to say (05), *whether it's safe* (06).

## 3. Persona / urgency modes

The State Machine (05) tags each utterance with a `mode` derived from `phase`:

| Mode | When | Voice behavior |
|---|---|---|
| `normal` | guiding | calm, short, step-by-step |
| `safety` | warning / hazard | immediate, firm, clipped — no extra words |
| `diagnosis` | explaining a cause | slower, explanatory |
| `confirm` | confirming a step | reassuring, brief |
| `expert` | high-stakes / enterprise | professional technician tone |

Examples:
- normal → "Open the rear panel and look for the black rubber roller."
- safety → "Stop. Unplug it first."
- diagnosis → "A shiny roller usually means it has lost grip — that can trigger repeated jam
  errors even when the path is clear."

Implementation: map mode → MiniMax voice params (style/speed/emphasis/voice id). Keep safety
mode **short and loud-styled**; never bury a stop instruction in a long sentence.

## 4. Latency
TTS is on the hot path after retrieval+safety. Prefer streaming synthesis (speak as tokens
arrive) so first-audio latency is low. Cap utterance length in `normal`/`safety` modes —
shorter = faster to first word and easier to interrupt (03).

## 5. Interfaces
```python
say(text: str, mode: Mode) -> AudioStream          # primary: MiniMax
# mode → voice params; returns a stream LiveKit plays into the room
```
LiveKit (03) calls `say`; barge-in (03) calls `stop()` on the active stream.

## 6. Fallback — AWS Nova Sonic (Tier 2)
Nova Sonic (via the LiveKit AWS plugin) is a speech-to-speech path with native turn detection
— usable as (a) a TTS fallback if MiniMax is unavailable, or (b) an alternative end-to-end
realtime backend. Trade-off: speech-to-speech is lower latency but harder to insert the
output guardrail (06) into; default to MiniMax TTS with the discrete pipeline, keep Nova
Sonic as fallback.

## 7. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| MiniMax unavailable / slow | LiveKit default TTS, or Nova Sonic |
| Safety utterance too long/soft | enforce a max-length + safety-style template for `mode=safety` |
| Voice clipping on interrupt | ensure `stop()` flushes the synth buffer immediately (03 barge-in) |

## 8. Open questions
- Single voice with style params vs distinct voice ids per mode.
- Whether `safety` mode should also trigger a UI flash/haptic (09) for redundancy.
