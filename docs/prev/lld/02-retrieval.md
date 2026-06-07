# Fixalong LLD 02 — Retrieval (Moss query mechanics)
**Implements:** HLD 02 (+ HLD 11 read side) · **Module(s):** `agent/knowledge/retrieve.py` · **Build tier:** 0 · **Status:** Draft v0.1

> Conventions, code layout, and shared contracts (`Chunk`, `RepairState`, `VisualObservation`,
> `Verdict`) live in [README](README.md) — restated by reference, not redefined.

## 1. Responsibility
Be the **hot-path retrieval layer**. Given a `RepairState` (and the latest `VisualObservation`),
build a hybrid Moss query, run it against the session's loaded index in <10 ms, and return a ranked
`list[Chunk]` of **raw** chunks. It also warms a **vision-driven prefetch cache** so speech hits a
warm result, and runs an optional parallel `risk_level=high` safety sweep. **Runtime recall lives
in `RepairState` (LLD 05 §5.6.5), not Moss** — `remember()` here is a **Tier-2, off-by-default**
helper that upserts into a *separate ephemeral* `session-<id>` index (§5.7), never the manual
index. It does **not** verify trust — FACT
verification is LLD 11; this module returns chunks with `verdict=None`, and `knowledge/verify.py`
attaches the verdict afterward.

## 2. Files & public surface
`agent/knowledge/retrieve.py`
```python
async def load_session_index(model_slug: str, *, client: MossClient | None = None) -> str  # → index_name
    # client defaults to the process Moss singleton (LLD 08); call sites pass only model_slug
async def retrieve(rc: RetrieveCtx, state: RepairState, *, obs: VisualObservation | None = None) -> list[Chunk]
async def prefetch(rc: RetrieveCtx, state: RepairState, *, obs: VisualObservation | None = None) -> None  # warm cache from state-derived queries
async def remember(rc: RetrieveCtx, session_id: str, item: dict) -> None           # Tier-2 OPTIONAL: upsert into ephemeral session-<id> index (NOT manual; off by default)
def build_query(state: RepairState, obs: VisualObservation | None) -> RetrievalQuery
def queries_from_vision(obs: VisualObservation, state: RepairState) -> list[RetrievalQuery]
class RetrieveCtx                                                                  # holds client, index, cache, fallback
class LocalCosineIndex                                                             # Moss-down fallback (§8)
```

## 3. Dependencies
- **Libs/SDKs:** `moss` (`MossClient`, `DocumentInfo`, `QueryOptions`, `MutationOptions`,
  `SearchResult`); `numpy` (cosine fallback only); `asyncio`.
- **Internal:** `agent/contracts.py` (`Chunk`, `RepairState`, `VisualObservation`); `agent/config.py`.
  `knowledge/verify.py` consumes our output (not a dependency of ours).
- **Env:** `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`, `MOSS_MODEL_ID` (default `moss-minilm`).
  Index name is derived: `fixalong-<model_slug>` (HLD 11 §6 — the cache key).

## 4. Data structures (beyond shared contracts)
```python
@dataclass
class RetrievalQuery:                          # built from state (HLD 11 §7); the cache key seed
    model: str; current_node_id: str | None; user_observation: str
    visible_parts: list[str]; need: list[str]  # need ⊆ {part_identity,warning,next_step,error,diagram,branch}
    type: str | None = None; risk_level: str | None = None

@dataclass
class RetrieveCtx:                              # one per session, built at session start
    client: MossClient; index_name: str; model_slug: str
    cache: dict[str, list[Chunk]]              # key → warmed Chunk[] (vision/partial prefetch)
    pending: dict[str, asyncio.Task]           # in-flight prefetch tasks (dedupe)
    local: "LocalCosineIndex | None" = None    # set when Moss is unreachable (§8)
    top_k: int = 5; alpha: float = 0.7
```
`cache` key = `cache_key(q)` = `sha1(f"{q.model}|{q.current_node_id}|{q.type}|{q.risk_level}|{norm(q.user_observation)}|{','.join(sorted(q.visible_parts))}")`.

## 5. Key functions

