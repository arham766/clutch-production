# Workstream 1 — Perception & Knowledge  *(YOUR PART)*

**Owner:** you · **Skills:** ML / vision / data · **Mission:** own the entire path from **what
the camera sees** to **verified, searchable repair knowledge**. You are the agent's *senses +
memory + conscience*. Everyone else reads from you.

This part fuses three things into one cohesive pipeline:
**Perception (vision + KriNein)** → **Onboarding (learn a machine)** → **Knowledge & Trust
(FACT × Moss)**. Camera in → trustworthy facts out.

> Big head start: the Knowledge & Trust half is **already built** — `fact/` (52 tests) and
> `ingest/` incl. `fact_moss.py` (39 tests). Your greenfield work is mostly **vision** + the
> **repair-graph builder** + the **onboarding orchestration**.

## You own
- **LLDs:** [04 perception](../lld/04-perception.md), [10 onboarding](../lld/10-onboarding.md),
  [01 ingestion](../lld/01-ingestion.md), [02 retrieval](../lld/02-retrieval.md),
  [11 FACT × Moss](../lld/11-fact-moss.md).
  *(MCP `find_manual` and the Unsiloed parse call are **Part 2's** — your `onboard` calls them.)*
- **Modules:**
  - `agent/perception/gate.py` — KriNein pHash frame gate (**pHash only; NO SSIM/LPIPS**; optional CLIP Tier-B), rolling ref + rate-limit ≤~2 VLM calls/sec.
  - `agent/perception/vision.py` — Qwen (multimodal) → `VisualObservation`; **describe + route only, never repair advice**.
  - `agent/onboarding/onboard.py` — the cold path orchestration (calls Part 2 for `find_manual` + `parse_manual`).
  - `ingest/repair_graph.py` *(new)* — parsed segments → RepairNodes → chunks.
  - `agent/knowledge/retrieve.py`, `verify.py`, `registry.py` — the read API.
  - Built & reused: `ingest/chunker.py`, `fact_moss.py`, `ingest.py` (offline pre-bake), `fact/`.

## Interfaces you PRODUCE (Part 2 depends on all of these — keep stable)
```python
# perception (live)
observe(frame, hint) -> VisualObservation          # gated; visible_object, candidate_parts[], hazards[], next_queries[]
observe_stream(frames, on_observation)
identify_product(frames) -> ProductIdentity        # OCR the model number; fallbacks
# onboarding (cold) — orchestrates your modules + Part 2's find_manual/parse_manual
onboard(frames, *, session, emit) -> OnboardResult # identify→cache?→[P2 find_manual]→[P2 parse]→graph→sign→index
# knowledge read (hot)
retrieve(state, *, obs=None) -> list[Chunk]        # builds query from state; Moss <10 ms
prefetch(state, *, obs=None) -> None               # warm cache on visual change
verify_chunk(c, *, trusted_issuers) -> Chunk       # stamps c.verdict (STRING: ACT/HEDGE/ESCALATE/REFUSE)
load_session_index(model_slug, *, client=None) -> str
index_exists(model_slug) -> bool ; index_name(slug) -> str
remember(session_id, item) -> None                 # Tier-2 OPTIONAL: ephemeral session-<id> index, NEVER the manual index; off by default
# knowledge write (used inside onboard)
build_repair_graph(unsiloed_result, *, model, source) -> RepairGraph
graph_to_chunks(graph, *, source) -> list[Chunk]
sign_chunks(chunks, kb_key) -> list[Chunk]         # BUILT (fact_moss)
persist(chunks, model_slug) -> index_name          # sign + inject_into_moss (BUILT)
```

## Interfaces you CONSUME
- From **Part 2:** `find_manual(brand, model, object_type, *, scope=None) -> ManualRef` and
  `parse_manual(manual_ref) -> UnsiloedResult` (your `onboard` calls these); `agent/config.py`
  (Moss/Qwen/FACT singletons + env); `agent/contracts.py` (the dataclasses).
- Built code otherwise — you are the bottom of the stack.

## External deps / keys (you hold the Moss + vision + trust ones)
Moss (`MOSS_PROJECT_ID/KEY`), Qwen (`QWEN_*`), FACT KB key (`FACT_KB_PRIVATE_KEY` / `.kb_key.b64`).
**You generate + own the KB key** and publish its `did:key` (`KB_ISSUER`) — the agent trusts
exactly this. *(TrueFoundry MCP + Unsiloed keys live in Part 2.)*
Libs: `imagehash`+Pillow (pHash), optional `open_clip` (Tier-B).

## Build order
- **Tier 0 (unblocks everyone):**
  1. Run `ingest.py --sign` on one real manual → signed Moss index + share `KB_ISSUER`.
     *(This offline pre-bake uses Unsiloed internally — grab the `UNSILOED_API_KEY` from Part 2
     for the one-off build.)*
  2. `verify.py` (`verify_chunk` → `.decision.value` string) + `retrieve(state)` (query build +
     `MossClient.query` + filters) → real `Chunk[]` in <10 ms.
  3. `perception/gate.py` (pHash gate) + `vision.py` (`observe` → `VisualObservation`) +
     `identify_product` (OCR model).
- **Tier 1:** `repair_graph.py`; full `onboard()` (identify → `index_exists` → **P2** `find_manual`
  → **P2** `parse_manual` → graph → `persist`); `prefetch` cache; per-model `index_exists` registry.
- **Tier 2:** CLIP Tier-B gate; multi-index/risk sweep; local-cosine Moss fallback; continuous
  re-identify stream feeding Part 2's re-route.

## Parallel-work stubs (hand to Part 2 on hour 0)
- `retrieve(state)` → 2-3 canned `Chunk`s w/ realistic metadata + `time_taken_ms` (+ a switch to
  force REFUSE/HEDGE for demo beats).
- `verify_chunk(c)` → stamp `c.verdict="ACT"`.
- `observe(frame)` → canned `VisualObservation` (rear panel + pickup roller + fuser hazard).
- `identify_product` / `onboard` → fixed identity / `OnboardResult(cache_hit=True)` → pre-baked index.

## Definition of done
(1) `observe` turns real frames into a correct `VisualObservation` with ≤~2 VLM calls/sec;
(2) `onboard` takes a *new* machine's frames and returns a usable signed index end to end;
(3) `retrieve`+`verify_chunk` return verified, ranked chunks in <10 ms, with the right verdict for
signed/tampered/foreign/stale (reuse `ingest/tests/test_fact_moss.py`).

## Your demo moments
- **Ambient takeover** — user opens the panel, says nothing, the agent already knows (your `observe`).
- **Instant grounded answer** + the **7 ms latency badge** (your `retrieve` `time_taken_ms`).
- **REFUSE / HEDGE** of a poisoned/stale fact (your `verify_chunk`).
- **Live onboard of the 2nd machine** (your `onboard`).
