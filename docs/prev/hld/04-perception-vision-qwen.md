# Fixalong HLD 04 — Perception / Vision (Qwen + KriNein)

**Status:** Draft v0.1 · **Build tier:** 0/1 (core — always-on stream) · **Sponsor:** Qwen
**Depends on:** camera frames (LiveKit video, 03) · **Consumed by:** State Machine (05)
**Reuses:** **KriNein** (our own video-analysis library) for fast frame triage + structured extraction

---

## 1. Purpose

The camera is the **primary, continuous context stream** — not a side input that confirms
what the user said. It is **always on**, and it **silently updates `RepairState`** as the
repair unfolds. Its job is to turn what the camera sees into **structured observations +
retrieval queries** — never into repair advice.

> **The camera is the state.** Qwen sees the world → Moss retrieves the right context → the
> agent's voice (only when useful) explains the next step.

Letting a vision model *answer* repair questions invites hallucinated, unsafe advice. Letting
it only *describe and route* keeps every spoken instruction grounded in the verified manual —
and lets the system know what the user is looking at **before they say anything**.

## 2. Scope
**In:** identify visible parts, read labels/model numbers, detect coarse repair state and
hazards, emit retrieval queries. **Out:** deciding the next step (05), generating advice
(05/06), speaking (07).

## 3. Inputs & outputs
- **Input:** sampled camera frame(s) (JPEG) + light context (current `node_id`, expected
  parts) to bias recognition.
- **Output:** `VisualObservation` (HLD 00 §5.4) — candidate parts w/ confidence, hazards,
  and `next_retrieval_queries`.

## 4. What Qwen does

Qwen2.5-Omni / Qwen-Omni is multimodal (text+image+audio+video). It runs **only on frames
that survive the KriNein triage gate (§5)** — never every frame. We use the **vision**
capability to:
- identify visible parts (roller, belt, wire, panel, latch, screw, fragment…);
- read labels / model numbers (helps pick the right manual);
- detect coarse repair state: panel open, part exposed, tool in hand, fragment visible,
  cable disconnected;
- flag hazards by appearance: hot surface (fuser), exposed wiring, sharp/moving parts.

We reuse **KriNein's structured-extraction scaffolding** (frame → structured JSON via a VLM)
to emit the `VisualObservation` schema with a repair-domain prompt — so we don't hand-roll
prompt/parse/validate logic.

```jsonc
// frame → VisualObservation
{
  "visible_object": "printer rear paper path",
  "candidate_parts": [ {"name":"pickup roller","confidence":0.78},
                       {"name":"transfer roller","confidence":0.51} ],
  "hazards": [ {"type":"hot_surface","part":"fuser","confidence":0.64} ],
  "next_retrieval_queries": ["pickup roller cleaning","transfer roller warning",
                             "rear paper path diagram"]
}
```

## 5. Frame triage — KriNein-derived (the latency de-risker)

Calling the VLM on every frame is *the* failure mode (too slow, too costly, flaky on stage).
We gate frames with the **fast** parts of KriNein so the VLM runs only on **meaningful
change**. We deliberately **drop SSIM and LPIPS — too slow per-frame for the live hot path**
— and keep a cheap two-tier gate:

```
camera frames (LiveKit, ~15–30 fps)
   │
   ▼  Tier A — pHash gate   (perceptual hash + Hamming distance; sub-ms; runs on EVERY frame)
   │      near-duplicate → drop.   meaningful pixel change → pass  (~5–20% of frames survive)
   ▼  Tier B — CLIP semantic check   (runs ONLY on frames that pass Tier A)
   │      embed + cosine vs last-sent frame.
   │      lighting / blur / camera-shake → drop.   semantically new (new part, panel open) → pass
   ▼  rate limit  (≤ ~1–2 VLM calls/sec, debounced)
   ▼  Qwen / Claude multimodal  →  VisualObservation
```

