# Clutch LLD 03 — Retrieval & Grounding (`src/retrieval/`)

**Status:** Draft v0.1 · **Build tier:** 0 · **Owner:** Fardin (Section D)
**Implements:** [HLD 03 — Retrieval & Grounding](../hld/03-retrieval-grounding.md) ·
[Brief 14 — Fardin](../hld/engineers/14-fardin-inference-retrieval-voice.md)
**Reads from:** the `clutch-<company>` Moss index produced by `ingest/` (01)
**Scope of this doc:** the `src/retrieval/` Limb **only** — designed so it builds, tests, and demos
**independent of every other `src/` subfolder** (10 §5/§6). It imports `src/contracts.py` and nothing
else of another section's; all neighbours arrive as **passed-in functions** with one-line mocks.

> This LLD is implementation-level: file layout, exact signatures, the precise Moss SDK calls, the
> compose prompt + citation discipline, the local-cosine fallback, the async lifecycle, the test plan,
> and the **config/Spine changes** the layer needs before it can land.

---

## 0. Independence contract (what this Limb touches)

| Direction | Item | How it's satisfied in isolation |
|---|---|---|
| **Imports** | `contracts.Chunk`, `contracts.RetrievalQuery`, `contracts.Answer`, `contracts.IdentifyResult`, `contracts.ProblemSignals` | Spine only. **Gap:** three of these are not yet in `contracts.py` — see §9.1 (blocker). |
| **Exposes** (S4) | `load_index`, `retrieve`, `retrieve_for`, `compose`, `make_retrieval` | Tonmoy's Agent calls `retrieve_for`/`compose`; this is the only path from the brain to Moss. |
| **Receives** (passed in) | `gateway_client` (for `reason.compose`); `cfg` | `make_retrieval(cfg, gateway_client)` binds them; the Agent then calls the returned closures with no knowledge of the gateway. |
| **Build-against mocks** | `local_cosine` (Moss-free `retrieve`) + `template_compose` (LLM-free `compose`) | Ship inside this folder; no network, no keys, deterministic. CI green with zero external deps. |
| **Real dev target** | the `clutch-demo` Moss index | Live retrieval works day 1 (Arham's index); only `compose`'s LLM waits on the gateway, and `template_compose` covers that gap. |

**No sibling import, ever.** `retrieval/` never imports `agent/`, `perception/`, `gateway.py`, or
`tenancy.py`. The gateway arrives as a callable; `company_id` arrives as a string argument.

---

## 1. Module layout

Mirrors the established `src/perception/` shape (a `base` protocol, a vendor impl, a deterministic
mock, a thin public `__init__`). Async throughout, matching the codebase.

```
src/retrieval/
  __init__.py          # public surface: load_index, retrieve, retrieve_for, compose, make_retrieval
  base.py              # Retriever Protocol + the SearchHit shape every backend returns
  index.py             # MossRetriever: MossClient wrapper, load-once cache, hybrid query, filter
  local_cosine.py      # LocalCosineRetriever: Moss-free fallback (same Protocol) + the mock
  query.py             # RetrievalQuery -> query text + product filter (pure)
  compose.py           # grounded cited compose + refusal; template_compose mock; LLM caller adapter
  settings.py          # role-named knobs read from contracts/config (alpha, top_k, min_score, creds)
  README.md            # already present
```

Why this split: `query.py` and `compose.py` are **pure** (no I/O) → fully unit-testable with no
mocks. `index.py` and `local_cosine.py` are the two interchangeable backends behind one Protocol →
the fallback is a drop-in. `__init__.py` is the seam surface Tonmoy sees.

---

## 2. The Spine shapes this layer uses

From `src/contracts.py` (00 §3). Reproduced here as the **read contract** — this layer does not
redefine them, but three are **not yet present** and must be added to the Spine (§9.1).

```python
@dataclass
class Chunk:                 # a retrieved doc chunk
    id: str
    text: str
    metadata: dict[str, str]            # from the chunker: source, section, page, segment_count, doc_index
    score: float | None = None
    source: str = ""                    # convenience mirror of metadata["source"]

@dataclass
class RetrievalQuery:        # 04 -> 03
    company_id: str
    text: str
    product_id: str | None = None
    need: list[str] = field(default_factory=list)

@dataclass
class Answer:                # 03 -> 04 -> 05
    text: str
    citations: list[dict] = field(default_factory=list)   # {doc, section, score}
```

**Metadata keys are fixed by the producer.** `ingest/chunker.py` writes exactly
`{source, section, page, segment_count, doc_index}` (all string-valued), with chunk ids shaped
`"<source-slug>-p<page>-<idx:04d>"`. The citation `{doc, section, score}` is therefore:
`doc = metadata["source"]`, `section = metadata["section"]`, `score = Chunk.score`. This layer must
not assume any metadata key the chunker doesn't emit.

---

## 3. Public surface (S4 — the only path the Agent uses)

```python
# src/retrieval/__init__.py

async def load_index(company_id: str) -> str:
    """Resolve + load + warm clutch-<company>. Returns the loaded index name.
    Idempotent: a second call for the same company is a no-op (cache hit).
    Mandatory before any *filtered* retrieve (a cloud query logs-and-drops the filter — HLD 03 §3)."""

async def retrieve(company_id: str, text: str, product_id: str | None = None,
                   *, top_k: int = 5) -> list[Chunk]:
    """Hybrid semantic+keyword query of clutch-<company>, product_id metadata filter, top-k.
    Auto-loads the index if not yet warm. Falls back to local-cosine if Moss is unreachable."""

async def retrieve_for(query: RetrievalQuery) -> list[Chunk]:
    """Convenience over retrieve(): builds query text from query.text (Talk/Type) and, when present,
    folds in ProblemSignals via the caller-supplied text (See path) — see §5."""

def compose(answer_query: str, chunks: list[Chunk],
            *, min_score: float = 0.35) -> Answer:
    """Grounded compose: Answer.text built ONLY from chunks; each claim cited {doc, section, score}.
    Empty / all-below-min_score -> Answer(text="", citations=[]) flagged for 'not in docs'/escalate.
    NOTE: the bound (LLM-backed) version is produced by make_retrieval(); this module-level default
    is template_compose (deterministic, no LLM) so the layer is importable with zero deps."""

def make_retrieval(cfg, gateway_client=None):
    """Factory used by src/app.py (10 §7). Binds index config + the compose LLM.
    Returns (retrieve_for, compose) closures the Agent receives. When gateway_client is None,
    compose == template_compose (mock). When real, compose calls reason.compose via the gateway."""
```

**Why a factory.** The seam is `retrieve_for(RetrievalQuery) -> Chunk[]` and
`compose(query, chunks) -> Answer` — the Agent calls them with **no** extra arguments (10 §4, S4). The
gateway client and config can't be parameters of `compose`, so `make_retrieval(cfg, gateway)` closes
over them and hands the Agent two clean callables. This is exactly the `app.py` wiring line
`retrieve, compose = make_retrieval(cfg, gateway) if cfg.real("retrieval") else (local_cosine, template_compose)`.

---

## 4. Backends behind one Protocol (`base.py`)

```python
# src/retrieval/base.py
from typing import Protocol
from contracts import Chunk

class Retriever(Protocol):
    async def load_index(self, company_id: str) -> str: ...
    async def search(self, company_id: str, text: str, product_id: str | None,
                     top_k: int) -> list[Chunk]: ...
```

Two implementations, swapped by config / availability — identical to how `perception/providers/`
swaps `QwenGatewayProvider` ↔ `MockVisionProvider`:

| Impl | File | Role |
|---|---|---|
| `MossRetriever` | `index.py` | Real Moss: load-once cache, hybrid query, product filter, latency. |
| `LocalCosineRetriever` | `local_cosine.py` | Moss-free fallback **and** the build-against mock. |

`retrieve()` (the public fn) holds a primary + fallback: try `MossRetriever`, on a network/SDK error
fall through to `LocalCosineRetriever` over the same chunk embeddings (HLD 03 §6, row 1). Both return
`list[Chunk]` — callers never see which served.

---

## 5. `MossRetriever` — the hot read path (`index.py`)

### 5.1 The Moss SDK calls (verified against the official docs)

**SDK source of truth:** [`docs.moss.dev`](https://docs.moss.dev/docs/reference/sdk) — the usemoss /
InferEdge retrieval engine (`pip install moss`; sub-10 ms; models `moss-minilm` (default) /
`moss-mediumlm` / `moss-litelm` / `custom`). Cross-checked with the live usage in
`ingest/tests/test_live.py` and `ingest/ingest.py`. *(Not to be confused with `developers.getmoss.com`,
which is Nufin's unrelated expense-management API — wrong "Moss".)*

The SDK is fully async and used as:

```python
from moss import MossClient, QueryOptions          # pip install moss  (>=1.1.1, per ingest/pyproject)

client = MossClient(MOSS_PROJECT_ID, MOSS_PROJECT_KEY)   # project id + key from the Moss portal
await client.load_index(index_name)                 # cloud -> in-process, warms; optional auto_refresh=
res = await client.query(
    index_name,
    query_text,
    QueryOptions(top_k=k, alpha=0.7, filter={...}),  # filter is a FIELD OF QueryOptions (verified)
)
for d in res.docs:                                   # ranked results
    d.id; d.text; d.score; d.metadata               # metadata IS returned on each doc -> citations
```

**Verified field semantics (docs.moss.dev → `/docs/reference/python/api`):**
- **`QueryOptions(top_k, alpha, filter, embedding)`** — the **metadata filter lives inside
  `QueryOptions`** (confirmed; not a separate `query()` kwarg). This matches the LLD's design exactly:
  `_query_options()` builds the whole `QueryOptions`.
- **`alpha`** — `1.0 = pure semantic, 0.0 = pure keyword` (SDK default `0.8`). HLD's `≈0.7` is a slight
  keyword lean off the default, so colloquial phrasing *and* exact part/error strings rank — and the
  widening step's "lower `alpha`" (§5.4) correctly means "favor keyword/exact strings." ✓
- **filter shape** — `{"$and": [{"field": "product_id", "condition": {"$eq": product_id}}]}`. The HLD's
  shape is correct (operators available: `$eq $ne $gt $gte $lt $lte $in $nin $near`, composed with
  `$and/$or`). All values are strings (Moss metadata is string-valued).
- **result docs carry `metadata`** — so `{doc, section, score}` citations are populated directly from
  `d.metadata["source"]` / `d.metadata["section"]` + `d.score`. ✓ (No dependency on an undocumented field.)

> ⚠️ **One real correction — latency is NOT on the result.** The official API documents **no
> `time_taken_ms`** on the query result. So the latency badge (HLD 14 DoD / `events.py` `latency`) must
> be **measured client-side**: wrap the `await client.query(...)` in a `time.perf_counter()` span and
> publish that. `getattr(res, "time_taken_ms", None)` is kept only as an opportunistic override if a
> future SDK build adds it; the **client-side span is the real source**, and the field is hidden when
> the measurement is unavailable (e.g. the local-cosine path). *(Previous draft assumed
> `res.time_taken_ms` existed — it is not in the docs.)*

### 5.2 Index identity & the load-once cache

```python
def index_name(company_id: str) -> str:
    return f"clutch-{company_id}"                    # 00 §5 — the only naming rule
```

A process-lifetime cache keyed by `index_name`, guarded by an `asyncio.Lock` per index so two
concurrent first-touches don't double-load:

```python
class MossRetriever:
    def __init__(self, project_id, project_key, *, alpha, default_top_k):
        self._client = MossClient(project_id, project_key)
        self._loaded: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._alpha = alpha
        self._default_top_k = default_top_k

    async def load_index(self, company_id: str) -> str:
        name = index_name(company_id)
        if name in self._loaded:
            return name                              # idempotent hot path
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name not in self._loaded:             # double-checked
                await self._client.load_index(name)
                self._loaded.add(name)
        return name
```

**Why load-once matters (HLD 03 §3):** filters only bind on a **locally-loaded** index; a cloud query
logs-and-drops the filter. So `search()` always ensures the load first.

### 5.3 `search()` — hybrid query + product filter + Chunk mapping

```python
async def search(self, company_id, text, product_id, top_k) -> list[Chunk]:
    name = await self.load_index(company_id)
    opts = self._query_options(top_k or self._default_top_k, product_id)
    t0 = time.perf_counter()
    res = await self._client.query(name, text, opts)             # filter rides inside opts (§5.1)
    self.last_latency_ms = (time.perf_counter() - t0) * 1000.0   # client-side span (no result field)
    return [self._to_chunk(d) for d in (getattr(res, "docs", None) or [])]
```

- `_query_options(top_k, product_id)` builds the **whole** `QueryOptions(top_k=..., alpha=self._alpha,
  filter={"$and": [{"field": "product_id", "condition": {"$eq": product_id}}]} if product_id else None)`
  — the **single** place the SDK option shape lives.
- `_to_chunk(doc)` maps a Moss result → `Chunk`: pulls `id`, `text`, `metadata` (dict[str,str]),
  `score`, and mirrors `metadata.get("source","")` into `Chunk.source`. Defensive: coerce metadata
  values to `str`, default missing `section`/`source` to `""`.

### 5.4 Empty / weak-result widening (HLD 03 §6, row 3)

`search()` returns raw hits; the **widening policy** lives in the public `retrieve()` so it's backend-
agnostic:

1. Query as asked (with `product_id` filter, `alpha=0.7`).
2. If `[]` **or** every hit `< min_score`: re-query **company-wide** (drop `product_id`) and/or lower
   `alpha` (favor keyword/exact part+error strings).
3. Still empty → **return `[]`**. Never invent. The Agent (04) decides "not in docs" vs escalate.

This is policy, not data invention — widening only changes recall scope, never fabricates a `Chunk`.

---

## 6. Query construction (`query.py`, pure)

```python
def build_query_text(q: RetrievalQuery, problem: ProblemSignals | None = None) -> str:
    """Type/Talk: q.text. See: fold in ProblemSignals.to_query() (error_codes+indicators+parts+
    damage+summary). q.need[] hints (e.g. ['steps','warning']) are appended as soft keywords."""
    parts = [q.text or ""]
    if problem is not None:
        parts.append(problem.to_query())
    if q.need:
        parts.append(" ".join(q.need))
    return " ".join(p for p in parts if p).strip()
```

`retrieve_for(query)` = `build_query_text(query)` → `retrieve(query.company_id, text, query.product_id)`.

**See-path note (independence-preserving):** this layer does **not** import `perception/`. The
`IdentifyResult`/`ProblemSignals` are produced by Tonmoy (02); the **Agent** (04) turns them into a
`RetrievalQuery` (`text = problem.to_query()`, `product_id = identify.product_id`) before calling
`retrieve_for` (HLD 03 §5, S4). `query.py` accepts an optional `ProblemSignals` only for direct/test
use; the seam itself stays `RetrievalQuery`-only.

---

## 7. Grounded compose (`compose.py`) — the accuracy guarantee

The single most important behavior in the layer: **the answer is built only from retrieved chunks,
every claim cited, and it refuses rather than inventing** (HLD 03 §3, 00 §4).

### 7.1 Refusal gate (pure, runs before any LLM)

```python
def _passing(chunks, min_score) -> list[Chunk]:
    return [c for c in chunks if c.score is not None and c.score >= min_score]
```

If `not chunks` **or** `not _passing(chunks, min_score)` → return
`Answer(text="", citations=[])` immediately. The empty `text` is the **refusal flag** the Agent reads
to say "I don't see that in the docs" or escalate (04 decides; this layer never speaks). No LLM call
is spent on a refusal.

### 7.2 Citation extraction (pure)

```python
def to_citation(c: Chunk) -> dict:
    return {"doc": c.metadata.get("source", c.source),
            "section": c.metadata.get("section", ""),
            "score": c.score}
```

`Answer.citations` = one entry per chunk actually used. The chunk's `source`/`section` **is** the
proof (provenance-by-citation — HLD 03 §3), shown inline to the user.

### 7.3 The two composers (swapped at wiring time)

**`template_compose` (mock / fallback, no LLM)** — deterministic, used in CI, when no gateway, or if
the LLM call fails. Concatenates the top passing chunks' text (trimmed, de-duped by section) into a
grounded summary and attaches their citations. Guarantees the grounding invariant *by construction*:
the text literally **is** chunk text, so nothing can be invented.

**`gateway_compose` (real)** — calls `reason.compose` through the injected `gateway_client`
(OpenAI-compatible, exactly as `perception/providers/qwen_gateway.py` calls the gateway), with a
**strict grounding prompt**:

```
SYSTEM: You are Clutch's support composer. Answer the user's question USING ONLY the numbered
        sources below. Do not add facts not present in the sources. If the sources do not contain
        the answer, reply exactly with: NOT_IN_DOCS. Cite each sentence with the [n] of the source
        it came from.
USER:   Question: {answer_query}
        Sources:
        [1] (source={source}, section={section}) {chunk.text}
        [2] ...
```

- Decoding params: `temperature=0`, `response_format` plain text (cite markers parsed out).
- **Post-LLM verification (defense-in-depth):** if the model returns `NOT_IN_DOCS` → refusal
  `Answer("", [])`. Otherwise map the `[n]` markers back to the passing chunks → `citations`. If the
  model emits a sentence with **no** citation marker, that sentence is dropped (un-cited sentences are
  not emitted — HLD 03 §3). If *nothing* survives, fall back to `template_compose` (never invent).
- The model is reached **only** by the logical name `reason.compose`; swapping MiniMax→any
  OpenAI-compatible model is a gateway/config change, never an edit here (00 §7).

### 7.4 The LLM adapter (how the gateway arrives)

`make_retrieval(cfg, gateway_client)` builds a tiny async caller and binds it into `gateway_compose`:

```python
def make_retrieval(cfg, gateway_client=None):
    retriever = MossRetriever(cfg.moss_project_id, cfg.moss_project_key,
                              alpha=cfg.retrieval_alpha, default_top_k=cfg.retrieval_top_k)
    fallback  = LocalCosineRetriever(...)            # built lazily on first Moss failure

    async def _retrieve_for(q: RetrievalQuery) -> list[Chunk]:
        return await retrieve(q.company_id, build_query_text(q), q.product_id,
                              top_k=cfg.retrieval_top_k, _primary=retriever, _fallback=fallback)

    if gateway_client is None:
        return _retrieve_for, template_compose       # mock-first: no LLM
    model = "reason.compose"                          # logical name -> gateway routes it (S5)
    def _compose(answer_query, chunks, *, min_score=cfg.retrieval_min_score):
        return gateway_compose(answer_query, chunks, client=gateway_client,
                               model=model, min_score=min_score)
    return _retrieve_for, _compose
```

No `openai` import in this module — the client is **passed in** (the only place an OpenAI-compatible
client is constructed for the gateway is Arham's `gateway.py`, per S5). Mirrors the lazy-import
discipline in `qwen_gateway.py` but pushes it across the seam.

---

## 8. Local-cosine fallback (`local_cosine.py`)

Same `Retriever` Protocol, **no network**, so it doubles as the build-against mock (10 §5).

- **Corpus:** a small fixture of `Chunk`s + precomputed embedding vectors for the `clutch-demo`
  company, shipped under `tests/retrieval/fixtures/` (or generated once and cached). For the mock
  path, a handful of canned chunks is enough; for the *real* fallback, the same chunk embeddings Moss
  was built from.
- **`search()`:** embed the query (a small local model or the gateway `embed` logical name if
  available; for the pure mock, a deterministic bag-of-words vector), cosine vs the corpus, apply the
  `product_id` filter in Python (`metadata["product_id"] == product_id` when set), sort desc, return
  top-k as `Chunk`s with `score` set to the cosine value (so `min_score` still applies uniformly).
- **No `time_taken_ms`** → the latency event is hidden when missing (HLD 14 DoD: "hidden if missing").

Dependency: `numpy` for the cosine (add to root deps — §9.3). The pure mock can avoid numpy with a
tiny hand-rolled dot/norm to keep CI dependency-free; numpy only needed for the real fallback over
many vectors.

---

## 9. Config / Spine changes needed (the blockers & knobs)

### 9.1 `src/contracts.py` — **BLOCKER (Spine PR, all-hands per 10 §3)**

`contracts.py` currently defines `CatalogEntry`, `ProblemSignals`, `Candidate`, `IdentifyResult`
only. The retrieval layer **cannot compile** without three more shapes that 00 §3 already specifies
but that are missing from the file:

- `Chunk` (id, text, metadata, score, source)
- `RetrievalQuery` (company_id, text, product_id, need)
- `Answer` (text, citations)

Also missing (used by other Limbs, same PR): `Company`, `SupportSession`, `Modality`. **Action:** add
all of these to `contracts.py` in one Spine PR before Phase 1. They are data shapes only (no logic),
exactly as 00 §3 lists them. *This is the single hard prerequisite for `src/retrieval/` to exist.*

### 9.2 `src/config.py` — **role-named keys (currently empty)**

`config.py` is empty; the layer needs role-named (never vendor-named — 00 §7, 10 §8) knobs:

```python
# retrieval knobs (role names, not vendor names)
retrieval_alpha:     float = 0.70      # hybrid semantic<->keyword blend (HLD 03 §3)
retrieval_top_k:     int   = 5         # default top-k (Talk may tighten — open Q §10)
retrieval_min_score: float = 0.35      # refusal floor (HLD 03 §3)
reason_model:        str   = "reason.compose"   # LOGICAL gateway name, not "minimax_*"
# index backend creds (read from env; see 9.4)
moss_project_id:     str
moss_project_key:    str
```

Adding config keys is a Spine-adjacent PR Arham reviews (10 §8) — coordinate so two sections don't
invent conflicting keys. **Naming rule:** `reason_model`, `retrieval_alpha`, etc. — **never**
`minimax_*` / `moss_*` in the role layer (vendor identity lives in the gateway/env, not the config
role names).

### 9.3 `pyproject.toml` (root) — **dependencies**

Root deps today: `openai`, `pillow`, `python-dotenv`. Retrieval adds:

```toml
"moss>=1.1.1",     # the Moss SDK (already pinned this way in ingest/pyproject.toml)
"numpy>=1.26",     # local-cosine fallback math (real fallback over many vectors)
```

`openai` is already present (the gateway client is OpenAI-compatible, but it's **passed in**, so
strictly the retrieval Limb itself needs only `moss` + `numpy`). `pytest`/`pytest-asyncio` already in
the dev group and `asyncio_mode = "auto"` is set — the async tests need no extra config.

### 9.4 Environment / secrets

This layer's API keys and the **persistable, shared-between-users** env configuration are specified in
their own sections: **§13 (API keys)** and **§14 (environment variables — committed `.env.example` +
gitignored `.env.local`)**. In short: `MossRetriever` needs `MOSS_PROJECT_ID` + `MOSS_PROJECT_KEY` for
the `--live` path only; the gateway key is **not** this Limb's (it rides the injected `gateway_client`,
S5); and the mock/CI path needs **no env at all**.

### 9.5 `src/events.py` (shared, not owned here)

Empty today. Retrieval **produces** the real `res.time_taken_ms` that the `latency` event carries, but
the event schema is the Spine/realtime concern (05). Retrieval's only obligation: surface
`last_latency_ms` (or `None`) so `realtime/` can populate the event, and **hide it when `None`**
(HLD 14 DoD). No schema work belongs in `src/retrieval/`.

---

## 10. Open questions / verification items (carried from HLD 03 §8, 14 §8)

1. **Moss `QueryOptions` — RESOLVED** (verified against [docs.moss.dev](https://docs.moss.dev/docs/reference/sdk),
   §5.1). `top_k`, `alpha` (1=semantic/0=keyword, default 0.8), and `filter` are **all fields of
   `QueryOptions`**; `filter` is **not** a separate `query()` arg. Result `docs` carry `metadata` →
   citations are safe. **Remaining caveat:** the result exposes **no `time_taken_ms`** — latency is
   measured **client-side** (§5.1, §5.3); confirm with one `--live` run that the span is sane (<10 ms
   warm). *(Earlier draft's uncertainty about kwarg names + a result latency field is now closed.)*
2. **`min_score` floor + `alpha` per modality.** Voice ("Talk") answers should be short — possibly a
   tighter `top_k` and/or higher `min_score` for spoken vs typed. Knobs already in config; values TBD.
3. **Per-company single index + `product_id` filter** vs per-product indexes. Default (per 00 §9):
   per-company, product-filtered. This LLD assumes that default.
4. **`product_id` in chunk metadata.** The chunker (01) currently emits
   `{source, section, page, segment_count, doc_index}` — **no `product_id`**. For the product filter
   to bind, onboarding (01) must add `product_id` to chunk metadata at ingest. **Cross-section
   dependency to flag to Arham** (this layer reads it; it cannot add it). Until then, retrieval runs
   company-wide (filter is a no-op), which is the documented graceful path (HLD 03 §6, row 3).

---

## 11. Unit tests — **bare minimum only** (everything behavioral lives in `scripts/`, §12)

Section-wide convention (mirrors LLD [05-realtime §9](05-realtime.md) "the script *is* the test" and
[05-speech §8](05-speech-layer.md) "Tier-1 selection tests only"): **the e2e gate is the script
(`scripts/retrieval_mini.py --check`, §12).** The `tests/` suite is deliberately cut to the **bare
minimum** — *only* the pure, dependency-free invariants that (a) are the safety guarantee and (b) are
cheaper to assert as a unit than to read off an e2e run. They import only `retrieval` + `contracts`,
touch **no** Moss, keys, or network, and are green on a bare clone.

`tests/retrieval/test_grounding_unit.py` — the **entire** unit suite, three cases:

| Test (pure) | Asserts | Why it stays a unit, not folded into §12 |
|---|---|---|
| `test_refusal_gate` | empty chunks **and** all-below-`min_score` → `Answer.is_refusal`; **no LLM call** spent on a refusal | the accuracy guarantee (00 §4); a pure branch, fastest to pin here |
| `test_grounding_invariant` | `template_compose` text ⊆ chunk text (nothing invented); `to_citation` maps `metadata{source,section}`+`score` → `{doc,section,score}` | the "never improvises" invariant; pure, no backend |
| `test_query_build` | `build_query_text` folds `text` + `ProblemSignals.to_query()` + `need[]` | pure string assembly |

Everything else — real `retrieve` over the fixture corpus, **product-filter binding, widening,
local-cosine fallback, load-once, the `gateway_compose` path, latency** — is exercised **only** by
`scripts/retrieval_mini.py --check` (§12), **not** duplicated in `tests/`. There is **no live-Moss
pytest lane** and **no `unittest.mock`-heavy integration test** in `tests/` (those moved to the script,
where the stand-ins are real working code). The full DoD acceptance gate is the script's `--check`
(§12.4).

---

## 12. End-to-end test — `scripts/retrieval_mini.py` **is** the whole e2e suite

**This is the single e2e test for the Limb** (the section convention, LLD 05-realtime §9). It is the
standalone runnable harness 10 §6 mandates per section: one command drives the **whole read+ground
path** the way the Agent will, with **mini stand-ins** for every neighbour, asserts the HLD-03 behavior,
prints evidence, and **exits non-zero on any failure**. It is the thing you run to answer *"does Section
D work, by itself, right now?"* — and the only behavioral test there is (§11 holds just three pure units).

### 12.1 What "e2e for one subfolder" means here

The harness imports **only** `src/retrieval/` (the real code under test) + `src/contracts.py` (Spine).
Every neighbour across a seam is a **mini layer** — the smallest possible real-shaped stand-in — so the
full flow runs with no other section present and no external service:

| Neighbour (seam) | Mini layer the harness supplies | Why it's enough |
|---|---|---|
| **Agent (04, S4)** — the caller | `mini_agent`: a ~15-line driver that builds a `RetrievalQuery`, calls `retrieve_for`, then `compose`, and prints the `Answer` + citations | Exercises the exact two callables the real Agent will call; proves the seam shape. |
| **Gateway (06, S5)** — compose LLM | `mini_gateway`: a fake OpenAI-compatible client whose `chat.completions.create` returns a scripted, **cited** completion (and a `NOT_IN_DOCS` variant on demand) | Drives the **real** `gateway_compose` path (prompt build + `[n]`→citation mapping + refusal) without keys or network. |
| **Perception (02, S3/S8)** — See input | `canned_identify`: returns a fixed `IdentifyResult{product_id, problem}` | Lets the harness build the See-path `RetrievalQuery` (product-filtered, problem-driven) exactly as the Agent would. |
| **Moss index (01)** — the corpus | `clutch-demo` **fixture corpus**: a handful of canned `Chunk`s (printer manual snippets) tagged with `product_id`, served by `LocalCosineRetriever` | Real `Chunk` shape + real ranking/filter/score path with zero infra. |

> **Mini, not mock.** A *mock* returns a canned `Answer`; a *mini layer* runs real-shaped logic over
> the seam so the harness tests the **wiring + behavior**, not just a stub. The mini_gateway actually
> goes through `gateway_compose`; the fixture corpus actually goes through cosine ranking + `min_score`.

### 12.2 Run modes (one script, escalating fidelity)

The same script runs at three fidelity levels, picked by flags/env — so it is CI-safe by default and
becomes a real integration smoke test when creds exist:

| Mode | Invocation | Retrieve backend | Compose backend | Needs |
|---|---|---|---|---|
| **mock** (default, CI) | `uv run python -m scripts.retrieval_mini --check` | `LocalCosineRetriever` over the fixture corpus | `template_compose` | nothing — no keys, no network |
| **gateway** | `... --check --real-compose` | `LocalCosineRetriever` | real `gateway_compose` via **mini_gateway** | still nothing (gateway is faked) |
| **live** | `... --check --live` (or `MOSS_PROJECT_ID/KEY` set) | **real `MossRetriever`** against `clutch-demo` | `template_compose` (or `--real-compose` for a real gateway client if `gateway.py` is wired) | Moss creds (§13); opt-in, skipped in CI |

A bare `... --query "how do I clear a jam"` (no `--check`) runs one turn and prints the `Answer` +
chunks — the **dev run-loop**; `--check` runs the full scenario battery (§12.3) as the **acceptance
gate**. Same code path, mirroring LLD 05-realtime §9.2.

`--live` is the one place real infra is touched; it mirrors `ingest/tests/test_live.py`'s opt-in
discipline — **keyless ⇒ `SKIP`, not fail**: if `--live` is requested without `MOSS_PROJECT_ID/KEY`
(§13), the live scenarios print `SKIP (no creds)` and the run still exits 0, so the default and CI runs
never flake on the network (same rule as LLD 05-realtime §9.5).

### 12.3 The scenarios it runs (each prints PASS/FAIL + the evidence)

Every scenario runs the **full** `RetrievalQuery → retrieve_for → compose → Answer` chain through the
mini_agent, and asserts the HLD-03 behavior, printing the chunks/citations/latency so a human can eyeball it:

1. **Type/Talk — grounded answer.** `RetrievalQuery(company_id="demo", text="printer shows error 13")`
   → non-empty `Chunk[]`, scored ≥ `min_score`; `Answer.text` non-empty; **every** citation resolves
   to a returned chunk (`{doc, section, score}`). *Asserts: grounding + citation discipline.*
2. **See — product-filtered, problem-driven.** `canned_identify` → product `lj-m404` + a paper-jam
   `ProblemSignals`; Agent builds `RetrievalQuery(text=problem.to_query(), product_id="lj-m404")`
   → results all carry `product_id == lj-m404`. *Asserts: filter binds + problem→query fold (§6).*
3. **Refusal — not in docs.** query for something absent → `retrieve_for` returns `[]` or all-below-
   floor → `compose` returns `Answer.is_refusal` (empty text, no citations); the mini_agent prints the
   "I don't see that in the docs" branch. *Asserts: never invents (00 §4).*
4. **Fallback — Moss down.** force the primary retriever to raise (a `--break-moss` flag / injected
   stub) → results still served by `LocalCosineRetriever`, identical `Chunk[]` shape. *Asserts: 03 §6.*
5. **Widen — weak first pass.** a filtered query that returns nothing under the `product_id` filter →
   harness shows the company-wide re-query surfacing the right doc. *Asserts: widening policy (§5.4).*
6. **load-once.** two concurrent first-touches of the same company → underlying `load_index` invoked
   once (counter on the stub/real client). *Asserts: the async lock (§5.2).*
7. **gateway compose path** *(--real-compose)*. mini_gateway returns a cited completion → `[n]` markers
   mapped to citations; then its `NOT_IN_DOCS` variant → refusal; then an un-cited sentence → dropped.
   *Asserts: §7.3 post-LLM verification.*
8. **latency surfacing.** `last_latency_ms` is a real number in `--live`, `None` on the fixture path →
   harness prints the badge value or "(hidden)". *Asserts: HLD 14 DoD "hidden if missing".*

### 12.4 The `--check` gate — output & exit code

`--check` is the **bare-minimum acceptance gate** (mirrors LLD 05-realtime §9.3): it runs every
scenario in §12.3, prints one line per check (`✓ scenario — one-line evidence` / `✗ scenario — what
failed + the Answer/chunks dump`), a final `N/N passed`, and **exits non-zero on any failure** — so it
drops straight into CI as Section D's gate. `--verbose` dumps the full retrieved `Chunk[]`, composed
`Answer.text`, and citations per scenario (the demo view). These checks cover every retrieval/grounding
DoD row in [14 §5](../hld/engineers/14-fardin-inference-retrieval-voice.md): company-scoped
product-filtered `retrieve`, grounded cited `compose` with refusal, local-cosine fallback, and
swap-by-config — so there is no separate DoD checklist to maintain in `tests/`.

### 12.5 Layout & wiring (independence preserved) — files in `scripts/`

```
scripts/
├── retrieval_mini.py        # THE e2e test (§12): boots the Limb + mini neighbours; `--check` is the gate
└── _retrieval_harness.py    # the mini stand-ins: mini_agent (S4), mini_gateway (S5), canned_identify
                             #   (S3/S8), and the clutch-demo fixture corpus — real-shaped, harness-only
```

- `scripts/` is at the **repo root** (the same dir LLD 05-realtime uses for `realtime_mini.py` /
  `_harness.py`) — **not** inside any Limb, since these are harnesses, not shipped code. The script puts
  `src/` on `sys.path` (matching `pyproject.toml` `pythonpath=["src"]`) and imports **only**
  `from retrieval import …` + `from contracts import …`.
- The mini layers live in `scripts/_retrieval_harness.py` (distinct from realtime's `scripts/_harness.py`
  so the two e2e scripts don't collide), **not** in `src/retrieval/` — the shipped Limb stays free of
  harness scaffolding and the no-sibling-import rule (10 §8) is never bent. (`local_cosine` /
  `template_compose` stay **in** the Limb — they are the real fallback path, not harness-only.)
- Because the script imports only the Limb + Spine, running `retrieval_mini.py --check` green with no
  other `src/` subfolder importable **is** the proof Section D is "done + tested" alone (10 §6).

### 12.6 Relationship to the three unit tests (§11)

Not redundant and not overlapping: §11 is **three pure invariants** (refusal, grounding, query-build —
the safety guarantee, green on a bare clone); §12 is **all behavior** (retrieve/filter/widen/fallback/
load-once/gateway/latency — the acceptance gate). The script owns the fixture corpus + `mini_gateway`;
the unit tests construct their own tiny `Chunk` literals, so there's no shared-fixture coupling. A
convenience target (`uv run python -m scripts.retrieval_mini --check`, optionally wrapped as a
`pyproject` script / `make e2e-retrieval`) is the one command CI and humans run.

---

## 13. API keys (what this Limb authenticates with)

This Limb has **two** authenticated edges, both opt-in — the mock/CI path (`--check` default) needs
**no key at all**. See the section-wide operator guide for the copy-paste `.env.example` and the
per-piece run commands: [`docs/clutch/lld/README.md` §2.1](README.md#21-retrieval--grounding-srcretrieval--lld-03).

| Edge | Key(s) | Used by | Required for | Source |
|---|---|---|---|---|
| **Moss index** | `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY` | `MossRetriever` (`index.py`) | the `--live` path only (real `retrieve`) | Moss portal (project id + key) |
| **Compose LLM** | `TRUEFOUNDRY_BASE_URL`, `TRUEFOUNDRY_API_KEY` | the **injected** `gateway_client` — *not* this Limb directly | `--real-compose` with a real gateway client | TrueFoundry gateway (HLD 06) |

- **The gateway key is not this Limb's.** `gateway_compose` reaches the model only through the
  passed-in `gateway_client` (S5, §7.4) — `retrieval/` imports no `openai`/vendor SDK and constructs
  no client. The TrueFoundry creds are listed here only so an operator wiring `--real-compose` against
  a *real* gateway knows what the injected client needs; the same pair backs perception's vision call
  (`qwen_gateway.py`), so it is set once for the whole repo.
- **Mock/CI path needs nothing.** `--check` (default) uses `LocalCosineRetriever` + `template_compose`
  — no Moss, no gateway, no network. Keyless ⇒ the `--live` scenarios print `SKIP (no creds)` and the
  run still exits 0 (§12.2).
- **Keys are env-only.** They are read lazily inside `MossRetriever`/the gateway client, **never**
  placed in `src/config.py` (config names roles, not vendors — 00 §7). Full env table in §14.

## 14. Environment variables (persistable, shared-between-users config)

Two files, the standard split (committed template + gitignored secrets), both at the repo root:

- **`.env.example`** — **committed**. The complete list of keys with safe placeholder/default values,
  so any contributor sees exactly what to set. The retrieval rows are below; the whole-repo file lives
  at [`/.env.example`](../../../.env.example).
- **`.env.local`** — **gitignored** (`.gitignore` ignores `.env` / `.env.local`). Each user copies the
  example (`cp .env.example .env.local`) and fills in their own secrets; loaded via `python-dotenv`
  (already a root dep).

| Var | Required? | Default | Purpose |
|---|---|---|---|
| `MOSS_PROJECT_ID` | only for `--live` | — | Moss project id for `MossRetriever` |
| `MOSS_PROJECT_KEY` | only for `--live` | — | Moss project key |
| `TRUEFOUNDRY_BASE_URL` | only for `--real-compose` (real client) | — | gateway `/openai` endpoint the injected client targets |
| `TRUEFOUNDRY_API_KEY` | only for `--real-compose` (real client) | — | gateway auth |

> **Role knobs are not env vars.** `retrieval_alpha` / `retrieval_top_k` / `retrieval_min_score` /
> `reason_model` live in `src/config.py` (§9.2), not `.env` — they are roles/tuning, not secrets.
> Only vendor identity + secrets go in the environment.

---

*Confirmation: this LLD specifies `src/retrieval/` in isolation — a Moss-backed hybrid `retrieve` with
load-once caching + product filter, a grounded cited `compose` with hard refusal, and a local-cosine
fallback/mock — importing only the Spine and receiving `gateway_client` by injection. Blocking
prerequisites called out: add `Chunk`/`RetrievalQuery`/`Answer` to `contracts.py` (§9.1), populate
role-named `config.py` keys (§9.2), add `moss`+`numpy` to root deps (§9.3), and (cross-section) have
onboarding stamp `product_id` into chunk metadata (§10.4). Moss API verified against
[docs.moss.dev](https://docs.moss.dev/docs/reference/sdk): `top_k`/`alpha`/`filter` are all
`QueryOptions` fields and result `docs` carry `metadata` (citations safe); the lone correction is that
the result has no `time_taken_ms`, so latency is measured client-side (§5.1/§5.3/§10.1). §12 specifies
the standalone e2e harness
(`scripts/retrieval_mini.py`) that exercises the full read+ground path with **mini** stand-ins for
the Agent/Gateway/Perception/Moss neighbours, in three escalating fidelity modes (mock → gateway →
live), as Section D's independent acceptance gate (10 §6).*
