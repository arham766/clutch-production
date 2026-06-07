# Clutch HLD 02 — Perception

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** Qwen (vision) via TrueFoundry AI Gateway

> Read [00 — Overview](00-overview.md) first. Shared contracts (`CatalogEntry`, `ProblemSignals`,
> `IdentifyResult`, `Candidate`) are defined there and in `src/contracts.py` — this HLD references
> them, never redefines them.

## 1. Purpose
When the customer hits **See**, Perception turns a burst of live camera frames into a structured
**product + problem**: `identify(frames, catalog) -> IdentifyResult`. Because the three locked
constraints — **multi-product per company**, a **generic support page** (no product context), and
**fresh "See" context** — strip away every shortcut, vision must do *real* work: closed-set
classification of the device against the company catalog **plus** spotting what's visibly wrong. The
output **scopes** Moss retrieval (`product_id`) and **drives** it (`problem`). Vision **describes and
routes only** — it never gives repair advice; that is the Agent (04) over Retrieval (03).

## 2. Scope
**In:** the **FrameGate** change-gate across the continuous feed (rolling pHash + rate cap); best-frame
selection (sharpness + pHash dedup); ONE Qwen vision call per gated look; closed-set product
classification over the catalog; reading the model/serial label (OCR); extracting `ProblemSignals`
(error codes, indicators, parts, damage); normalizing to `IdentifyResult` with `confidence`,
ranked `candidates`, and a `needs_confirmation` flag; provider-agnostic backends (mock + real Qwen).
**Out:** fetching/retrieving docs (03); deciding or speaking the next step, asking the confirm
question (04/05); camera transport and frame capture (05); building the catalog (01); any repair
advice (forbidden here).

## 3. Design (how it works; key decisions)
- **Closed-set, not open-ended.** The catalog is injected into the prompt as the *allowed* product
  set; the model must pick a `product_id` from it or return `null`. This is what makes a generic page
  workable — the company's own products are the label space.
- **Continuous feed, gated calls (the hot loop).** "See" streams frames continuously, so a stateful
  **`FrameGate`** (`src/perception/frames.py`) sits across the feed: it keeps a **rolling pHash
  reference** and lets a frame through only when the view **meaningfully changes** (pHash Hamming ≥
  `change_distance`) **and** a **rate cap** (`min_interval_s`) has elapsed. Result: Qwen fires when the
  customer moves to a new part, **not** on every frame while they hold still (zero calls during static
  stretches). The realtime loop (05, seam S8) calls `gate.should_process(frame)` before `identify`.
  (`product_id` is stable per session; gated re-looks mostly re-read the *problem* in view.)
- **Best-frame selection, then ONE call.** For each gated look, `select_best_frames`
  (`src/perception/frames.py`) ranks the small burst by **sharpness** (variance of an edge-filtered
  image) and drops near-duplicates via a **pHash (imagehash)** Hamming gate, yielding the sharpest
  1–2 distinct frames → **exactly one** vision call (bounded latency and cost).
- **Two evidence paths, one call.** The prompt asks the model to (1) **read the model number off any
  visible label** (OCR → `raw_text`, `model_source="ocr_label"`) and (2) **visually match** the
  device shape against the catalog (`model_source="visual_match"`). OCR is preferred when a label is
  legible — it is the strongest signal; visual match is the fallback. `ref_image` per `CatalogEntry`
  (Unsiloed-extracted) makes the visual match image-to-image and far more reliable than name-only.
- **Problem-spotting in the same pass.** The same prompt extracts `ProblemSignals` — error codes,
  indicators ("amber blinking light"), parts ("rear paper tray"), damage, and a `summary`.
  `ProblemSignals.to_query()` becomes the retrieval query downstream.
- **Provider-agnostic (vision swap point, 00 §7).** `VisionProvider` protocol
  (`src/perception/providers/base.py`) returns a raw dict matching `RESPONSE_SCHEMA`; `identify`
  validates and normalizes it. **Mock-first** (`MockVisionProvider`, deterministic, no keys) for
  tests/dev; **real** `QwenGatewayProvider` calls the vision model through the TrueFoundry AI Gateway
  (OpenAI-compatible chat-completions, image parts). **Qwen-VL is the default, not a hard dependency**:
  the model is bound to change, so a swap is one new provider impl *or* repointing the gateway
  `vision.identify` logical name (06) — the normalizer and every caller are untouched because they
  depend only on `RESPONSE_SCHEMA`, not the vendor.
- **Confidence → confirmation.** `identify` sets `needs_confirmation` when the chosen product is
  low-confidence or when top `candidates` are close (ambiguous). `IdentifyResult.resolved` is true
  only when `product_id is not None and not needs_confirmation`. Unresolved → the widget (05/04) asks
  the customer to pick / confirm; it does **not** silently guess and mis-scope retrieval.

## 4. Data & interfaces (PRODUCES / CONSUMES; see 00 contracts)
**Consumes** (from 01): `list[CatalogEntry]` — the company's closed product set (`product_id`, name,
brand, aliases, description, optional `ref_image`). Plus the raw camera `frames: list[bytes]` (from 05).
**Produces** (to 03/04): `IdentifyResult` (`product_id`, `confidence`, `model_source`, `brand`,
`model`, `raw_text`, `candidates`, `needs_confirmation`, `problem: ProblemSignals`).

