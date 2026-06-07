# Fixalong LLD 10 — Onboarding flow (cold path orchestration)
**Implements:** HLD 10 · **Module(s):** agent/onboarding/onboard.py · **Build tier:** 0/1 · **Status:** Draft v0.1

## 1. Responsibility
`onboard.py` is the **orchestrator for the cold path**: given camera frames it wires together
five existing components — perception (LLD 04), the cache check + persist (LLD 11), MCP
manual discovery (LLD 06), the built Unsiloed client, and the repair-graph builder (LLD 01) —
and returns `(ProductIdentity, model_slug, list[Chunk])`. It owns only **sequencing, the
cache short-circuit, progress emission to the UI, and per-step fallbacks**. It does *not*
implement vision, MCP transport, parsing internals, graph heuristics, signing, or indexing —
those live in their own LLDs and are called, not duplicated here.

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/onboarding/onboard.py` | `async onboard(frames, *, session, emit) -> OnboardResult` |
| | `normalize_model_slug(identity: ProductIdentity) -> str` |
| | `OnboardResult` (dataclass, §4) · `OnboardError` (exc) |

Everything else is imported:
`perception.vision.identify_product` (04) · `knowledge.verify.index_exists` /
`knowledge.persist.persist_chunks` (11) · **`integrations.mcp.find_manual` (Part 2 / 06)** ·
**`integrations.unsiloed.parse_manual` (Part 2)** · `ingest_graph.build_repair_graph`,
`graph_to_chunks` (01).

> **Ownership note:** `find_manual` (TrueFoundry MCP) and `parse_manual` (Unsiloed) are owned
> by **Part 2** — onboarding *calls* them across the seam; it does NOT import `UnsiloedClient`
> directly.

## 3. Dependencies
- **Internal:** LLD 04 (perception), LLD 11 (cache + persist), **Part 2** (`find_manual` +
  `parse_manual`), LLD 01 (graph→chunks), `agent/contracts.py` (shared types).
- **Built/reused:** `ingest/chunker.py` (graph_to_chunks reuses its slug/metadata patterns),
  `ingest/fact_moss.py` (via LLD 11). *(Unsiloed itself is wrapped by Part 2's `parse_manual`.)*
- **Libs:** `asyncio` (awaits the async sub-steps). No network/parse code here.
- **Env:** none consumed directly; sub-modules own `UNSILOED_API_KEY` (Part 2), MCP gateway creds,
  Moss creds. `onboard` reads only `session.identity` (issuer/scope) to pass to `find_manual`.

## 4. Data structures (beyond shared contracts)
`ProductIdentity`, `ManualRef`, `RepairNode`, `Chunk` are the shared contracts (README §
"Shared contracts") — restated by reference, not redefined. New to this flow:
```python
@dataclass
class OnboardResult:
    identity: ProductIdentity
    model_slug: str
    chunks: list[Chunk]          # [] when cache_hit (already indexed — nothing to persist)
    cache_hit: bool              # True → caller skips LLD 11 persist, routes straight to LLD 12
    degraded: list[str]          # step names that fell back (e.g. ["manual","graph"])

OnboardStage = Literal[          # progress strings emitted to UI over the data channel
    "identifying", "checking_cache", "fetching_manual",
    "parsing_manual", "building_graph", "indexing", "ready"]
