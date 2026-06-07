# Clutch LLD 03-LOCAL — On-Device (On-Prem Edge) Retrieval & Grounding

**Status:** Draft v0.3 · **Read after** [LLD 03 — Retrieval & Grounding](03-retrieval-grounding.md) and
[HLD 00 §7 — model-portability](../hld/00-overview.md). This is a **variant backend of Section D's
retrieval Limb, not a fork**: it adds one retrieval backend, one durable store, one compose-endpoint
resolver, and a config-keyed registry. Every caller (Agent S4, `realtime/`, widget) and every existing
backend (`MossRetriever`, `LocalCosineRetriever`) is **unchanged**. The acceptance gate is the same
Limb script, run in a new mode: **`scripts/retrieval_mini.py --mode on_device --check`** (§11).

---

## 0. Purpose & scope

Run Clutch retrieval **entirely on a company-owned edge box**. The proprietary corpus and its **Moss
embeddings are built and held on that box and never pushed to Moss cloud**. At query time, embedding +
search run **in-process, offline**. The only corpus-derived egress is the retrieved top-k chunk *text*
sent to an LLM for compose — a **local LLM API when one is configured/healthy, else the cloud gateway**.

**Deployment assumptions (decided):**
- **Only retrieval/Moss is local.** **Realtime (LiveKit transport), voice (Cartesia STT/TTS), and
  vision (Qwen) run via API** — so this LLD's confidentiality guarantee covers the **document corpus
  only** (§2).
- **Index updates are admin-triggered** — an explicit operator action, no watch-folder (§6).

**In scope:** the `OnDeviceMossRetriever` backend, the `LocalIndexStore`, the config-keyed registry,
the compose-endpoint resolver (local-LLM-first), the Local-vs-Cloud decision rule, and the on-device
e2e gate. **Out of scope:** vision/`identify`, STT/TTS, LiveKit transport (own LLDs; orthogonal).

---

## 1. Where it slots in — file manifest

One backend behind the existing `Retriever` protocol that already governs `MossRetriever` (cloud) and
`LocalCosineRetriever` (fallback). Selection becomes a **config read into a registry table** (§4.1), so
adding/switching a path is data, not branch code.

| File | Status | Owner | What |
|---|---|---|---|
| `src/retrieval/base.py` | unchanged | Fardin | `Retriever` protocol — the seam every backend satisfies |
| `src/retrieval/index.py` | unchanged | Fardin | `MossRetriever` (cloud) |
| `src/retrieval/local_cosine.py` | unchanged | Fardin | `LocalCosineRetriever` (keyless fallback) |
| `src/retrieval/query.py` | unchanged | Fardin | `build_query_text` + widening — **reused verbatim** |
| **`src/retrieval/on_device.py`** | **NEW** | Fardin | `OnDeviceMossRetriever` |
| **`src/retrieval/local_store.py`** | **NEW** | Fardin | `LocalIndexStore` (durable disk artifact + boot) |
| **`src/retrieval/registry.py`** | **NEW** | Fardin | `RETRIEVERS` / `REASON_ENDPOINTS` config-keyed tables |
| `src/retrieval/compose.py` | edited | Fardin | `resolve_reason_endpoint()` + `local_llm_healthy()` |
| `src/retrieval/__init__.py` | edited | Fardin | `make_retrieval()` becomes a registry lookup |
| `src/config.py` | edited | Arham | role keys: `retrieval_mode`, `local_index_path`, `embed_model`, `reason_endpoint`, `local_reason_base_url` |
| `src/app.py` | edited | Arham | one wiring line — backend per `Company.retrieval_mode` |
| `src/api/` (admin route) | edited | Arham | `POST /admin/onboard` → triggers `LocalIndexStore.upsert` |
| `scripts/retrieval_mini.py` | edited | Fardin | `--mode on_device` run mode + on-device scenarios (§11) |
| `scripts/_retrieval_harness.py` | edited | Fardin | `mini_local_llm`, `egress_spy`, fixture `LocalIndexStore` builder |