### 5.1 `load_session_index` (session start, once)
```python
async def load_session_index(model_slug, *, client=None) -> str:
    client = client or moss_singleton()        # process Moss client (LLD 08)
    name = f"fixalong-{model_slug}"
    await client.load_index(name)              # cloud → in-memory; ~3–5 ms queries after this
    await client.query(name, "warmup", QueryOptions(top_k=1))   # warm embed model + path
    return name
```
Filters only apply to **locally-loaded** indexes (Moss logs+drops a filter on a cloud query), so
`load_index` is mandatory before any filtered `retrieve`. On `RuntimeError`, fall back to §8.

### 5.2 `build_query` (state → RetrievalQuery) — the core algorithm
Deterministic, no LLM. Maps live signals to a retrieval target (HLD 02 §5).
```
build_query(state, obs):
  q.model            = state.object["model"]
  q.current_node_id  = state.current_node_id
  parts              = [p["name"] for p in state.visible_parts] + ([obs.visible_object] if obs else [])
  q.visible_parts    = dedupe(parts)
  # 1. user_observation text: latest spoken observation > newest visual hint > goal
  q.user_observation = (state.user_observations[-1]["text"] if state.user_observations
                        else (obs.next_retrieval_queries[0] if obs and obs.next_retrieval_queries
                              else state.goal))
  # 2. phase → need + type/risk filter (the colloquial→structured mapping)
  match state.phase:
    "warning":     need=["warning"];                 type="warning"; risk="high"
    "diagnosing":  need=["branch","part_identity"];  type=None
    "guiding":     need=["next_step","warning"];      type="step"
    "confirming":  need=["next_step"];                type="step"
    _:             need=["next_step"]
  # 3. precondition skip: if an action already covers a node's first branch, target the next
  if state.actions_taken and q.current_node_id: q.need.insert(0,"branch")  # widen toward branches
  return RetrievalQuery(model=q.model, current_node_id=q.current_node_id,
                        user_observation=q.user_observation, visible_parts=q.visible_parts,
                        need=q.need, type=type, risk_level=risk)
```

### 5.3 `retrieve` — query build → cache hit → Moss query
```python
async def retrieve(rc, state, *, obs=None) -> list[Chunk]:
    q = build_query(state, obs)
    key = cache_key(q)
    if key in rc.cache:            return rc.cache.pop(key)        # warm hit (consume)
    if key in rc.pending:          return await rc.pending[key]    # join in-flight prefetch
    chunks = await _run_query(rc, q)
    # optional parallel safety sweep when not already a high-risk query
    if q.risk_level != "high":
        sweep = await _safety_sweep(rc, q)
        chunks = _merge(chunks, sweep)        # sweep chunks appended, dedup by id, keep max score
    return chunks
```
`_run_query` builds `QueryOptions` and calls Moss (or the local fallback):
```python
async def _run_query(rc, q) -> list[Chunk]:
    opts = QueryOptions(top_k=rc.top_k, alpha=rc.alpha, filter=_filter(q))
    try:
        res: SearchResult = await rc.client.query(rc.index_name, q.user_observation, opts)
    except Exception:                          # Moss unreachable mid-session
        rc.local = rc.local or await LocalCosineIndex.from_moss(rc)
        return rc.local.query(q.user_observation, rc.top_k, _filter(q))
    if not res.docs:                           # empty → widen (HLD 02 §9), never invent
        opts = QueryOptions(top_k=rc.top_k, alpha=0.4, filter=_node_only_filter(q))
        res = await rc.client.query(rc.index_name, q.user_observation, opts)
    return [_to_chunk(d) for d in res.docs]
```
`_to_chunk(d)` maps a `QueryResultDocumentInfo` → `Chunk(id=d.id, text=d.text, metadata=dict(d.metadata or {}), score=d.score, type=meta.get("type"), part=meta.get("part"), risk_level=meta.get("risk_level"), verdict=None)`.

