# Fixalong HLD 03 — Voice & Realtime (LiveKit)

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** LiveKit
**Depends on:** State Machine (05), TTS (07) · **Consumed by:** the user

---

## 1. Purpose

Run the **hands-free repair call**. Repair is hands-busy — the user is holding tools and
opening panels, not typing. LiveKit provides the realtime room: the **continuous camera
stream** (the primary input → Vision 04), mic streaming, STT, turn detection, and
**interruptions (barge-in)**.

Voice here is the **control surface, not the input of record** — the camera carries the state
(HLD 00 §2). The agent is **mostly silent**, speaking only when the speak gate (05) fires.

> "LiveKit gives us the repair call. The camera gives it the state. Moss gives it memory.
> Unsiloed gives it the repair knowledge."

## 2. Scope
**In:** audio room, STT, turn-taking, barge-in handling, agent speech, the **continuous video
track** (primary input → HLD 04), session lifecycle. **Out:** what to *say* (05), retrieval
(02), TTS voice (07).

## 3. Pipeline

Use the LiveKit Agents SDK STT → LLM → TTS pipeline, but the "LLM" step is our orchestrator
(05), not a raw model call.

```
mic ─▶ LiveKit room ─▶ STT ─▶ (partial + final transcripts)
                                   │
                                   ▼
                         Repair State Machine (05)
                                   │ proposed utterance + mode
                                   ▼
                         TTS (MiniMax/Nova Sonic, 07) ─▶ speaker
   camera (primary, always on) ─▶ video track ─▶ frame sampler ─▶ Vision (04)
```

## 4. The realtime loop

```python
# PRIMARY: continuous camera → vision → state (usually silent)
on_video_frame(frame):
    obs = vision.observe_gated(frame)                    # 04 (KriNein gate → Qwen)
    if obs:
        result = state_machine.on_visual_observation(obs)  # 05 (updates state, prefetches)
        if result: tts.say(*result)                        # only if the speak gate fired

# CONTROL: voice intent / observation / danger
on_user_speech_partial(transcript):
    moss.prefetch(build_queries(transcript, state))      # 02 — speculative, ~free
on_user_speech_final(transcript):
    result = state_machine.on_final_transcript(transcript)  # 05 → speak gate → 02 + 06
    if result: tts.say(*result)                          # may be None (stay silent)

on_user_interrupt():            # barge-in — critical for safety
    tts.stop()
    state_machine.note_interrupt()
```

Note both paths route through the State Machine's **speak gate** (05) — most calls return
nothing and the agent stays quiet, watching.

## 5. Barge-in & safety (the most important realtime behavior)

The agent **must** be interruptible, and interruptions can be safety-critical:

```
agent: "Open the rear panel and—"
user (interrupts): "Wait, I smell burning."
  → LiveKit stops agent speech immediately
  → StateMachine: hazard observed → phase=warning
  → Moss retrieves stop-condition (risk_level=high)
  → agent (safety mode): "Stop. Unplug it now. Don't continue the paper-path procedure."
```

Turn-detection tuning: bias toward **fast interruption** over letting the agent finish —
in repair, a late stop is dangerous. Endpointing should be snappy so the user isn't talking
over a monologue.

## 6. Session lifecycle
- `start_session()` → create room, load Moss session index (02), init RepairState (05).
- `end_session()` → generate the repair receipt (09), tear down room, persist transcript.

## 7. Interfaces
```python
start_session(object_hint) -> Session
on_partial(cb); on_final(cb); on_interrupt(cb)
say(text, mode)          # delegates to TTS (07)
attach_video(track)      # continuous frames → Vision (04, primary input)
```

## 8. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| MiniMax TTS down | LiveKit default TTS or AWS Nova Sonic (07) |
| STT mis-hears terse part words | State Machine confirms ("the shiny black wheel — the pickup roller?") before acting |
| Turn detection too eager/lazy | tune endpoint silence threshold; push-to-talk fallback button (09) |
| Realtime model path flaky | swap to STT→LLM→TTS discrete pipeline (LiveKit supports both) |

## 9. Open questions
- Realtime speech-to-speech (Nova Sonic via LiveKit AWS plugin) vs discrete STT→LLM→TTS.
  Discrete is easier to instrument for the safety guardrail (06); speech-to-speech is lower
  latency. Default discrete for Tier 0, evaluate Nova Sonic Tier 2.
- Where the orchestrator runs (LiveKit agent process vs separate service).