**Seam impact:**

| Seam | Change |
|---|---|
| **S4 Agent⇄Retrieval** (`retrieve_for`, `compose`) | **Signatures unchanged.** Backend differs by config. |
| **S5 ⇄Gateway** (`reason.compose`) | **Extended:** the logical name resolves to a **local LLM base URL** when configured, else the TrueFoundry gateway. |
| **S7 Onboarding⇒Runtime** (`Company{…}`) | **Re-pointed:** onboarding writes a **local index artifact + catalog on disk**; `Company` carries `local_index_path` + `retrieval_mode`. |

---

## 2. Deployment topology (company edge box; realtime + vision = API)

```
        COMPANY EDGE BOX (on-prem)                         API / CLOUD (gateway)
  ┌──────────────────────────────────────────┐
  │ proprietary docs ─► onboarding (admin-     │       ┌─ LiveKit transport (realtime)
  │   triggered): parse ─► chunk ─► embed       │       ├─ Cartesia STT/TTS (voice I/O)
  │   (moss-minilm, ON-DEVICE)                  │       ├─ Qwen-VL (vision identify)
  │        ▼                                    │       └─ MiniMax LLM (reason.compose)
  │  LOCAL INDEX STORE                          │              ▲ (local LLM preferred
  │   chunks.jsonl + catalog.json (durable)     │              │  if present, else this)
  │   + in-memory Moss session                  │              │
  │        ▲ retrieve()  in-process, offline    │              │ top-k chunk TEXT only
  │  Agent + compose() ──────────────────────────┼──────────────┘
  └─────────────────────────────────────────────┘
   frames ─► Qwen API · audio ─► Cartesia API · transport ─► LiveKit API   (accepted egress)
```

**Boundary statement (the governance contract):**
- **Never leaves the box:** the full document corpus + all Moss vectors + the retrieval/compose
  orchestration.
- **Accepted egress (by the §0 decision):** (a) retrieved **chunk text** → LLM API; (b) **camera
  frames** → vision API; (c) **audio** → STT/TTS API; (d) transport via LiveKit.
- The proprietary-corpus confidentiality guarantee is therefore **independent** of frame/audio
  handling — those are governed by LLD 02/05. If a tenant later requires frames/audio to *also* stay on
  the box, that escalates 02/05 to on-box and is a new LLD (§14).

---

## 3. Durable persistence decision (the crux)

Verified against the Moss SDK: the **Python `SessionIndex` is in-memory only** (`add_docs` / `query` /
`push_index`, **no save/load to a file path**). Durable on-disk persistence is documented for the
**JS/WASM/Electron** runtime ("index persisted to the user data directory," offline, optional background
sync). The edge box makes the index durable via one of:

| Option | How | Verdict |
|---|---|---|
| **A. Derived index (recommended)** | Durable artifact = `chunks.jsonl` + `catalog.json`; the in-memory Moss session is **rebuilt at boot** by re-embedding on-device (fast, local). | **Default** — matches the Python SDK reality; the corpus-on-disk is the proprietary asset, the index is reproducible. |
| **B. WASM/Node sidecar** | Run the local index in the Moss JS runtime that persists to disk; Python calls it over a localhost loopback. | Use only if boot-time re-embedding is too slow for the corpus size. |
| **C. cachePath load** | Treat the box as a cache of a cloud index (`load_index` + `cachePath`). | **Rejected** — implies a canonical cloud copy, violating "never pushed." |

**Chosen: Option A.** `LocalIndexStore` (§4.3) owns the disk artifact and `build_session()`.

---

## 4. Component design

### 4.1 Config-keyed registry (selection is data, not branch code)

The mode is a **string read from config and looked up in a table** — no `if/elif` selection logic, so a
new backend is one registry entry + a config value, never an edit to `make_retrieval`.