### 5.4 `_filter` — string-valued metadata filter (Moss `$and` form)
Moss filters are `{"$and": [{"field": F, "condition": {"$eq": V}}]}`, **all values strings**.
```python
def _filter(q) -> dict | None:
    terms = [{"field": "model", "condition": {"$eq": q.model}}]      # always scope to model
    if q.type:        terms.append({"field": "type",       "condition": {"$eq": q.type}})
    if q.current_node_id: terms.append({"field": "node_id","condition": {"$eq": q.current_node_id}})
    if q.risk_level:  terms.append({"field": "risk_level", "condition": {"$eq": q.risk_level}})
    return {"$and": terms}
def _node_only_filter(q):  # widen step: keep model+node, drop type/risk
    return {"$and": [{"field":"model","condition":{"$eq":q.model}}] +
                    ([{"field":"node_id","condition":{"$eq":q.current_node_id}}] if q.current_node_id else [])}
```

### 5.5 `prefetch` (vision-driven, PRIMARY) + `queries_from_vision`
```python
async def prefetch(rc, queries):
    for q in queries:
        key = cache_key(q)
        if key in rc.cache or key in rc.pending: continue
        rc.pending[key] = asyncio.create_task(_prefetch_one(rc, key, q))

async def _prefetch_one(rc, key, q):
    try:    rc.cache[key] = await _run_query(rc, q)
    finally: rc.pending.pop(key, None)
```
`queries_from_vision(obs, state)` turns each `obs.next_retrieval_queries` + each
`obs.candidate_parts[*]["name"]` into a `RetrievalQuery` carrying the current `node_id` and
`type` hints (e.g. part name → `type="part"`, hazard → `type="warning", risk_level="high"`).
Called on every gated visual change (HLD 02 §6) so speech later hits a warm cache; also called
on speech partials with `candidate_queries(transcript)`.

### 5.6 `_safety_sweep` (optional, parallel)
A second query pinned to `risk_level=high` near the current node, run concurrently so hazard
warnings are never missed even when the main query targets a routine step.
```python
async def _safety_sweep(rc, q) -> list[Chunk]:
    sq = replace(q, type="warning", risk_level="high", user_observation=" ".join(q.visible_parts) or q.user_observation)
    return await _run_query(rc, sq)
```

### 5.7 `remember` — Tier-2 OPTIONAL (ephemeral per-session memory; NOT the runtime recall path)
Runtime recall is in-memory `RepairState` (LLD 05 §5.6.5) — `remember` is **off by default**
(`cfg.session_memory`). It exists only for the Tier-2 semantic-memory option (long/enterprise
sessions). It writes to a **separate ephemeral `session-<id>` index — NEVER the shared manual
index `fixalong-<model>`** (which is read-only at runtime and cached across users; writing chat
into it would leak across sessions and pollute manual retrieval).
```python
async def remember(rc, session_id, item):     # Tier-2 only; item: {"text","node_id","ts"}
    sess_index = f"session-{session_id}"        # ephemeral: created at session start, DELETED at end
    doc = DocumentInfo(id=f"obs-{session_id}-{item['ts']}", text=item["text"],
                       metadata={"type": "observation", "node_id": item.get("node_id", ""),
                                 "session_id": session_id})
    await rc.client.add_docs(sess_index, [doc], MutationOptions(upsert=True))
```
If enabled, queried via multi-index alongside the manual, debounce-batched (~300 ms; reads stay
<10 ms), and the `session-<id>` index is deleted on session end.
so earlier observations are recalled within the same session.

## 6. Control flow / sequence
```
session start ─ load_session_index() ─────────────────────────────► index loaded + warmed
camera frame ─ gate(04) ─ VisualObservation ─ prefetch(queries_from_vision) ─► cache warmed (PRIMARY)
STT partial  ─ prefetch(candidate_queries(transcript)) ───────────────────► cache warmed
intent/turn  ─ retrieve(state, obs) ─ cache hit? ─yes─► return warmed Chunk[]
                                       └─no─► _run_query (+ parallel _safety_sweep) ─► Chunk[]
return Chunk[] (verdict=None) ─► knowledge/verify.py (LLD 11) attaches Verdict ─► state machine (05)
state change ─ remember(observation/action) ─ debounced upsert ───────────► recallable next turn
```