```
`emit` is a callback `(stage: OnboardStage, detail: dict[str,str]) -> None` supplied by the
orchestrator (LLD 13); it serializes to JSON on the LiveKit data channel for LLD 09.

## 5. Key functions

```python
async def onboard(frames, *, session, emit) -> OnboardResult:
    degraded: list[str] = []

    # ── Step 1: identify (LLD 04) ───────────────────────────────────────────
    emit("identifying", {})
    identity = await identify_product(frames)              # Qwen + OCR, perception
    if identity.confidence < CONF_CONFIRM:
        identity = await _confirm_or_ask(identity, session, emit)   # fallback chain §8
    model_slug = normalize_model_slug(identity)

    # ── Step 2: cache short-circuit (LLD 11) ────────────────────────────────
    emit("checking_cache", {"model": model_slug})
    if await index_exists(model_slug):                     # delegated to HLD 11 registry
        emit("ready", {"model": model_slug, "source": "cache"})
        return OnboardResult(identity, model_slug, [], cache_hit=True, degraded=degraded)

    # ── Step 3: find manual (LLD 06 / TrueFoundry MCP) ──────────────────────
    emit("fetching_manual", {"model": model_slug})
    try:
        manual = await find_manual(identity.brand, identity.model, identity.object_type,
                                   scope=session.identity)
    except McpError:
        manual = await _manual_fallback(identity, session, emit)   # §8; may raise OnboardError
        degraded.append("manual")

    # ── Step 4: parse manual (Part 2's parse_manual — Unsiloed lives in Part 2) ──
    emit("parsing_manual", {"title": manual.title, "source": manual.source})
    try:
        unsiloed_result = await parse_manual(manual)      # → integrations.unsiloed (Part 2)
    except UnsiloedError:
        unsiloed_result = _prebaked_or_raise(model_slug)  # demo JSON; else OnboardError
        degraded.append("parse")

    # ── Step 5: build graph → chunks (LLD 01) ───────────────────────────────
    emit("building_graph", {"model": model_slug})
    try:
        graph = build_repair_graph(unsiloed_result, model=model_slug)
        chunks = graph_to_chunks(graph)                   # reuses chunker patterns
        if not chunks:
            raise ValueError("empty graph")
    except Exception:
        chunks = _hand_authored_chunks(model_slug)        # curated demo graph; else OnboardError
        degraded.append("graph")

    # ── Step 6: hand off to LLD 11 (sign + index + cache) ───────────────────
    emit("indexing", {"model": model_slug, "chunks": str(len(chunks))})
    await persist_chunks(model_slug, chunks, issuer=session.kb_issuer)   # HLD 11 owns trust/KB/index
    emit("ready", {"model": model_slug, "source": "fresh"})
    return OnboardResult(identity, model_slug, chunks, cache_hit=False, degraded=degraded)


# NOTE: parse_manual is owned by Part 2 (integrations.unsiloed) — onboarding imports it, it does
# NOT construct UnsiloedClient. Part 2 wraps the built client (submit→poll) in a thread executor
# and returns the UnsiloedResult. Onboarding just does: unsiloed_result = await parse_manual(manual)


def normalize_model_slug(identity: ProductIdentity) -> str:
    # mirror chunker._slug: lowercase, brand+model, non-alnum → '-', collapse, cap len.
    return _slug(f"{identity.brand} {identity.model}", max_len=40)   # e.g. "hp-laserjet-pro-m404"
```

## 6. Control flow / sequence
```
LLD 13 orchestrator
   └─ onboard(frames, session, emit)
        1 identify_product ──────────────▶ LLD 04 (Qwen OCR)      [emit identifying]
           └─ low conf ▶ _confirm_or_ask (user / manual entry)
        2 index_exists(slug) ────────────▶ LLD 11 cache  [emit checking_cache]
           └─ HIT ▶ return cache_hit=True ──────────────▶ LLD 12 (skip 3-6)   ★ short-circuit
        3 find_manual ───────────────────▶ LLD 06 MCP gateway    [emit fetching_manual]
        4 parse_manual ─────────────────▶ Part 2 (Unsiloed)     [emit parsing_manual]
        5 build_repair_graph→graph_to_chunks ▶ LLD 01            [emit building_graph]
        6 persist_chunks ────────────────▶ LLD 11 sign+index     [emit indexing → ready]
           └─ return (identity, slug, chunks, cache_hit=False) ─▶ LLD 13 → LLD 12 (write LLD 11)
