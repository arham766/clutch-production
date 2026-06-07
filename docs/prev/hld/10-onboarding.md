# Fixalong HLD 10 — Onboarding & Manual Acquisition (the cold path)

**Status:** Draft v0.1 · **Build tier:** 0/1 · **Phase:** Flow 1 of 3 — *routed by [HLD 13 Session Orchestrator](13-session-orchestrator.md)* (cold, per-model)
**Sponsors:** Qwen · TrueFoundry (MCP) · Unsiloed
**Runs:** once per machine **model** · **Latency:** seconds (NOT the live path)
**Produces:** repair-graph **chunks** → handed to **HLD 11 (FACT × Moss)** for sign + index
**Reuses:** `ingest/unsiloed_client.py`, `ingest/chunker.py` (built)

> **Self-contained:** restates the schemas it uses. Persistence (signing + indexing +
> caching) is deliberately **not** here — it lives in **HLD 11 (FACT × Moss)**. The live
> retrieval/voice loop is **HLD 12**.

---

## 1. Purpose

Turn *"a phone pointed at an unknown broken machine"* into *"the structured repair graph for
that exact model, ready to be signed and indexed."* This is the **cold path**: it can take
seconds and runs **once per model**. Its output (repair-graph chunks) is handed to HLD 11,
which signs + indexes + caches them so every future live session (HLD 12) reuses the result.

```
"what is this machine?"            (HLD 10)                    (HLD 11)            (HLD 12)
   camera  ──▶ identify + fetch manual + structure  ──chunks──▶ sign + index ──▶ live retrieval
```

The three flows, explicitly:

| Flow | Doc | What | Latency | Frequency |
|---|---|---|---|---|
| **1 — Onboarding** | ✅ HLD 10 | identify → fetch manual → parse → repair-graph chunks | seconds | once per **model** |
| 2 — Knowledge & Trust | HLD 11 | **FACT-sign + Moss-index + cache** (write) / retrieve + verify (read) | sec (write) / <10 ms (read) | write once · read always |
| 3 — Live loop | HLD 12 | camera → state → retrieve (verified) → speak | <10 ms | every session |

## 2. Scope

**In:** product/model identification (vision), manual acquisition via the TrueFoundry MCP
gateway, Unsiloed parsing, repair-graph construction → chunks.
**Out:** FACT signing, Moss indexing, caching, retrieval, verification — **all HLD 11**. The
live loop — **HLD 12**. The only thing HLD 10 emits is `(ProductIdentity, model, list[Chunk])`.

## 3. End-to-end sequence

```
        ┌──────────────── ONBOARDING (cold, once per model) — HLD 10 ─────────────────┐
        │                                                                            │
 camera │ (1) Qwen: identify machine + OCR the model number  → ProductIdentity       │
        │            │  HLD 11 cache: index for model exists? ──yes──▶ DONE (reuse)   │
        │            ▼ no                                                             │
        │ (2) agent calls find_manual(model)                                         │
        │      └─ discovered + routed + auth'd via TrueFoundry MCP Gateway           │
        │      └─ MCP server fetches manual (mfr site / enterprise store) → ManualRef │
        │            ▼                                                                │
        │ (3) Unsiloed /parse (submit → poll)  → structured segments (md/json)       │
        │            ▼                                                                │
        │ (4) repair-graph builder → RepairNodes → fine-grained chunks{id,text,meta} │
        └────────────────────────────────────────────────────────────────────────────┘
                                   │  emits (ProductIdentity, model, chunks)
                                   ▼
                       HLD 11 — FACT × Moss   (sign + index + cache)
```

---

## 4. Step 1 — Visual product & model identification

**Goal:** resolve *what machine this is* — ideally the exact model — from the camera.

- **Prefer OCR of the model label** over appearance inference. Reading "HP LaserJet Pro
  M404" off the sticker beats guessing from shape. Prompt Qwen to *"find and read any
  model/part number on a label."*
- **Fallbacks (in order):** OCR label → appearance inference (brand/type) → **ask the user**
  ("what's the model? usually a sticker on the back") → manual text entry.