```python
# src/retrieval/registry.py  (NEW) — the table IS the config surface
RETRIEVERS = {
    "cloud":     lambda cfg: MossRetriever(cfg),
    "on_device": lambda cfg: OnDeviceMossRetriever(
                                 LocalIndexStore(cfg.local_index_path, embed_model=cfg.embed_model), cfg),
    "local":     lambda cfg: LocalCosineRetriever(),
}
REASON_ENDPOINTS = {
    "local":   lambda cfg, gw: openai_compat(cfg.local_reason_base_url),
    "gateway": lambda cfg, gw: gw,
    "auto":    lambda cfg, gw: openai_compat(cfg.local_reason_base_url) if local_llm_healthy(cfg) else gw,
}
```

```python
# src/retrieval/__init__.py  (edited) — read config → lookup, no branching
def make_retrieval(cfg, gateway_client=None):
    if cfg.retrieval_mode not in RETRIEVERS:                       # fail loud, not KeyError
        raise ConfigError(f"retrieval_mode={cfg.retrieval_mode!r} not in {list(RETRIEVERS)}")
    retriever = RETRIEVERS[cfg.retrieval_mode](cfg)
    resolve   = REASON_ENDPOINTS[cfg.reason_endpoint]
    async def retrieve_for(query): ...          # same closure for any backend
    async def compose(q, chunks):  ...          # uses resolve(cfg, gateway_client)
    return retrieve_for, compose
```

`src/app.py` (Arham) stays a single line — `make_retrieval(cfg.for_company(company), gateway)` — and the
mode rides on the `Company` record. Adding a 4th backend touches **only** `registry.py` + a config value
(matches HLD 00 §7: "config names roles, not vendors" — now the *path selection itself* is config-driven).

### 4.2 `OnDeviceMossRetriever` (behind `Retriever`)

Duck-types the `Retriever` protocol (`base.py`); same contract as `MossRetriever`, in-process & offline.

```python
class OnDeviceMossRetriever:
    def __init__(self, store, cfg):
        self._store, self._cfg = store, cfg
        self._session = None; self._lock = asyncio.Lock()

    async def _ensure_session(self):                 # load-once, mirrors MossRetriever (§5.2 of LLD 03)
        async with self._lock:
            if self._session is None:
                self._session = await self._store.build_session()

    async def retrieve(self, company_id, text, product_id, *, top_k, min_score):
        await self._ensure_session()
        t0 = time.perf_counter()
        hits = await self._session.query(text, QueryOptions(top_k=top_k,
                    alpha=self._cfg.retrieval_alpha, filter=product_filter(product_id)))
        chunks = [to_chunk(h) for h in hits]         # same defensive mapper as index.py
        chunks = widen_if_weak(chunks, ...)          # REUSES query.py: product → company-wide → []
        stamp_latency(chunks, time.perf_counter() - t0)   # client-side span (no network)
        return chunks                                # NO cloud read; NO push
```

### 4.3 `LocalIndexStore` (durable disk artifact, §3-A)

```python
class LocalIndexStore:
    def __init__(self, path, *, embed_model): ...    # path/: chunks.jsonl, catalog.json, manifest.json
    async def build_session(self):                   # boot: stream chunks.jsonl → add_docs (on-device embed)
        ...                                          # returns a warm Moss session; NEVER push_index
    async def upsert(self, chunks, catalog_delta):   # admin onboarding (§6)
        ...                                          # append (atomic temp+rename) to disk + live session; bump manifest
```

- `manifest.json` carries `{version, embed_model, doc_hashes}` so `upsert` re-embeds **only changed
  docs** (incremental).
- Atomic write-temp-then-rename so a crash mid-onboard cannot corrupt the durable artifact.

### 4.4 Compose endpoint resolver (local-LLM-first)

The refusal gate and citation extraction in `compose.py` are **pure and unchanged**. Only endpoint
resolution is new (driven by the `REASON_ENDPOINTS` table, §4.1):

