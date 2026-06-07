# Fixalong HLD 02 — Retrieval (Moss)

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** Moss (retrieval layer)
**Depends on:** a signed Moss index (built by **HLD 11**) · **Consumed by:** State Machine (05)
**Note:** This is the **component** view of Moss query mechanics (state→query, prefetch). The
integrated knowledge & trust layer — **signing, indexing, caching, and FACT verification — is
owned by [HLD 11 (FACT × Moss)](11-fact-moss-knowledge-trust.md)**. HLD 12 calls HLD 11, which
uses these mechanics.

---

## 1. Purpose

Be the **live repair memory**. Given the current repair state, return the exact warning,
diagram, part name, and next step in **<10 ms**, so the agent can preload context before the
user finishes speaking and check safety continuously. Moss is not "manual search" — it's the
hot-path retrieval layer the whole loop runs on.

## 2. Why Moss (the honest argument)

Users *can* wait two seconds for a one-shot answer. They **cannot** wait when the copilot
should: (a) prefetch context before they finish a sentence, and (b) check for hazards
continuously as they move. Moss makes retrieval cheap enough to run on **every** state
change — that's what turns "voice wrapper over a PDF" into "person standing next to you."

## 3. What we index

Per live session we load a Moss index built from the repair graph (HLD 01): steps,
warnings, error-code tables, part names + aliases, diagram labels, model caveats,
troubleshooting branches — **plus** evolving runtime context: current state, user
observations, prior actions, "do-not" constraints.

> **Every repair-state change is a retrieval event** (HLD 00 §2).

## 4. Index strategy

- **Session index, cloned from a template.** Following Moss's per-entity pattern, each
  repair session gets its own small index (template = the printer's repair graph, ~tens of
  chunks). Tiny indexes load fast and queries scale with the *session*, not a global catalog.
- **Pre-load once** at session start (`load_index`) so queries are in-process and ~3–5 ms.
- **Runtime recall is in-memory `RepairState`** (LLD 05 §5.6.5), **not** Moss. Optional **Tier-2**
  semantic memory upserts observations into a *separate ephemeral* `session-<id>` index (never the
  shared manual index), debounce-batched — off by default.

## 5. The query builder (state → query)

The State Machine (05) emits a `RetrievalQuery` (HLD 00 §5.3) on each meaningful change.
Mapping examples:

| Live signal | Retrieval target |
|---|---|
| "paper jam" | troubleshooting branch (by `symptom`) |
| "already pulled paper out" | skip first branch → persistent-jam branch (`preconditions`) |
| camera sees rear panel | rear-panel diagram + warnings (by `node_id`) |
| "black rubber wheel" | part identification (by `part` alias) |
| "looks shiny" | worn-roller branch (by `condition`) |
| reaches near fuser | `risk_level=high` warnings (proactive safety sweep) |

Implementation: build a hybrid query (semantic + keyword). Use Moss `alpha≈0.6–0.8` for
colloquial user phrasing; metadata filters narrow scope (`model`, `node_id`,
`type`, `risk_level`).

```python
results = moss.query(session_index, text=query.user_observation,
                     QueryOptions(top_k=5, alpha=0.7,
                                  filter={"model": {"$eq": model}}))
```

## 6. Speculative prefetch — vision-driven first

Prefetch is driven **primarily by the camera**, not by speech. On every gated
`VisualObservation` (04), warm context for **what the user is looking at** — before they say
anything. Speech then usually hits a warm cache. Because Moss is <10 ms, prefetch is nearly
free and removes perceived latency entirely.

```
on_visual_change(obs):   for q in queries_from_vision(obs, state): moss.prefetch(q)  # PRIMARY
on_partial(transcript):  for q in candidate_queries(transcript, state): moss.prefetch(q)
on_intent(text):         use cached results if hit, else moss.query(...)
```

Example: the camera sees the rear panel open → prefetch `rear path diagram`, `pickup roller
inspection`, `fuser warning`. So when the user later says "it looks shiny," the pickup-roller
context is **already loaded** — the system isn't starting from zero.

## 7. Output

A ranked `Chunk[]` (HLD 00 §5.3). Results pass through the FACT gate (06) which attaches a
`verdict` before they reach Claude. A safety sweep (`risk_level=high` near current part) can
run in parallel so warnings are never missed.

## 8. Interfaces
```python
load_session_index(model_slug) -> index_name       # loads the manual index fixalong-<model>
retrieve(state: RepairState, *, obs=None) -> list[Chunk]   # builds query + queries Moss
prefetch(state: RepairState, *, obs=None) -> None  # warms cache (vision-driven)
remember(session_id, item) -> None                 # Tier-2 OPTIONAL: ephemeral session-<id> index (NOT manual)
```

## 9. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| Moss cloud unreachable | local in-memory cosine over the same chunk embeddings (same `retrieve` interface) |
| Slow first query | pre-load index at session start; warm with one dummy query |
| Empty/low-confidence results | widen filter, lower `alpha`, fall back to node-level chunk; never invent (Safety 06 will REFUSE) |
| Live upsert latency | debounce-batch observations; reads stay <10 ms so recall of earlier observations still works |

## 10. Open questions
- Per-session index clone cost vs one shared index with `session_id` metadata filter.
- Embedding model choice (`moss-minilm` default vs `moss-mediumlm` for better recall on
  terse part names).
