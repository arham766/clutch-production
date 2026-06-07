# Workstream 3 — Voice (realtime + TTS)

**Owner:** ____ · **Skills:** realtime / voice · **Mission:** the hands-free voice room — mic +
camera capture, STT, turn-taking, **barge-in**, and an expressive repair voice that changes tone
with the stakes.

## You own
- **LLDs:** [03 voice/LiveKit](../lld/03-voice-livekit.md), [07 persona](../lld/07-persona.md).
- **Modules:** `agent/voice/livekit_agent.py` (LiveKit Agents SDK: room, STT, turn detection,
  the callbacks, data-channel publish), `agent/voice/persona.py` (MiniMax TTS, mode→voice params,
  streaming, `stop()`).

## Interfaces you PRODUCE (Part 2 + Part 4 depend on these)
```python
say(text, mode) -> AudioStream        # MiniMax; mode→voice params; streaming; stop() for barge-in
persona_mode(phase) -> PersonaMode    # warning→safety, diagnosing→diagnosis, guiding→normal, …
publish(payload)                      # agent→UI over the LiveKit data channel
# LiveKit room wires Part 2's state-machine entry points to the callbacks:
on_video_frame(frame)      -> Part1.observe -> Part2.on_visual_observation -> maybe say()
on_final_transcript(text)  -> Part2.on_final_transcript -> maybe say()
on_user_interrupt()        -> stop() + Part2.note_interrupt        # barge-in (safety-critical)
```

## Interfaces you CONSUME (stub on hour 0)
- From **Part 2:** `on_visual_observation(obs)` / `on_final_transcript(text)` → `Optional[(text, mode)]`;
  `persona_mode(phase)`. (You call these from LiveKit callbacks and speak the result.)
- From **Part 1:** `observe(frame)` (you feed it frames — agree with Part 2 who calls it).

## External deps / keys
LiveKit (`LIVEKIT_*`), MiniMax (`MINIMAX_*`), optional AWS Nova Sonic (Tier 2 fallback).
Start from the `livekit-moss-vercel` scaffold's `agent.py` for SDK wiring patterns.

## Build order
- **Tier 0:** LiveKit room (mic + **camera on**) + STT + turn detection; `say(text, mode)`
  (any TTS) + **barge-in** (`stop()` on interrupt); wire the two callbacks to Part 2; publish
  events to the data channel.
- **Tier 1:** MiniMax persona urgency modes (mode→params table; short/firm `safety` mode);
  tune turn-detection for fast interruption.
- **Tier 2:** AWS Nova Sonic fallback; LiveKit default-TTS fallback if MiniMax is down.

## Correctness notes
- `PersonaMode` ∈ `normal/safety/diagnosis/confirm/expert` (must match `contracts.py` exactly).
- Barge-in must stop speech **immediately** (flush the synth buffer) — late stops are dangerous.
- Keep `safety`-mode utterances short.

## Parallel-work stub
`say(text, mode)` → log + play any TTS (or print) until MiniMax is wired; drive callbacks with
mock transcripts/frames so you can build without the rest of the agent.

## Definition of done
A user can hold a hands-free conversation: speak → agent answers in the right tone → interrupt
stops it instantly; frames flow to Part 1's `observe`; events publish to the UI.

## Your demo moment
Everything the audience *hears*: the calm-then-firm voice on the **safety pivot**, and the snappy
**barge-in** when the user interrupts.