```python
def local_llm_healthy(cfg) -> bool:
    return bool(cfg.local_reason_base_url) and _ping(cfg.local_reason_base_url)   # cheap probe

async def compose(query, chunks, *, cfg, gateway_client, min_score):
    if not passing(chunks, min_score):
        return Answer(text="", citations=[])                    # refusal — unchanged (LLD 03 §7.1)
    client = REASON_ENDPOINTS[cfg.reason_endpoint](cfg, gateway_client)
    if client is None:
        return template_compose(query, chunks)                  # deterministic, ZERO egress
    return await gateway_compose(query, chunks, client=client)  # post-verifies [n] citations — unchanged
```

Honors **"if we have a LOCAL API we must use it":** `local` = local-only (down ⇒ template, zero egress);
`auto` = local-first, gateway fallback; `gateway` = cloud only. The `[n]`→citation post-verification is
identical for local and cloud LLMs — grounding is enforced regardless of endpoint.

---

## 5. Config (role-named) & precedence

| Key | Role | Default |
|---|---|---|
| `retrieval_mode` | `cloud` \| `on_device` \| `local` | `cloud` |
| `local_index_path` | durable artifact dir | `./clutch-local/<company>` |
| `embed_model` | on-device embedder role | `moss-minilm` (`moss-mediumlm` = higher accuracy) |
| `reason_endpoint` | `auto` (local-first) \| `local` \| `gateway` | `auto` |
| `local_reason_base_url` | OpenAI-compatible local LLM URL | unset |
| `retrieval_alpha` / `retrieval_top_k` / `retrieval_min_score` | reused from LLD 03 §9.2 | 0.70 / 5 / 0.35 |

Precedence (most specific wins, all **reads** — no code path):
```
Company.retrieval_mode  (per-tenant, set at onboarding)   ▸ overrides
   env CLUTCH_RETRIEVAL_MODE  (per-deployment default)    ▸ overrides
      built-in default "cloud"
```
`cfg.real("retrieval")`: `on_device` is "real" when `local_index_path` exists and a session boots.
Unknown `retrieval_mode` / `reason_endpoint` values fail loud at startup (§4.1) — a typo is a boot
error, not a silent wrong-path pick (mirrors LLD 03 §6 / README §6).

---

## 6. Onboarding-on-device (admin-triggered)

```
admin action (console "Re-index" / CLI / authenticated POST /admin/onboard)   ← DECIDED: explicit, no watch-folder
  → parse (Unsiloed adapter or local parser behind the parse_doc seam)
  → chunk (reuse ingest/ chunker; metadata = {source, section, page, …})
  → LocalIndexStore.upsert(chunks, catalog_delta)
        ├─ append chunks.jsonl            (durable, atomic)
        ├─ embed on-device + add_docs to the live session   (NEVER push_index)
        └─ derive/append catalog.json     (closed-set product ids; ref images optional)
  → ready: retrieve() serves in-process
```

No cloud index is created. The `Company` (S7) carries `local_index_path` instead of a cloud
`index_name`. `manifest.json` doc-hashes make the upsert incremental. A future watch-folder remains
possible behind the same `upsert`, but is **not** in scope.

---

## 7. Failure modes & fallbacks (HLD §6 discipline)

| Failure | Behavior |
|---|---|
| Local index missing/corrupt at boot | Rebuild session from `chunks.jsonl`; if that's gone → surface "onboarding required" (no silent empty answers). |
| On-device embed model unavailable | Hard fail at boot with a clear message; does **not** fall back to cloud (would breach the boundary). |
| Local LLM down (`reason_endpoint=local`) | Fall back to `template_compose` → grounded, **zero egress**. (`auto` may fall back to gateway.) |
| Retrieval empty / below floor | Existing refusal gate: `Answer.is_refusal` → Agent refuses/escalates (00 §4). |
| Gateway egress disallowed by policy | Set `reason_endpoint=local`; no local LLM → `template_compose` (answerable-or-refuse, nothing leaves). |