## 7. Config & tuning
| Param | Default | Notes |
|---|---|---|
| `top_k` | 5 | per-query cap (Moss local default is 5) |
| `alpha` | 0.7 | hybrid weight; 0.6–0.8 for colloquial phrasing (HLD 02 §5). Widen step drops to 0.4 |
| model id | `moss-minilm` | `moss-mediumlm` candidate for terse part names (open Q) |
| prefetch fan-out | ≤4 queries/visual change | bound concurrent tasks |
| remember debounce | 300 ms | batch upserts; reads unaffected |
| safety sweep | on unless `risk_level=high` already | parallel, merged by id |

## 8. Error handling & fallbacks (fail-safe)
- **Moss unreachable** (load or query raises): `LocalCosineIndex.from_moss(rc)` pulls all docs once
  via `client.get_docs(index_name)` (or a snapshot persisted at load), embeds the query with the
  same model, ranks by cosine, then applies the **same** `_filter` predicate over string metadata —
  identical `Chunk[]` interface, just slower. `retrieve`/`prefetch`/`remember` keep working
  (remember becomes in-memory append).
- **Empty / low-confidence:** widen (lower `alpha`, drop `type`/`risk` to node-only). If still
  empty, return `[]` — **never invent**; LLD 06/11 will REFUSE on empty.
- **Filter ignored warning:** means the index wasn't loaded locally — treat as a load failure.
- **All exceptions are caught at `_run_query`**; the live loop never crashes on a retrieval miss.

## 9. Latency / perf notes
- Hot path (`retrieve`, local index): **<10 ms**; cache hit is ~0 ms (dict pop). `load_index`
  ~3–5 ms queries after a one-time ~100–150 ms model warm-up.
- Prefetch makes the *perceived* intent latency ~0 (warm hit). Safety sweep runs concurrently —
  adds no serial latency.
- `remember` upsert is off the read path; cosine fallback is ~tens of ms for a tiny session index.

## 10. Test plan (unit + integration; what to mock)
**FakeMossClient** — async, in-memory: `create_index`/`add_docs` store `DocumentInfo` by id;
`load_index` no-op returns name; `query` does substring+metadata-filter match returning a
`SearchResult` of `QueryResultDocumentInfo`; `get_docs` returns all. A `FailingMossClient` raises
on `query`/`load_index` to exercise §8.
- **build_query:** phase → `need`/`type`/`risk_level` table; observation-source precedence
  (spoken > visual > goal); precondition-skip widens to `branch`.
- **_filter:** emits `$and` with string values only; type/node/risk included only when set; widen
  produces node-only filter.
- **retrieve:** cache hit consumes (pop) and skips Moss; cache miss calls `query` once with the
  built `QueryOptions`; empty result triggers the widen re-query; safety sweep merged and
  de-duped by id with max score.
- **prefetch:** dedupes by `cache_key`, joins in-flight `pending`, bounds fan-out; warmed key is
  later hit by `retrieve`.
- **remember:** issues `add_docs(..., MutationOptions(upsert=True))` with string-only metadata.
- **fallback:** with `FailingMossClient`, `retrieve` returns cosine-ranked chunks via
  `LocalCosineIndex` and the result honors the metadata filter.
- **contract:** every returned `Chunk` has `verdict is None` (verification is LLD 11).

## 11. Build checklist (Tier-0 first)
1. `RetrievalQuery`, `RetrieveCtx`, `cache_key`, `_to_chunk` (pure, unit-tested).
2. `build_query` + `_filter`/`_node_only_filter` — the deterministic mapping (no Moss).
3. `load_session_index` + `_run_query` against real `MossClient` (load + filtered query + widen).
4. `retrieve` with cache hit / miss + empty-widen.
5. `prefetch` + `queries_from_vision` + dedupe/pending.
6. `_safety_sweep` parallel + `_merge`.
7. `remember` debounced upsert.
8. `LocalCosineIndex` fallback (§8) + FailingMossClient tests.
9. Wire to `knowledge/verify.py` (LLD 11) and the live loop (LLD 12).
