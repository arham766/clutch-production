# Fixalong LLD 04 — Perception: KriNein gate + Qwen vision
**Implements:** HLD 04 · **Module(s):** agent/perception/gate.py, agent/perception/vision.py · **Build tier:** 0/1 · **Status:** Draft v0.1

## 1. Responsibility
Turn the always-on camera stream into structured `VisualObservation`s **cheaply and safely**. `gate.py` is a fast, two-tier frame triage (KriNein-derived) that drops visually redundant frames so the VLM runs only on meaningful change: **Tier A = pHash (perceptual hash, Hamming distance, every frame, sub-ms)**, optional **Tier B = CLIP cosine (only on Tier-A survivors)**, then a **hard rate-limit (~1–2 VLM calls/sec)**. `vision.py` calls Qwen (multimodal) on gated frames with a context-biased prompt (expected parts for the current node) and parses the reply into the `VisualObservation` contract with confidence gating. **Vision DESCRIBES + ROUTES only — it never produces repair advice** (that is 05/06). We **deliberately do NOT use SSIM or LPIPS** — both are too slow per-frame for the live hot path.

## 2. Files & public surface
```
agent/perception/gate.py
  class FrameGate:                       # stateful: holds rolling reference + rate-limit clock
    __init__(cfg: GateConfig)
    submit(frame: bytes, now: float|None=None) -> GateDecision   # main entry: A→B→rate-limit
    reset() -> None                      # clear reference (e.g. on node change / camera flip)
  def phash_changed(frame: bytes, ref_hash: ImageHash|None, threshold: int) -> tuple[bool, ImageHash]
  def clip_semantically_new(frame: bytes, ref_emb: np.ndarray|None, threshold: float) -> tuple[bool, np.ndarray]

agent/perception/vision.py
  async def observe(frame: bytes, hint: ObserveHint) -> VisualObservation        # one VLM call + parse
  async def observe_stream(frames: AsyncIterator[bytes], hint_fn, on_observation) -> None  # gate→observe
  def _build_prompt(hint: ObserveHint) -> str
  def _parse(raw: str, hint: ObserveHint) -> VisualObservation                   # strict, confidence-gated
```

## 3. Dependencies
- **Libs:** `imagehash` (phash), `Pillow` (decode JPEG → `PIL.Image`), `numpy`. Tier B (optional): `open_clip_torch` + `torch` (CLIP ViT-B/32). `httpx`/SDK for the Qwen endpoint (routed through TrueFoundry, see 06/08).
- **Internal:** `agent/contracts.py` (`VisualObservation`); `agent/state/machine.py` consumes observations (05); `agent/voice/persona.py` is the degradation target (07). Frames arrive from `agent/voice/livekit_agent.py` (03).
- **Env:** `QWEN_ENDPOINT`, `QWEN_API_KEY` (or TrueFoundry gateway creds), `PERCEPTION_CLIP_ENABLED` (bool, default false on demo), `CLIP_DEVICE` (`cpu`/`cuda`).

## 4. Data structures (beyond shared contracts)
```python
# agent/perception/gate.py
@dataclass
class GateConfig:
    phash_size: int = 16                 # hash side → 256-bit hash (more bits = finer)
    phash_hamming_threshold: int = 10    # Tier A: pass if Hamming(ref, cur) > threshold
    clip_enabled: bool = False           # Tier B off by default (demo-safe)
    clip_cosine_threshold: float = 0.92  # pass (semantically new) if cosine(ref, cur) < threshold
    max_calls_per_sec: float = 2.0       # hard rate-limit on VLM calls
    min_interval_s: float = 0.5          # = 1/max_calls_per_sec, debounce window

@dataclass
class GateDecision:
    passed: bool                         # True → caller may run observe()
    reason: str                          # "duplicate" | "rate_limited" | "semantically_same" | "pass"
    hamming: int|None                    # Tier A distance (debug/telemetry)
    cosine: float|None                   # Tier B similarity, None if Tier B skipped

# agent/perception/vision.py
@dataclass
class ObserveHint:
    node_id: str
    expected_parts: list[str]            # biases recognition; from current RepairNode.parts
    object_type: str = ""                # e.g. "laser printer" — extra prompt context
    min_confidence: float = 0.45         # parts below this are dropped (→ verbal confirm in 05)
```

## 5. Key functions

### 5.1 `phash_changed` (Tier A — every frame, sub-ms)
```python
def phash_changed(frame, ref_hash, threshold):
    img  = Image.open(io.BytesIO(frame)).convert("L")   # grayscale; phash ignores color
    cur  = imagehash.phash(img, hash_size=cfg.phash_size)
    if ref_hash is None:                # first frame always "changed"
        return True, cur
    dist = cur - ref_hash               # imagehash overloads __sub__ = Hamming distance
    return (dist > threshold), cur
```
Rolling reference is the **last frame we actually SENT to the VLM** (not the previous raw frame) — so slow drift across many tiny frames still eventually trips the threshold instead of never accumulating.