---

## 8. Known gotchas (verified against Moss docs)

1. **`MossClient` requires project credentials at init** even local-only — so this is
   **"data-never-leaves," not true air-gap**. Confirm whether Moss offers an offline / credential-less
   local mode; else document the one-time auth handshake as an accepted exception.
2. **Python session = in-memory only** → §3-A (derived index) is mandatory unless the §3-B sidecar is
   adopted.
3. **The retrieved chunks reach the LLM API** by design. For excerpt-level confidentiality use
   `reason_endpoint=local` (local LLM) or `template_compose` (fully offline grounding).

---

## 9. How to decide Local vs Cloud Moss

**The decision is per-company, made at onboarding, stored on the `Company` record as `retrieval_mode`**
(§5). Because both backends sit behind the same `Retriever` protocol, switching is a config flip +
re-onboard — never a migration.

### 9.1 Criteria

| Factor | Points to **LOCAL** (`on_device`) | Points to **CLOUD** (`cloud`) |
|---|---|---|
| **Data sensitivity** | Corpus is proprietary/regulated; must not be stored in Moss cloud | Public / non-sensitive manuals |
| **Compliance / residency** | Data-residency, contractual, or near-air-gap mandate | None |
| **Corpus size vs box** | Fits edge-box RAM; boot re-embed acceptable | Very large / fast-growing; wants elastic scale |
| **Update cadence** | Infrequent, admin-triggered (§6) | Frequent edits, many editors, wants managed sync |
| **Latency** | Wants zero-network in-process retrieval | Network retrieval acceptable |
| **Ops capability** | Company can run/patch an edge box | Wants zero-ops managed service |
| **Tenancy** | Single company per box | Many tenants, central multi-tenancy |
| **Availability** | Must work offline / intermittent link | Always-connected |

### 9.2 Decision tree (apply in order)

```
1. Does a confidentiality / residency rule forbid the corpus living in Moss cloud?
      └─ YES → LOCAL (on_device).     [hard gate — overrides everything below]
2. Else, do you need managed scale, many tenants, frequent multi-editor updates, or zero ops?
      └─ YES → CLOUD.
3. Else (default) → CLOUD — lower-ops and today's wired default.
```