```python
# public surface — src/perception/__init__.py
async def identify(frames: list[bytes], catalog: list[CatalogEntry],
                   *, provider: VisionProvider) -> IdentifyResult: ...

# continuous-feed change gate — src/perception/frames.py (stateful, one per SupportSession)
class FrameGate:
    def __init__(self, change_distance: int = 10, min_interval_s: float = 0.5): ...
    def should_process(self, frame: bytes, *, now: float | None = None) -> bool: ...  # rolling pHash + rate cap
    def reset(self) -> None: ...

# provider contract — src/perception/providers/base.py
class VisionProvider(Protocol):
    async def identify(self, images: list[bytes], catalog: list[CatalogEntry]) -> dict: ...
# build_prompt(catalog) + RESPONSE_SCHEMA define the closed-set identify+diagnose JSON every
# provider returns; frames.select_best_frames(frames, k=2) picks the inputs.
```
Downstream wiring: `IdentifyResult.product_id` → `RetrievalQuery.product_id` (scope);
`IdentifyResult.problem.to_query()` → `RetrievalQuery.text` (drive). Moss metadata is string-valued.

## 5. Sequence / flow
```
customer hits "See" (05) ─ CONTINUOUS frames ─▶ FrameGate.should_process(frame)   # rolling pHash + rate cap
   (no meaningful change → skip, no VLM call)         on meaningful change ▼
                                              identify(burst, catalog, provider)
   1. select_best_frames(burst, k=2)         # sharpness rank + pHash dedup (frames.py)
   2. build_prompt(catalog)                  # closed-set ids + problem schema (base.py)
   3. provider.identify(images, catalog)     # ONE Qwen vision call via TrueFoundry Gateway
   4. validate + normalize → IdentifyResult  # clamp confidence, keep ids in catalog, rank candidates
   5. set needs_confirmation if low-conf / ambiguous candidates
        ├─ resolved → product_id + problem ─▶ Retrieval (03), scoped + driven
        └─ needs_confirmation ─▶ widget/Agent (04/05) asks the customer to confirm/pick
```

## 6. Failure modes & fallbacks (fail-safe behavior)
| Failure | Fallback |
|---|---|
| Camera held still / no view change | `FrameGate.should_process` returns False → **no VLM call** (intended: zero cost during static stretches) |
| Camera shaking / over-moving | rate cap (`min_interval_s`) bounds calls to ≤ ~2/s even when every frame "changes" |
| No product matches the catalog | `product_id=null`, `model_source="none"`, `needs_confirmation=true` → widget asks the customer to pick |
| Low confidence / close candidates | `needs_confirmation=true` → confirm with the customer; never silently mis-scope retrieval |
| All frames blurry / unreadable | `select_best_frames` skips unreadable frames; if none survive → prompt re-capture (05) |
| Label illegible (no OCR) | fall back to `visual_match` against `ref_image` / catalog description |
| Malformed / non-JSON model output | `identify` normalizer rejects out-of-catalog ids, clamps confidence, defaults to `needs_confirmation=true` |
| Qwen endpoint down / slow | TrueFoundry Gateway routes to another multimodal model (06); else mock/degrade to manual product pick |
| Vision misreads the problem | `ProblemSignals` only *drives* the query; the Agent confirms verbally before acting — vision never advises |

## 7. Sponsor mapping
- **Qwen** — the vision model doing closed-set classification, OCR, and problem extraction in one call.
- **TrueFoundry AI Gateway** — OpenAI-compatible routing for the Qwen call: fallback model, cost, and
  guardrails; the `QwenGatewayProvider` targets the Gateway, not the model directly (see 06).
- **Moss / Unsiloed (adjacent)** — consumes the catalog + `ref_image`s Unsiloed extracts at onboarding
  (01); emits the scope + query that Moss (03) retrieves against.

## 8. Reuse + Open questions
**Reuse:** built in `src/perception/` — `frames.py` (best-frame: sharpness + pHash dedup, **plus the
`FrameGate` rolling-pHash change gate** for the continuous feed, via `imagehash`), `providers/base.py`
(VisionProvider protocol + prompt + JSON schema), `providers/mock.py` + `providers/qwen_gateway.py`
(Qwen via TrueFoundry, image-to-image ref-photo match), `identify.py` (normalizer +
confidence/ambiguity gating). **11 tests pass** (`tests/test_identify.py`, mock). Principle: Qwen
describes-and-routes only (product + problem); never repair advice.
**Open questions:** confidence + candidate-margin thresholds for `needs_confirmation` (tune live);
whether to send 1 vs 2 frames per call (cost vs recall); how heavily to weight OCR over visual match
when they disagree; whether to pass `ref_image`s inline to the model or rely on text catalog for v0.

---
Confirmation: HLD 02 — Perception written to `docs/clutch/hld/02-perception.md`, following the 8-section template and consistent with `src/contracts.py`, `src/perception/frames.py`, and `providers/base.py`.