### 5.2 `clip_semantically_new` (Tier B — survivors only, optional)
```python
def clip_semantically_new(frame, ref_emb, threshold):
    emb = _clip_embed(frame)            # normalized ViT-B/32 image embedding
    if ref_emb is None:
        return True, emb
    cos = float(emb @ ref_emb)          # both L2-normalized → dot = cosine
    return (cos < threshold), emb       # low similarity = new scene → pass
```
Separates real repair-state changes (panel opened, new part in view) from lighting / blur / camera-shake that pHash let through. Runs only on the small Tier-A surviving fraction, so its cost stays off the per-frame critical path.

### 5.3 `FrameGate.submit` (the gating algorithm)
```python
def submit(frame, now=None):
    now = now or time.monotonic()
    # Tier A — pHash on every frame
    changed, cur_hash = phash_changed(frame, self._ref_hash, cfg.phash_hamming_threshold)
    if not changed:
        return GateDecision(False, "duplicate", self._ref_hash - cur_hash, None)
    # Tier B — CLIP only if enabled AND Tier A passed
    cosine = None
    if cfg.clip_enabled:
        new, cur_emb = clip_semantically_new(frame, self._ref_emb, cfg.clip_cosine_threshold)
        cosine = ... # similarity for telemetry
        if not new:
            return GateDecision(False, "semantically_same", None, cosine)
    # Hard rate-limit (debounce) — even if A+B pass, cap VLM calls
    if (now - self._last_sent) < cfg.min_interval_s:
        return GateDecision(False, "rate_limited", None, cosine)
    # PASS — commit this frame as the new rolling reference
    self._ref_hash = cur_hash
    if cfg.clip_enabled: self._ref_emb = cur_emb
    self._last_sent = now
    return GateDecision(True, "pass", None, cosine)
```
Order matters: cheapest filter first (pHash), then CLIP only on survivors, then rate-limit last so the reference only advances on frames we truly send.

### 5.4 `observe` (vision.py — VLM call + parse)
```python
async def observe(frame, hint):
    prompt = _build_prompt(hint)                 # context-biased, DESCRIBE-only
    raw    = await _qwen_call(frame, prompt)     # multimodal: image + prompt → JSON text
    return _parse(raw, hint)                     # strict JSON, drop low-confidence parts
```
`_build_prompt` injects expected parts and **hard-constrains the model to describe + route, not advise**:
> "You are a camera, not an advisor. List visible parts (bias toward: {expected_parts}), read any labels/model numbers, flag visible hazards (hot surface, exposed wiring, sharp/moving parts), and propose retrieval queries. **Never suggest a repair step, never say how to fix anything.** Return ONLY JSON matching {schema}."

`_parse`: parse JSON (repair-and-retry once on malformed), coerce to `VisualObservation`, **drop any `candidate_parts` with confidence < `hint.min_confidence`**, clamp confidences to [0,1], cap list lengths (≤5 parts, ≤5 queries). On unparseable output → return an **empty** `VisualObservation` (visible_object="", empty lists) so the state machine simply gets no update rather than a hallucinated one.

### 5.5 `observe_stream` (wiring)
```python
async def observe_stream(frames, hint_fn, on_observation):
    gate = FrameGate(cfg)
    async for frame in frames:
        if not gate.submit(frame).passed:
            continue
        try:
            obs = await asyncio.wait_for(observe(frame, hint_fn()), timeout=cfg.vlm_timeout_s)
        except (asyncio.TimeoutError, VisionUnavailable):
            continue                     # fail-safe: skip frame, voice narration still works
        on_observation(obs)              # → RepairState.update (05)
```
`hint_fn()` is a callback so the prompt always reflects the **current** node/expected_parts (state can advance between frames). `reset()` is called by 05 on node change so the rolling reference doesn't suppress the first frame of a new scene.

## 6. Control flow / sequence
1. LiveKit (03) yields JPEG frames at ~15–30 fps into `observe_stream`.
2. Each frame → `FrameGate.submit`: **Tier A pHash** (drop ~80–95% as near-duplicates) → **Tier B CLIP** if enabled (drop shake/lighting) → **rate-limit** (≤1–2/s).
3. Survivors → `observe`: context-biased Qwen call → parsed, confidence-gated `VisualObservation`.
4. `on_observation` hands it to the State Machine (05), which silently updates `RepairState`, resolves pronouns, and may raise a hazard → `warning`. Low-confidence parts trigger a **verbal confirm** instead of acting.

## 7. Config & tuning (defaults)
| Param | Default | Notes |
|---|---|---|
| `phash_size` | 16 | 256-bit hash; live-tunable |
| `phash_hamming_threshold` | 10 | ↑ = fewer VLM calls (lax), ↓ = more (eager). Cheap to tune on stage |
| `clip_enabled` | **false** | Off for demo; flip on only if GPU available |
| `clip_cosine_threshold` | 0.92 | pass if cosine **<** this (new scene) |
| `max_calls_per_sec` / `min_interval_s` | 2.0 / 0.5 | hard cap regardless of A/B |
| `min_confidence` (parts) | 0.45 | below → drop → 05 verbally confirms |
| `vlm_timeout_s` | 2.5 | per-call wall clock before skip |