- **Confidence gating:** below threshold → confirm with the user before fetching (fetching the
  wrong manual is worse than asking).

**Output — `ProductIdentity`:**
```jsonc
{
  "object_type": "printer",
  "brand": "HP",
  "model": "LaserJet Pro M404",
  "model_source": "ocr_label | inference | user",
  "confidence": 0.91,
  "raw_text": "HP LaserJet Pro M404dn"      // raw OCR, for auditing
}
```

**Cache short-circuit:** normalize `model` → `model_slug`; ask HLD 11 whether an index for
`model_slug` already exists. If yes → **skip the rest of onboarding** and go straight to HLD 12.
(The common case in production; the registry/cache is owned by HLD 11.)

## 5. Step 2 — Manual acquisition via TrueFoundry MCP

**Goal:** get the authoritative manual for `model`, through a governed tool layer.

- The agent does **not** hold a hardcoded tool list or hit arbitrary servers. It **discovers**
  available tools at runtime from the **TrueFoundry MCP Gateway** (policy-scoped to the
  session's identity/environment) and calls `find_manual`.
- **TrueFoundry is the gateway/governance, not the manual store.** The MCP **server behind**
  the gateway performs the lookup (manufacturer support site, enterprise repository, or web
  search) and returns a reference. Every call is auth'd (OAuth) and logged.
- Scoping example: an enterprise tech's session discovers the customer's **private** manual
  server; a consumer session only sees public sources.

**Tool contract:**
```jsonc
// discover_tools(context) → [ToolSpec...]                (TrueFoundry MCP gateway)
// call: find_manual({ brand, model, object_type }) → ManualRef
ManualRef {
  "model": "LaserJet Pro M404",
  "title": "HP LaserJet Pro M404 User Guide",
  "uri": "https://.../m404-userguide.pdf",   // or an enterprise file handle
  "source": "manufacturer | enterprise | web",
  "mime": "application/pdf",
  "confidence": 0.88
}
```

**If multiple/ambiguous:** prefer `manufacturer` > `enterprise` > `web`; pick the
service/repair guide over the brochure; on a tie, ask the user.

## 6. Step 3 — Parsing with Unsiloed

**Goal:** turn the messy manual (tables, diagrams, warnings, multi-column, scans) into clean,
structured content. Generic OCR is insufficient.

- Submit `ManualRef` (file or URL) to Unsiloed `POST /parse` → `job_id`; **poll**
  `GET /parse/{job_id}` until `Succeeded` (async; seconds–tens of seconds).
- Output: `chunks[].segments[]` with `segment_type` (Title/Text/Table/Picture/Caption/…),
  `content`, `markdown`, `page_number`, `bbox`, `confidence`.
- *(Built: `ingest/unsiloed_client.py` — submit+poll implemented.)*

> **Demo note:** Unsiloed runs in onboarding (offline), so its latency never touches the live
> loop. Pre-run it for the demo model; for a new machine it runs live behind a "getting the
> manual…" state.

## 7. Step 4 — Repair-graph build → chunks

**Goal:** turn flat segments into an **executable repair graph**, then into fine-grained
**chunks** that HLD 11 will sign + index.

Heuristics over segments → `RepairNode`s:
- numbered steps / section headers → node boundaries + `instructions`
- "Warning"/"Caution" callouts → `warnings` (+ `risk_level`, `hazard`)
- error-code tables → `error_codes`
- diagram captions + labels → `diagrams` (+ `parts`)
- cross-refs ("if jam persists, see…") → `next[]` / `conditions[]` edges
- part glossary / exploded-view labels → `parts[]` + **aliases** (colloquial → canonical, e.g.
  "black rubber wheel" → "pickup roller" — boosts recall on user language in HLD 12)

**`RepairNode` (restated):**
```jsonc
RepairNode {
  "id":"jam_rear_panel",
  "trigger_phrases":["paper jam","already removed paper","still says jam"],
  "preconditions":["jam persists after paper removal"],
  "instructions":["Open the rear access panel.","Check the lower paper path."],
  "warnings":["Do not touch the fuser assembly; it can be hot."],
  "parts":["rear access panel","pickup roller","transfer roller","paper sensor"],
  "hazards":[{"type":"hot_surface","part":"fuser"}],
  "conditions":["torn fragment visible","roller looks glossy"],
  "next":["jam_fragment_removal","roller_cleaning"],
  "source":{"section":"3.2","page":14}
}
```

Each node decomposes into **fine-grained chunks** (one per instruction/warning/part/error/
diagram) so retrieval (HLD 12) is precise. The chunk shape handed to HLD 11:

```jsonc
Chunk {
  "id":"m404-jam_rear_panel-warn-0",
  "text":"Do not touch the fuser assembly; it can be hot.",   // becomes the FACT claim in HLD 11
  "metadata": {            // all string-valued (Moss requirement)
    "type":"warning", "node_id":"jam_rear_panel", "part":"fuser",
    "risk_level":"high", "hazard":"hot_surface", "symptom":"paper jam",
    "model":"laserjet-pro-m404", "section":"3.2", "page":"14"
  }
}
```

> **Pragmatic stance:** run Unsiloed for real, but **hand-curate the small repair graph** for
> the demo model. Show the automatic path in architecture; don't depend on perfect auto-graph
> extraction in 24 h.

## 8. Output & handoff

Onboarding emits **`(ProductIdentity, model_slug, list[Chunk])`** and calls HLD 11 to persist:
`fact_moss.sign_chunks(...)` → `ingest.inject_into_moss("fixalong-<model_slug>", ...)`. HLD 11
owns the trust tier, the KB key, the index name, and the cache. After that, HLD 12 can serve
live sessions for the model.

## 9. Data models produced
- `ProductIdentity` (§4) — what machine this is.
- `ManualRef` (§5) — where its manual is.
- `RepairNode` + fine-grained `Chunk[]` (§7) — the executable graph, ready to sign+index.

## 10. Latency & when it runs
Seconds end-to-end (Unsiloed dominates); **never** on the live hot path. Demo: pre-onboard the
printer; optionally show live onboarding of a 2nd machine behind a progress state.

## 11. Interfaces
```python
identify_product(frames) -> ProductIdentity                 # Qwen + OCR (perception, HLD 04)
index_exists(model_slug) -> bool                            # delegated to HLD 11 cache/registry
find_manual(brand, model, object_type) -> ManualRef         # via TrueFoundry MCP gateway
parse_manual(manual_ref) -> UnsiloedResult                  # ingest/unsiloed_client (built)
build_repair_graph(unsiloed_result, model) -> RepairGraph   # repair-graph builder
graph_to_chunks(graph) -> list[Chunk]                       # reuses chunker patterns (built)
onboard(frames) -> (ProductIdentity, model_slug, list[Chunk])  # then → HLD 11.persist(...)
```

## 12. Failure modes & fallbacks
| Step | Failure | Fallback |
|---|---|---|
| 1 ID | can't read model | appearance inference → ask user → manual entry |
| 1 ID | wrong-model risk (low conf) | confirm with user before fetching |
| 2 MCP | gateway/tool down | direct manufacturer search; or user pastes manual URL/file |
| 2 MCP | no manual found | ask user for the manual; or a generic same-class manual (flagged) |
| 3 Unsiloed | parse slow/errors | pre-parsed JSON for known models (demo); retry/backoff |
| 4 Graph | noisy extraction | hand-authored repair graph for the demo model |

## 13. Sponsor mapping
| Step | Sponsor | Role |
|---|---|---|
| 1 | Qwen | read the model number / identify the machine |
| 2 | TrueFoundry | **MCP discovery + governed `find_manual`** |
| 3 | Unsiloed | manual → structured segments |

## 14. Open questions
- Cache lookup contract with HLD 11 (`index_exists`): Moss `list_indexes` vs a small registry.
- Whether `find_manual` returns a file or a URL Unsiloed fetches directly.
- How much repair-graph extraction is automatic vs curated for the demo model.