```
**Cold→hot handoff:** the return value is the contract. On `cache_hit=True` the result carries
`chunks=[]` — the caller must **not** re-persist; it routes straight to LLD 12 for `model_slug`.
On `cache_hit=False`, persist already ran inside step 6, so the caller likewise routes to LLD 12;
`chunks` is returned for logging/inspection only. Either way LLD 13 transitions the session
lifecycle `cold → hot` once `onboard` returns `ready`.

## 7. Config & tuning
| Param | Default | Meaning |
|---|---|---|
| `CONF_CONFIRM` | `0.75` | below → confirm/ask before fetching (wrong manual worse than asking) |
| `CONF_ACCEPT` | `0.90` | above → accept identity silently, no confirm prompt |
| `parse max_wait` / `poll_interval` | `600 s` / `2.0 s` | **owned by Part 2's `parse_manual`** (Unsiloed poll); onboarding just awaits it |
| `manual source priority` | `manufacturer > enterprise > web` | picked inside LLD 06; tie → ask |
| `slug max_len` | `40` | matches `chunker._slug` for index-name consistency |

## 8. Error handling & fallbacks (fail-safe per step)
| Step | Failure | Fallback (in order) |
|---|---|---|
| 1 identify | can't read model | appearance inference → `_confirm_or_ask` (emit, prompt user) → manual text entry; only `raise OnboardError("no_identity")` if all refused |
| 1 identify | low confidence | confirm with user before fetching (gated by `CONF_CONFIRM`) |
| 2 cache | registry unreachable | treat as **miss** (proceed to fetch); log `degraded=["cache_check"]` — never fabricate a hit |
| 3 manual | MCP gateway/tool down | `_manual_fallback`: direct mfr search → user pastes URL/file; if none → `OnboardError("no_manual")` |
| 3 manual | no manual found | ask user; or generic same-class manual flagged in metadata (`degraded`) |
| 4 parse | Unsiloed slow/error/timeout | `_prebaked_or_raise`: pre-parsed JSON for known demo model; else `OnboardError("parse_failed")` |
| 5 graph | empty/noisy extraction | `_hand_authored_chunks` curated demo graph; else `OnboardError("graph_failed")` |
| 6 index | persist fails (LLD 11) | bubble up — do **not** mark `ready`; caller keeps session cold and surfaces error to UI |
Principle (README): on uncertainty the flow asks the user or fails loudly; it never indexes
invented content. Every fallback records its step name in `degraded` for the UI/audit trail.

## 9. Latency / perf notes
Cold path — **seconds to tens of seconds**, never on the live hot loop.
- identify (1): ~1–3 s (one Qwen call). cache check (2): <50 ms (LLD 11 registry).
- find_manual (3): ~1–5 s (MCP round-trip + remote fetch).
- **parse (4) dominates: seconds–tens of seconds** (Unsiloed submit→poll). Runs in a thread
  executor so the async loop stays responsive; the `fetching_manual`/`parsing_manual` UI
  states cover this window.
- graph (5): <500 ms (CPU heuristics). persist (6): seconds (sign + Moss index create).
- **Offline/cache-hit:** steps 3–6 skipped; total ≈ identify + cache ≈ **<3 s**. Demo model is
  pre-onboarded so first live session is always a cache hit; a 2nd machine shows the live cold
  path behind the progress states.

## 10. Test plan
**Unit (mock every sub-step — this is a wiring test):** patch `identify_product`,
`index_exists`, `find_manual`, `parse_manual`, `build_repair_graph`/`graph_to_chunks`,
`persist_chunks`; capture `emit` calls.
- **Happy path:** all mocks succeed → assert call **order** is exactly
  identify → index_exists → find_manual → parse → build → persist; assert `emit` sequence
  `identifying, checking_cache, fetching_manual, parsing_manual, building_graph, indexing, ready`;
  assert returned `model_slug` matches `normalize_model_slug`, `cache_hit=False`.
- **★ Cache short-circuit:** `index_exists → True` → assert `find_manual`, `parse`,
  `build_repair_graph`, `persist_chunks` are **never called**; result `cache_hit=True`,
  `chunks==[]`; emit ends `checking_cache → ready(source=cache)`.
- **Low confidence:** identity below `CONF_CONFIRM` → assert `_confirm_or_ask` invoked before
  `find_manual`.
- **Per-step fallbacks:** raise `McpError` / `UnsiloedError` / empty-graph in turn → assert the
  matching fallback runs, `degraded` contains the step, and (when fallback yields content) the
  flow still reaches `persist_chunks` + `ready`.
- **Hard failures:** fallback also unavailable → assert `OnboardError` raised, `persist_chunks`
  not called, no `ready` emitted.
- **Slug:** `normalize_model_slug` parity with `chunker._slug` on sample identities.
**Integration:** mock `parse_manual` (its real-parse integration test lives in Part 2) + real
`graph_to_chunks`, mock perception/MCP/persist; assert non-empty `Chunk[]` with string-only
metadata and a deterministic `model_slug`.

## 11. Build checklist (Tier-0 first)
1. **T0** Define `OnboardResult`, `OnboardStage`, `OnboardError` in `onboard.py`; add `normalize_model_slug` reusing `chunker._slug`.
2. **T0** Implement `onboard` skeleton with the 6 steps calling **stubbed** sub-modules + `emit`; unit-test order & cache short-circuit against stubs.
3. **T0** Import `parse_manual` from Part 2 (stub it until Part 2 lands); assert onboarding awaits it. (Part 2 owns the real Unsiloed wiring + its integration test.)
4. **T1** Wire real `identify_product` (LLD 04) and `index_exists` (LLD 11); add `_confirm_or_ask`.
5. **T1** Wire `find_manual` (LLD 06) + `_manual_fallback`; wire `build_repair_graph`/`graph_to_chunks` (LLD 01) + `_hand_authored_chunks` curated demo graph + `_prebaked_or_raise`.
6. **T1** Wire `persist_chunks` (LLD 11); confirm cold→hot handoff with LLD 13; end-to-end onboard of the demo printer, then verify a 2nd run hits the cache.