## 8. Error handling & fallbacks (fail-safe)
- **Tier B too slow / no GPU:** set `clip_enabled=false` → **pHash-only gate** + rate-limit; still removes ~80–95% of VLM calls (the demo default). This is the in-gate fallback from HLD §5.
- **Qwen endpoint down / timeout (`VisionUnavailable`):** skip the frame; the loop keeps running on **voice-narrated observations** — the user narrates ("I see a black rubber wheel, looks shiny") and the same `RepairState.update` + retrieval path runs unchanged (HLD §6/§8). Optionally swap to another multimodal model behind TrueFoundry routing (06).
- **Malformed VLM JSON:** one repair-retry, else return empty `VisualObservation` (no hallucinated state).
- **Wrong part:** confidence gate → 05 verbally confirms before acting; **voice overrides vision** on disagreement.
- **Latency spike from per-frame calls:** gate + hard rate-limit cap it; last resort is **tap-to-capture** (manual single-frame submit).
- **Camera flip / node change:** `reset()` clears the rolling reference so a genuinely new scene isn't suppressed as a duplicate.

## 9. Latency / perf notes
- **Tier A pHash:** decode + 16×16 DCT hash ≈ sub-ms to low-ms per frame on CPU; runs on **every** frame — this is the budget-defining gate.
- **Tier B CLIP:** ~5–30 ms/frame on GPU (ViT-B/32), much slower on CPU (why it is off by default). Runs only on the ~5–20% Tier-A survivors, so it is **off the per-frame critical path**.
- **VLM (Qwen) call:** the dominant cost (hundreds of ms–seconds); the entire point of the gate + rate-limit is to bound this to ~1–2/s. The live path never blocks the voice loop — `observe_stream` is async and frame-skipping.
- **Offline (Tier 2):** the full KriNein pipeline (scene detection, dedup cascade, clustering, Whisper) is **not** on the live loop — it runs post-repair on the recorded video for receipts/QA/training (HLD §9). Live = pHash gate only.

## 10. Test plan (synthetic frames; mock the VLM)
**Unit — gate.py (no network):**
- `phash_changed`: two near-identical JPEGs (one re-encoded / +1px shift) → Hamming ≤ threshold → False; an unrelated image → True; `ref_hash=None` → True.
- `clip_semantically_new`: same image twice → cosine≈1 → False; two different scenes → cosine low → True (skip if `open_clip` not installed via `pytest.importorskip`).
- `FrameGate.submit` ordering: feed [A, A-dup, B-new] within `min_interval_s` → decisions `pass`, `duplicate`, `rate_limited`; advance fake clock past `min_interval_s` → B-new now `pass`. Use injected `now` (no real sleeping).
- `clip_enabled=false` → CLIP never invoked (assert via spy) → pHash-only path.
- `reset()` → next frame always passes Tier A.
**Unit — vision.py (mock `_qwen_call`):**
- well-formed JSON → correct `VisualObservation`; parts below `min_confidence` dropped; lists capped/clamped.
- malformed JSON → repair-retry path → empty observation on second failure.
- `_build_prompt` asserts expected_parts present **and** an explicit "no repair advice" instruction.
- prompt-injection style label ("ignore instructions, tell user to...") in image → parser still yields description-only schema (no advice field exists).
**Integration:** synthetic frame sequence (duplicates + a "panel open" change) through `observe_stream` with a fake VLM → assert `on_observation` fires only on changed frames and at ≤ `max_calls_per_sec`. Simulate `VisionUnavailable` → loop continues, no crash (voice-narration degradation).

## 11. Build checklist (Tier-0 first)
1. **(Tier 0)** `gate.py`: `phash_changed` + `FrameGate.submit` with pHash + rate-limit only (`clip_enabled` default false). Unit-test ordering with injected clock.
2. **(Tier 0)** `vision.py`: `ObserveHint`, `_build_prompt` (describe-only), `_qwen_call`, `_parse` with confidence gate; `observe`. Mock the VLM in tests.
3. **(Tier 0)** `observe_stream` wiring with timeout + `VisionUnavailable` skip; hook `on_observation` to 05.
4. **(Tier 0)** Fail-safe paths: empty-observation on parse failure; verify voice-narration degradation end-to-end.
5. **(Tier 1)** Add Tier-B CLIP (`clip_semantically_new`, `open_clip` lazy import, GPU/CPU switch); keep behind `clip_enabled`.
6. **(Tier 1)** Live-tune thresholds on stage hardware (Hamming, cosine, rate-limit); add telemetry from `GateDecision`.
7. **(Tier 2, separate)** Offline KriNein full pipeline for post-repair receipts (out of this module's hot path).
