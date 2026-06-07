# Fixalong HLD 01 — Ingestion & Repair Graph

**Status:** Draft v0.1 · **Build tier:** 0 (ingest) / 1 (signing) · **Sponsor:** Unsiloed (+ FACT)
**Depends on:** a manual PDF/URL · **Consumed by:** Retrieval (02), Safety (06)
**Reuses:** `ingest/` (built) + `ingest/fact_moss.py` (built)

---

## 1. Purpose

Turn a messy manufacturer manual into an **executable repair graph** — a set of retrievable,
self-contained, *signed* knowledge chunks — and load it into Moss. This is the offline,
pre-demo step. "We don't parse PDFs; we turn dead manuals into executable repair state
machines."

## 2. Scope

**In:** parse the manual (Unsiloed), build the repair graph, chunk + tag, FACT-sign, index
into Moss. **Out:** live retrieval (02), runtime state tracking (05).

## 3. Inputs & outputs

- **Input:** manufacturer manual (PDF/DOCX/scanned), support article, service guide,
  exploded diagram.
- **Output:** (a) `repair_graph.json` (the branch structure), (b) a **Moss index** of
  signed chunks with rich metadata.

## 4. Pipeline

```
manual.pdf
  │  Unsiloed /parse  (async submit → poll)          [ingest/unsiloed_client.py — built]
  ▼  segments: title, text, table, picture, caption, page, bbox
chunker  (heading-aware, size-merged, string metadata) [ingest/chunker.py — built]
  ▼
repair-graph builder  (NEW for Fixalong)
  │  groups chunks into RepairNodes: steps, warnings, parts, hazards, branches
  ▼  repair_graph.json + per-chunk repair metadata
FACT sign  (sign_chunk, asserts="extracted_from")     [ingest/fact_moss.py — built]
  ▼  each chunk → signed envelope folded into metadata
Moss create_index / add_docs (batched)                 [ingest/ingest.py — built]
  ▼
Moss session-template index  (cloned per live session, HLD 02)
```

Steps 1, 2, the signer, and the Moss loader **already exist** in `ingest/`. The **new**
piece is the repair-graph builder + repair-specific metadata.

## 5. Data model

### 5.1 RepairNode
See HLD 00 §5.2. The graph is `{ "nodes": [RepairNode, ...] }`. Each node decomposes into
multiple Moss chunks (one per instruction/warning/part) so retrieval is fine-grained.

### 5.2 Per-chunk Moss metadata (all string-valued — Moss requirement)
Extends the existing chunker metadata (`source`, `section`, `page`) with repair fields:

| key | example | use |
|---|---|---|
| `type` | `warning` | filter by kind (step/warning/part/error/diagram/branch) |
| `node_id` | `jam_rear_panel` | tie chunk to its graph node |
| `part` | `pickup roller` | part-keyed retrieval |
| `risk_level` | `high` | safety prioritization + Moss filter |
| `hazard` | `hot_surface` | surface warnings proactively |
| `symptom` | `paper jam` | symptom→branch mapping |
| `model` | `hp-laserjet-x` | model scoping |
| `fact_envelope` etc. | … | FACT attestation (HLD 06) |

Risk-level + hazard in metadata lets the agent **proactively** retrieve warnings near
dangerous parts, and lets Moss filter (`risk_level=high`) for safety sweeps.

## 6. Repair-graph builder (the new component)

Heuristics over Unsiloed segments → RepairNodes:
- **Section headers / numbered steps** → node boundaries + `instructions`.
- **Callout/“Warning”/“Caution” blocks** → `warnings` (+ `risk_level`, `hazard`).
- **Tables (error codes)** → `error_codes` entries.
- **Diagram captions + labels** → `diagrams` with `parts`.
- **Cross-references ("if jam persists, see…")** → `next[]` / `conditions[]` edges.
- **Part glossary / exploded-view labels** → `parts[]` + aliases (for part-keyed retrieval).

> **Pragmatic stance for the hackathon:** run Unsiloed for real to produce clean
> Markdown/JSON, then **hand-curate the small repair graph** for the one demo scenario
> (printer jam). Show the automatic ingestion path in architecture; don't depend on fully
> automatic graph extraction being robust in 24 h.

## 7. Interfaces

```python
parse_manual(path_or_url) -> UnsiloedResult                # ingest/unsiloed_client.py (built)
build_repair_graph(unsiloed_result, model) -> RepairGraph  # NEW
graph_to_chunks(graph) -> list[Chunk{id,text,metadata}]    # NEW (reuses chunker patterns)
sign_chunks(chunks, kb_key) -> list[Chunk]                 # ingest/fact_moss.py (built)
inject_into_moss(chunks, index_name, ...)                  # ingest/ingest.py (built)
```

CLI target: `python ingest.py manuals/printer.pdf --sign --index fixalong-printer`.

## 8. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Unsiloed API slow/erroring at build time | commit pre-parsed JSON; ingestion is offline so this is safe |
| Graph extraction noisy | hand-authored `repair_graph.json` for the demo scenario |
| FACT signing not ready | ingest without `--sign`; agent runs without the trust gate (HLD 06 degrades) |

## 9. Open questions
- Aliases per part (e.g., "black rubber wheel" → pickup roller) — author a synonym list to
  boost retrieval recall on colloquial user language.
- Per-node `fresh_until` policy (manuals rarely go stale; freshness matters more for
  firmware/service-bulletin content).