**Rule of thumb:** *confidentiality forces the choice; everything else is a convenience trade-off, and
convenience favors Cloud.* LOCAL is the **enterprise/compliance tier**; CLOUD is the **default/self-serve
tier**. One Clutch deployment serves both at once (`app.py` selects per `Company.retrieval_mode`); a
tenant can start CLOUD and flip to LOCAL later by re-onboarding, callers untouched. (`local` cosine is
**not** a third deployment — it's the keyless dev/fallback path.)

---

## 10. Unit tests — **bare minimum only** (everything behavioral lives in `scripts/`, §11)

Section-wide convention (mirrors [LLD 03 §11](03-retrieval-grounding.md), [05-realtime §9], [05-speech
§8]): **the e2e gate is the script** (`scripts/retrieval_mini.py --mode on_device --check`, §11). The
`tests/` suite is cut to the **pure, dependency-free invariants** unique to the LOCAL path that are
cheaper to pin as a unit than to read off an e2e run. They import only `retrieval` + `contracts`, touch
**no** Moss/keys/network, and are green on a bare clone.

`tests/retrieval/test_on_device_unit.py` — three cases:

| Test (pure) | Asserts | Why a unit, not folded into §11 |
|---|---|---|
| `test_registry_dispatch` | `RETRIEVERS["on_device"]` builds `OnDeviceMossRetriever`; unknown mode → `ConfigError` (not `KeyError`) | the config-keyed selection contract (§4.1); a pure table lookup |
| `test_endpoint_resolution` | `REASON_ENDPOINTS`: `local`→local client, `auto`+healthy→local, `auto`+down→gateway, missing→template (None) | the local-LLM-first rule (§4.4); pure branch over a stubbed `local_llm_healthy` |
| `test_no_push_contract` | a `LocalIndexStore.upsert` over tiny `Chunk` literals never calls `push_index` (spy) | the "data stays local" invariant (§0); pure, no Moss |

Everything else — real on-device `retrieve`, boot-from-disk, durable artifact reload, egress spying,
incremental upsert, the local-LLM compose path, latency — is exercised **only** by §11, not duplicated
here. There is **no live-Moss pytest lane**.

---

## 11. End-to-end test — `scripts/retrieval_mini.py --mode on_device` **is** the on-device gate

The on-device path is a **new run mode of the same Limb script** (LLD 03 §12), not a second script —
"one standalone harness per Limb" (10 §6) is preserved. One command drives the **whole local
build→retrieve→ground path** the way the Agent will, with **mini stand-ins** for every neighbour,
asserts the LOCAL-specific behavior, prints evidence, and **exits non-zero on any failure**.

### 11.1 What "e2e for the on-device backend" means here

The harness imports **only** `src/retrieval/` + `src/contracts.py` (Spine). Each neighbour across a seam
is a **mini layer** (smallest real-shaped stand-in), so the full flow runs with no other section present:

| Neighbour (seam) | Mini layer | Why it's enough |
|---|---|---|
| **Agent (S4)** — the caller | `mini_agent` (reused from `_retrieval_harness`): builds a `RetrievalQuery`, calls `retrieve_for` then `compose`, prints `Answer` + citations | exercises the exact two callables the real Agent calls; backend-agnostic |
| **Local Moss index (01, on-device)** | `LocalIndexStore` over the **`clutch-demo` fixture corpus**; with `moss` installed it does a real on-device embed, else a `local_cosine` stand-in fills the session | real disk artifact + real boot path with zero infra |
| **Local LLM (S5, local)** | `mini_local_llm`: a fake OpenAI-compatible client on `local_reason_base_url` returning a scripted **cited** completion (+ a `NOT_IN_DOCS` variant) | drives the **real** `gateway_compose` path against a local endpoint without a model |
| **Gateway (S5, fallback)** | `mini_gateway` (reused) | proves the `auto` local→gateway fallback |
| **Onboarding/admin (S7)** | the harness calls `LocalIndexStore.upsert` directly (the admin route is Arham's) | exercises the incremental upsert + no-push contract |
| **egress_spy** (cross-cutting) | wraps every outbound client; records payloads | proves **only chunk text** leaves and the corpus/vectors never do |

> **Mini, not mock.** The `mini_local_llm` actually goes through `gateway_compose`; the
> `LocalIndexStore` actually writes `chunks.jsonl` and boots a session — the harness tests **wiring +
> behavior + the egress boundary**, not stubs.

### 11.2 Run modes (one script, escalating fidelity)

| Mode | Invocation | Retrieve backend | Compose backend | Needs |
|---|---|---|---|---|
| **on_device mock** (default, CI) | `uv run python -m scripts.retrieval_mini --mode on_device --check` | `OnDeviceMossRetriever` over a fixture `LocalIndexStore` (local_cosine fill if `moss` absent) | `template_compose` | nothing — no keys, no network |
| **local-compose** | `... --mode on_device --check --local-compose` | same | real `gateway_compose` via **mini_local_llm** | nothing (the local LLM is faked on loopback) |
| **live** | `... --mode on_device --check --live` (or `MOSS_PROJECT_ID/KEY` set) | **real** on-device Moss session (real on-device embed) | `template_compose` (or `--local-compose`) | Moss creds (§12) + the embed model; opt-in, skipped in CI |

A bare `... --mode on_device --query "how do I clear a jam"` runs one turn and prints the `Answer` +
chunks — the **dev run-loop**; `--check` runs the full battery (§11.3) as the **acceptance gate**.
**Keyless ⇒ SKIP, not fail:** `--live` without `MOSS_PROJECT_ID/KEY` prints `SKIP (no creds)` and the run
still exits 0 (same rule as LLD 03 §12.2 / 05-realtime §9.5).

### 11.3 The scenarios it runs (each prints PASS/FAIL + evidence)

LOCAL-specific behavior, on top of the cloud Limb's already-covered ranking/filter/refuse battery
(§11.5):

1. **Boot from disk (derived index, §3-A).** Build a `LocalIndexStore` from the fixture → drop the live
   session → construct a **fresh** `OnDeviceMossRetriever` (simulated restart) → `retrieve` still
   returns the seeded chunks. *Asserts: durability via the disk artifact, not in-memory state.*
2. **No-push invariant (the core guarantee).** Through a whole onboard→retrieve→compose run,
   `push_index` is **never** called (spy). *Asserts: the corpus/embeddings never leave the box (§0).*
3. **Egress boundary.** `egress_spy` records every outbound byte; with `--local-compose`, the **only**
   outbound call hits `local_reason_base_url` and its payload is **chunk text only** (no full corpus, no
   vectors). With default `template_compose`, **zero** outbound calls. *Asserts: §2 boundary statement.*
4. **Local-LLM-first resolution.** `reason_endpoint=auto` + healthy `mini_local_llm` → compose hits the
   local endpoint; kill the local stub → falls back to `mini_gateway`; unset both → `template_compose`.
   *Asserts: §4.4 / "use the LOCAL API if present."*
5. **Admin upsert is incremental.** `upsert` the same doc twice → `manifest.json` hash-diff re-embeds
   **0** docs the second time; a changed doc re-embeds **1**. *Asserts: §6 incremental onboarding.*
6. **Registry dispatch + fail-loud.** `retrieval_mode="on_device"` → `make_retrieval` returns the
   on-device backend; a bogus mode → `ConfigError`. *Asserts: §4.1 config-keyed selection.*
7. **Grounding parity.** A grounded query returns non-empty `Chunk[]` ≥ `min_score`; every citation
   resolves to a returned chunk; an absent query → `Answer.is_refusal`. *Asserts: the grounding
   guarantee holds identically on the local backend (00 §4).*
8. **latency surfacing.** `last_latency_ms` is a real client-side number on the live path, `None` on the
   fixture fill → harness prints the badge value or "(hidden)". *Asserts: HLD 14 DoD "hidden if missing".*

### 11.4 The `--check` gate — output & exit code

`--check` runs every §11.3 scenario, prints one line each (`✓ scenario — evidence` / `✗ scenario — what
failed + the Answer/chunks/egress dump`), a final `N/N passed`, and **exits non-zero on any failure** —
so it drops straight into CI as the on-device gate alongside the cloud `--check`. `--verbose` dumps the
retrieved `Chunk[]`, composed `Answer.text`, citations, **and the egress_spy log** per scenario (the
governance view — proof of what left the box).

### 11.5 Relationship to the cloud harness & the units (§10)

Not redundant: the **cloud** `--check` (LLD 03 §12) already covers retrieve/filter/widen/fallback/
load-once/gateway over `MossRetriever`+`LocalCosineRetriever`; `--mode on_device` adds **only the LOCAL
deltas** — boot-from-disk, no-push, egress boundary, local-LLM-first, incremental upsert, registry
dispatch. §10 holds three pure invariants (registry, endpoint resolution, no-push) green on a bare clone;
§11 is all behavior + the egress boundary. The harness owns the fixture `LocalIndexStore` + `mini_local_llm`
+ `egress_spy`; the units build their own tiny literals, so there's no shared-fixture coupling.

### 11.6 Layout & wiring (independence preserved) — files in `scripts/`

```
scripts/
├── retrieval_mini.py        # THE e2e test (LLD 03 §12 + this §11): `--mode on_device --check` is the on-device gate
└── _retrieval_harness.py    # mini stand-ins: mini_agent (S4, reused), mini_local_llm (S5 local), mini_gateway
                             #   (S5 fallback, reused), egress_spy (boundary), fixture LocalIndexStore builder
```

`scripts/` is at the repo root (not inside the Limb — these are harnesses, not shipped code). The script
puts `src/` on `sys.path` and imports **only** `from retrieval import …` + `from contracts import …`.
The shipped LOCAL code (`on_device.py`, `local_store.py`, `registry.py`) stays **in** the Limb — they are
the real path, not harness scaffolding — so the no-sibling-import rule (10 §8) is never bent. Running
`--mode on_device --check` green with no other `src/` subfolder importable **is** the proof the on-device
backend is "done + tested" alone (10 §6).

---

## 12. API keys (what this backend authenticates with)

The mock/CI path (`--mode on_device --check` default) needs **no key**. Two opt-in authenticated edges:

| Edge | Key(s) | Used by | Required for | Source |
|---|---|---|---|---|
| **Moss (on-device)** | `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY` | `LocalIndexStore` / `OnDeviceMossRetriever` | the `--live` path (real on-device session); see §8 gotcha 1 | Moss portal |
| **Local LLM** | `LOCAL_REASON_BASE_URL` (no key, or a local key) | the local OpenAI-compat client (S5 local) | `--local-compose` against a real local model | the box's own served model |
| **Gateway (fallback)** | `TRUEFOUNDRY_BASE_URL`, `TRUEFOUNDRY_API_KEY` | the **injected** `gateway_client` — *not* this backend directly | `reason_endpoint` ∈ `{auto,gateway}` with a real gateway | TrueFoundry (HLD 06) |

- **The compose LLM is never reached directly** — local or gateway, it rides the resolver (§4.4); the
  backend imports no vendor SDK.
- **Mock/CI path needs nothing** — fixture `LocalIndexStore` + `template_compose`, no Moss/LLM/network.
- **Keys are env-only**, read lazily; **never** placed in `src/config.py` (config names roles, not
  vendors — 00 §7).

---

## 13. Environment variables (additions for the LOCAL path)

Appended to the repo-root [`.env.example`](../../../.env.example) (committed template; `.env.local` is
gitignored):

```dotenv
# ── On-device retrieval (LLD 03-LOCAL) ────────────────────────────────────
CLUTCH_RETRIEVAL_MODE=cloud          # cloud | on_device | local  (per-box default; Company can override)
CLUTCH_REASON_ENDPOINT=auto          # auto (local-first) | local | gateway
CLUTCH_LOCAL_INDEX_PATH=./clutch-local   # dir for chunks.jsonl + catalog.json + manifest.json
CLUTCH_EMBED_MODEL=moss-minilm       # on-device embedder (alt: moss-mediumlm)
LOCAL_REASON_BASE_URL=               # OpenAI-compatible local LLM URL (e.g. http://localhost:8000/v1)
MOSS_PROJECT_ID=                     # still required at MossClient init even local-only (§8 gotcha 1)
MOSS_PROJECT_KEY=
```

---

## 14. Open questions (remaining)

- **Single-tenant per box** assumed; multi-tenant-on-one-box would need per-company `LocalIndexStore`
  isolation — confirm if ever needed.
- **Local LLM choice** behind `reason.compose` (quantized model, GPU sizing) — an ops decision, not a
  code change.
- **Frame/audio confidentiality** — if a LOCAL tenant later requires frames/audio to *also* stay on the
  box, that escalates 02/05 to on-box and is a new LLD (flagged in §2, not solved here).
- **Moss offline licensing** — whether a credential-less / air-gapped MossClient init exists (§8
  gotcha 1) determines if true air-gap is achievable or stays "data-never-leaves."
