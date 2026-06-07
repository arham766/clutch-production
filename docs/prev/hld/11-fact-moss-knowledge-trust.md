# Fixalong HLD 11 — FACT × Moss: the Knowledge & Trust Layer

**Status:** Draft v0.1 · **Build tier:** 0 (Moss) / 1 (FACT) · **Phase:** Flow 2 of 3 — *routed by [HLD 13 Session Orchestrator](13-session-orchestrator.md)*
**Sponsors:** Moss · FACT (our IP)
**Write side:** seconds, once per model (called by **HLD 10**) · **Read side:** <10 ms, every turn (called by **HLD 12**)
**Reuses:** `fact/` (52 tests), `ingest/fact_moss.py`, `ingest/ingest.py` (all built)

> **Self-contained.** This is the shared layer between onboarding (which *writes* into it) and
> the live loop (which *reads* from it). It restates the schemas it uses. It does **not** cover
> manual acquisition (HLD 10) or what to *do* with a verdict / how to speak (HLD 12). It also
> does **not** include TrueFoundry output guardrails — those stay in the live loop (HLD 12/06);
> this layer is strictly **Moss (what's the fact?) + FACT (can I trust it?)**.

---

## 1. Purpose

Be the repair brain's **memory + conscience**:

| Concern | Tech | Question | Latency |
|---|---|---|---|
| Knowledge | **Moss** | *"What's the relevant fact for this state?"* | <10 ms |
| Trust | **FACT** | *"Can I prove this fact is genuine, unaltered, and fresh?"* | <1 ms |

Two sides of one layer:
- **WRITE (cold):** sign chunks (FACT envelopes) → index in Moss → cache per model. Called once
  per model by HLD 10.
- **READ (hot):** retrieve by repair state (Moss) → verify each chunk offline (FACT) → return a
  **verdict**. Called on every turn by HLD 12.

## 2. Scope
**In:** signing chunks, Moss indexing, per-model caching, retrieval (state→query, prefetch),
offline verification → `Verdict`. **Out:** producing chunks (HLD 10), guardrailing/speaking
the result (HLD 12), TrueFoundry MCP/guardrails (HLD 10/12/06).

## 3. The two sides

```
   WRITE (cold, once per model — from HLD 10)         READ (hot, every turn — from HLD 12)
   chunks{id,text,metadata}                            RepairState
        │                                                   │ build query (state→query)
        ▼ FACT.sign each chunk                              ▼
   text == claim.content ; sign_originator               Moss.query  ── <10 ms ──▶ Chunk[]
        │ attestation → metadata (envelope)                 │
        ▼                                                   ▼ FACT.verify each (offline <1 ms)
   Moss.create_index("fixalong-<model>")                 Verdict per chunk:
        │ register in cache                                 ACT | HEDGE | ESCALATE | REFUSE
        ▼                                                   │ (drop REFUSE, flag others)
   cached, reused by all sessions ───────────────────────▶ verified Chunk[] → HLD 12
```

## 4. The core trick — the chunk text *is* the claim

No duplication. A Moss document already has `text`; that text **is** the FACT claim:
```
claim.content  ==  doc.text                     # NFC-normalized chunk text
claim_hash     ==  sha256(canonicalize(text))   # integrity binds to that exact text
```
Only the **attestation** (signature, hashes, trust) goes into Moss **metadata** (string-only,
which Moss requires). At read time the envelope is reconstructed from `text` + `metadata` and
verified. (Built & verified: `ingest/fact_moss.py`, `fact/`.)

---

## 5. WRITE — signing (the FACT envelope)

**Goal:** make every chunk provable, so the live loop can *refuse* a fabricated/tampered fact.

```python
# fact_moss.sign_chunk → fact.sign_originator (built)
env = sign_originator(
    content      = nfc(chunk.text),                   # == doc.text (the claim)
    content_type = "fact://types/manual/chunk/v1",
    asserts      = "extracted_from",                  # provenance: from this manual
    evidence     = {"source": model, "section": meta["section"],
                    "page": meta["page"], "method": "unsiloed-parse"},
    trust        = Trust(tier, coverage, fresh_until_in_seconds=ttl),  # tier per risk (§5.2)
    signing_key  = KB_KEY,                            # the knowledge base's identity
)
chunk.metadata |= envelope_to_metadata(env)           # flatten attestation → metadata
```

### 5.1 What lands in Moss metadata (`envelope_to_metadata`)
| key | meaning |
|---|---|
| `fact_envelope` | full envelope JSON, **content stripped** (lossless; rehydrated at read) |
| `fact_issuer` | the KB's `did:key` — the live loop trusts exactly this |
| `fact_actionability` | `ACT`/`VERIFY`/`ESCALATE` (also a Moss filter) |
| `fact_fresh_until` | RFC3339 freshness bound |

### 5.2 Trust-tier policy (write-time)
Map manual risk → tier, so the live loop knows how confidently to speak:
- routine step → `ACT` (speak plainly), long `fresh_until`
- safety-critical / electrical / "must verify" → `VERIFY` (live loop hedges) or `ESCALATE`
- firmware / service-bulletin content → short `fresh_until` (goes stale → HEDGE downstream)

### 5.3 Keys & identity (demo scope)
One local **KB signing key** (`load_kb_key` — base64 env/file), identity = **`did:key`**
(offline, no key server). **The same key signs the index and is trusted by the live loop**
(`trusted_issuers={KB_ISSUER}`) — persist it; regenerating it makes everything read ESCALATE.

## 6. WRITE — Moss indexing & per-model caching

- **Index name = `fixalong-<model_slug>`** (the cache key). `MossClient.create_index` with
  batched upsert for large manuals (built: `ingest.inject_into_moss`).
- **Cache / registry:** `index_exists(model_slug)` (HLD 10 calls this) = `list_indexes()`
  contains `fixalong-<model_slug>` (or a small KV). Hit → onboarding short-circuits.
- **Reuse:** every live session for that model **loads** the existing index — no re-parse, no
  re-sign. Onboarding cost is amortized across all repairs of that model (the scale property).
- **Refresh:** re-onboard on manual-version change or `fresh_until` lapse (rare for static
  manuals; relevant for firmware/bulletins).

---

## 7. READ — retrieval (Moss, <10 ms)

**Goal:** given the live `RepairState`, return the right chunks instantly.

- **Session load:** at session start, `load_index("fixalong-<model>")` → in-process, ~3–5 ms
  queries. (Per-model template; sessions read it; live observations may be upserted back.)
- **State → query:** build a hybrid query from state (restated `RetrievalQuery` below).
  `alpha≈0.6–0.8` for colloquial phrasing; metadata filters narrow scope (`model`, `node_id`,
  `type`, `risk_level`).
- **Vision-driven prefetch:** on each gated visual change, prefetch for what's in view so
  speech hits a warm cache (drives the "it already knows what you're looking at" feel in HLD 12).
- **Multi-index / safety sweep:** optionally run a parallel `risk_level=high` query near the
  current part so warnings are never missed.

```jsonc
RetrievalQuery {                          // built by HLD 12 from RepairState
  "model":"laserjet-pro-m404", "current_node_id":"jam_rear_open",
  "user_observation":"shiny roller", "visible_parts":["pickup roller"],
  "need":["part_identity","warning","next_step"]
}
Chunk {                                   // returned (verdict attached in §8)
  "id":"...", "type":"step|warning|part|error|diagram|branch",
  "text":"...", "part":"pickup roller", "risk_level":"low|medium|high",
  "source":{"section":"3.4","page":15}, "score":0.86,
  "verdict":"ACT|HEDGE|ESCALATE|REFUSE"
}
```

## 8. READ — verification (the FACT gate, offline <1 ms)

**Goal:** before any chunk is usable, prove it's genuine, from the trusted issuer, and fresh.

```python
# fact_moss.verify_document (built) — reconstruct envelope from text+metadata, verify offline
v = verify_document(chunk.text, chunk.metadata, trusted_issuers={KB_ISSUER})
```

| Verdict | Condition | Meaning to HLD 12 |
|---|---|---|
| **ACT** | signed by KB key, fresh, tier ACT | ground truth — usable |
| **HEDGE** | stale, or tier VERIFY | usable but "let me confirm…" |
| **ESCALATE** | valid signature but **issuer not trusted**, or tier ESCALATE | don't speak; flag/human |
| **REFUSE** | unsigned / tampered (hash or signature fails) | **drop the chunk** |

### 8.1 Security model (why verify is meaningful)
Two checks: **integrity** (recompute `claim_hash` from `text`, check Ed25519 signature) +
**issuer policy** (`trusted_issuers={KB_ISSUER}`). With `did:key` the key is in the envelope →
no key server, but anyone can mint a key — so the issuer policy is what makes a signature
*mean* "from our manual."

| Situation | Verdict | Why |
|---|---|---|
| chunk added without `fact_envelope` | REFUSE | unsigned |
| stored chunk text edited after signing | REFUSE | `HASH_MISMATCH` |
| chunk signed with an attacker's key | ESCALATE | integrity ok, issuer ∉ trusted set |
| legit chunk past `fresh_until` | HEDGE | `stale` |
| legit, trusted, fresh | ACT | all checks pass |

> A normal RAG layer would serve all five. This one returns a verdict the live loop acts on —
> the agent **can't speak a fact it can't prove came from the real manual**.

## 9. Data models (restated)
- **Signed chunk** = Moss doc: `text` (the claim) + metadata (`fact_envelope`, `fact_issuer`,
  `fact_actionability`, `fact_fresh_until`, plus repair fields from HLD 10).
- `RetrievalQuery`, `Chunk` (§7); `Verdict` ∈ {ACT, HEDGE, ESCALATE, REFUSE} (§8).

## 10. Latency budget
| Op | Side | Budget |
|---|---|---|
| sign one chunk | write | sub-ms (Ed25519) — but whole-manual sign is offline |
| `create_index` | write | seconds (offline, once per model) |
| `load_index` | read | ~3–5 ms, once at session start |
| `query` (in-process) | read | **<10 ms** |
| `verify_document` per chunk | read | **<1 ms**, offline |

## 11. Interfaces
```python
# WRITE (called by HLD 10)
sign_chunks(chunks, kb_key, *, tier, coverage, ttl) -> list[Chunk]   # fact_moss (built)
inject_into_moss(chunks, "fixalong-<model>") -> index_name           # ingest (built)
index_exists(model_slug) -> bool                                     # cache/registry

# READ (called by HLD 12)
load_session_index("fixalong-<model>") -> index_name
retrieve(state, *, obs=None) -> list[Chunk]   # build query + Moss.query (+ vision-driven prefetch)
verify_document(text, metadata, trusted_issuers, clock=None) -> Verdict   # fact_moss (built)
# NOTE: runtime conversation recall is in-memory RepairState (LLD 05 §5.6.5), NOT Moss.
# remember() is Tier-2 OPTIONAL → separate ephemeral session-<id> index, never fixalong-<model>.
```

## 12. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Moss cloud unreachable | local in-memory cosine over the same embeddings (same `retrieve` interface) |
| empty / low-confidence retrieval | widen filter / lower `alpha` / node-level chunk; never invent (REFUSE empties) |
| FACT not wired at write time | index unsigned → read returns REFUSE/degraded; live loop loses the trust gate |
| KB key mismatch (signed ≠ trusted) | everything reads ESCALATE → pin & persist the one key |
| live-upsert latency (Tier-2 `remember` only) | debounce-batch into the ephemeral `session-<id>` index; runtime recall is in-memory `RepairState` regardless |

## 13. Sponsor mapping
| Side | Sponsor | Role |
|---|---|---|
| knowledge | Moss | sub-10 ms index + retrieval, per-model, cached |
| trust | FACT (ours) | sign (write) + offline verify → verdict (read) |

## 14. Open questions
- Per-model index vs one shared index filtered by `model` metadata (default: per-model, cached).
- Embedding model (`moss-minilm` vs `moss-mediumlm`) for terse part names.
- Registry store for `index_exists` (Moss `list_indexes` vs KV).
- Trust-tier derivation: hand-mapped vs from Unsiloed extraction confidence.