- **Tier A — pHash (every frame, ~sub-ms).** Kills the 80–95% of visually redundant frames
  (KriNein's headline reduction) for almost no cost. This is the primary gate.
- **Tier B — CLIP (only on Tier-A survivors).** Separates *real* repair-state changes (new
  part in view, panel opened) from camera shake / lighting / motion blur. Because it runs only
  on the small surviving fraction, its cost stays **off the per-frame critical path**.
- **No SSIM / LPIPS.** Both are comparatively slow per-frame; pHash + CLIP make the same "is
  this frame worth a VLM call?" decision at a fraction of the latency.
- **Adaptive sampling + hard rate-limit.** Cap VLM calls to ~1–2/sec even if Tier B passes
  more — bounds cost and keeps the loop responsive.
- **Context-biased prompts:** pass expected parts for the current `node_id` so the VLM is
  scoped ("are any of {pickup roller, transfer roller, fuser} visible?").
- **Confidence gating:** below threshold → State Machine (05) **confirms verbally** ("is that
  the shiny black wheel?") instead of acting.

> **Demo fallback within the gate:** if CLIP is CPU-bound and adds latency on the demo
> machine, run **pHash-only** (Tier A) + the rate-limit. pHash alone already removes almost
> all redundant VLM calls; CLIP is a precision upgrade, not a requirement.

## 6. Fusion with voice (HLD 05)

Vision is the **primary state source**; it updates `RepairState` continuously and silently.
Voice is the **control surface** — it carries *intent* ("how do I clean it?"), *observations*
("it looks shiny"), and *danger* ("I smell burning"), and the State Machine **resolves
pronouns** ("it"/"this") against the current visual state. Rules:
- **Either** source can raise a hazard (→ jump to `warning`).
- **Voice tie-breaks** when speech and vision disagree on a part.
- **Vision-primary, voice-degradable:** if the camera drops, the user narrates and the same
  `RepairState.update` + retrieval path runs unchanged (graceful degradation, §8) — but the
  intended experience is "it already knows what you're looking at."

## 7. Interfaces
```python
# KriNein-derived gate (fast; no SSIM/LPIPS)
phash_changed(frame, ref, threshold) -> bool        # Tier A — every frame, sub-ms
clip_semantically_new(frame, ref, threshold) -> bool # Tier B — only on Tier-A survivors

# Perception
observe(frame: bytes, hint: {node_id, expected_parts}) -> VisualObservation  # VLM + KriNein extraction
observe_stream(frames, on_observation)               # gate A → B → rate-limit → observe()
```

## 8. Failure modes & fallbacks  ⚠️ highest-risk component
| Failure | Fallback |
|---|---|
| Live recognition flaky / slow on stage | **voice-narrated observations** — the winning scene ("I see a black rubber wheel, looks shiny") already works without vision |
| Wrong part identified | confidence gate → verbal confirm before acting; voice overrides vision |
| Latency spikes from per-frame calls | KriNein pHash gate (§5) caps VLM calls; hard rate-limit; drop to tap-to-capture |
| CLIP (Tier B) too slow on demo machine | run **pHash-only** gate — still removes ~80–95% of VLM calls |
| pHash gate too eager / too lax | tune Hamming-distance threshold (cheap, live-tunable); same for CLIP cosine threshold |
| Qwen endpoint unavailable | swap to another multimodal model behind TrueFoundry routing (06), or disable vision |

> **Design rule:** vision is *primary*, but the loop is built so `RepairState` can be updated
> by voice through the same interface — so a vision outage **degrades** to user narration
> rather than failing. The headline scene is the vision-first one (the user doesn't name the
> part); narration is the safety net, not the intended path.

## 9. Offline reuse (enterprise, Tier 2)

The **full** KriNein pipeline (scene detection, dedup cascade, clustering, structured
extraction, Whisper) is built for *complete video files* — not the live hot path — so we
don't put it on the loop. But it's a clean **post-repair** feature: run the **recorded**
session video through KriNein offline to auto-generate the repair receipt (09), QA evidence,
and training data. Live = pHash gate only; offline = full pipeline.

## 10. Open questions
- Hosting Qwen (Alibaba Cloud Model Studio vs self-host) and its realtime latency budget.
- CLIP runtime placement (GPU vs CPU); if CPU-bound, ship pHash-only for the demo.
- Whether to use Qwen-Omni's audio understanding too, or keep STT in LiveKit (03). Default:
  STT in LiveKit, Qwen for vision only — simpler, fewer moving parts.
