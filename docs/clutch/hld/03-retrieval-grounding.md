# Clutch HLD 03 — Retrieval & Grounding

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** Moss (retrieval layer)
**Consumes:** `RetrievalQuery` / `IdentifyResult` (from [02 Perception](02-perception.md), [04 Agent](04-agent.md))
**Produces:** `Chunk[]` + grounded `Answer` (to [04 Agent](04-agent.md))

> Reference [00 Overview](00-overview.md) for the shared contracts (`Chunk`, `RetrievalQuery`,
> `Answer`, `IdentifyResult`, `ProblemSignals`) and the grounding guarantee (§4). This HLD does not
> redefine them.

---

## 1. Purpose
Be the **knowledge read path** and the **grounding guarantee** in one component. Given a customer's
text (and/or the `ProblemSignals` the camera saw), retrieve the most relevant chunks from *that
company's* Moss index in **sub-10 ms**, then compose an `Answer` **only** from those chunks, each
sentence backed by a citation. This is what makes Clutch accurate and safe: the agent states facts
from the company's own docs, surfaced as `Answer.citations`, and **never improvises**. (This is the
accuracy guarantee that replaces the dropped FACT cryptographic layer — grounding by citation, not
by signature.)

## 2. Scope
**In:** company-scoped Moss query (`retrieve`), hybrid semantic+keyword ranking, `product_id`
metadata filter, top-k, the grounded cited compose, the empty/low-score refusal, multi-tenant
isolation. **Out:** producing the query/state (04), vision/product ID + problem signals (02),
building/signing the index (01 onboarding, `ingest/`), speaking/transporting the answer (05),
model routing (06).

## 3. Design (how it works; key decisions)
- **One index per company, product-filtered.** Query `clutch-<company>`; when the product is known
  (from Perception 02 or session state), add a `product_id` metadata filter to scope within the
  company. No product known → query company-wide (broader recall). **Never** query across companies.
- **Hybrid query.** Build the query text from the customer's message and/or `ProblemSignals.to_query()`
  (error codes + indicators + parts + damage + summary). Run Moss hybrid (`alpha≈0.7`) so colloquial
  phrasing and exact part/error strings both rank well. `top_k≈5`.
- **Load once, query hot.** `load_index("clutch-<company>")` at session start (cloud→in-process,
  ~3–5 ms queries) so filters apply and reads stay <10 ms. Filters only bind on a locally-loaded
  index (a cloud query logs-and-drops the filter), so the load is mandatory before any filtered
  `retrieve`.
- **Grounded compose (the guarantee).** The agent composes its `Answer.text` **only** from the
  retrieved `Chunk`s; each claim carries an `Answer.citations` entry `{doc, section, score}` shown to
  the user. If retrieval is empty or every chunk is below the score floor (`min_score≈0.35`), it does
  **not** answer — it says *"I don't see that in the docs"* or escalates (04). Nothing is invented.
- **Why citation (not cryptographic proof).** Clutch grounds by **provenance-by-citation** — the
  chunk's `source`/`section` *is* the proof, shown inline to the user. The
  composer is constrained to the retrieved text; un-cited sentences are not emitted.
- **Model-portable (00 §7).** The compose LLM is reached only through the gateway `reason.compose`
  logical name (06) — swapping the brain (MiniMax → any OpenAI-compatible model) is a route/config
  change, never an edit to `compose()`. Retrieval itself is swappable too: `retrieve()` is an
  interface over Moss with a **local-cosine fallback** (§6) — the grounding guarantee depends on the
  citation discipline, not on any specific model or index vendor.

## 4. Data & interfaces (reference 00 contracts)
**Consumes:** `RetrievalQuery{company_id, text, product_id?, need[]}` (04 → 03), and
`IdentifyResult.product_id` / `IdentifyResult.problem: ProblemSignals` (02) when in a "See" session.
**Produces:** ranked `Chunk[]` and a grounded `Answer{text, citations[]}` (03 → 04 → 05).

```python
def retrieve(company_id: str, text: str, product_id: str | None = None,
             *, top_k: int = 5) -> list[Chunk]
    # query clutch-<company>, hybrid semantic+keyword, product_id metadata filter, top-k

def retrieve_for(query: RetrievalQuery) -> list[Chunk]
    # convenience: build query text from query.text and/or ProblemSignals.to_query()

def compose(answer_query: str, chunks: list[Chunk],
            *, min_score: float = 0.35) -> Answer
    # grounded compose: text built ONLY from chunks; citations {doc,section,score};
    # empty/all-below-min_score -> Answer with empty text flagged for "not in docs"/escalate (04)

def load_index(company_id: str) -> str          # -> "clutch-<company>", loaded + warmed
```
Moss metadata is **string-valued only** (00 §3): the product filter is
`{"$and": [{"field": "product_id", "condition": {"$eq": product_id}}]}`; citation fields read from
`Chunk.metadata` (`doc`/`source`, `section`) + `Chunk.score`.

## 5. Sequence / flow
```
session start ─ load_index("clutch-<company>") ─────────────────► index loaded + warmed (<10 ms reads)
Type/Talk turn ─ 04 emits RetrievalQuery(company_id, text) ─────► retrieve() ─► Chunk[]
See turn ─ 02 IdentifyResult{product_id, problem} ─ 04 builds RetrievalQuery(text=problem.to_query(),
                                                       product_id) ─► retrieve() ─► Chunk[]
Chunk[] ─ compose(query_text, chunks, min_score) ──────────────► Answer{text, citations[]}
   ├─ non-empty, scored ─► grounded answer + citations ─► 04 ─► 05 (render/speak, show citations)
   └─ empty / all < min_score ─► "I don't see that in the docs" or escalate (04 decides)
```

## 6. Failure modes & fallbacks (fail-safe)
| Failure | Fallback |
|---|---|
| Moss cloud unreachable | local in-memory cosine over the same chunk embeddings — identical `retrieve` interface, slower |
| Filter ignored (warning logged) | index not loaded locally → treat as load failure, re-`load_index` |
| Empty / all chunks < `min_score` | widen (lower `alpha`, drop `product_id` to company-wide); still empty → **return `[]`; never invent** — 04 says "not in docs" or escalates |
| Wrong `product_id` from 02 | low scores → company-wide widen surfaces the right doc; citations let the user catch a mismatch |
| Cross-company leak | structurally impossible — every call is scoped to `clutch-<company>`; product filter only narrows *within* a company |

## 7. Sponsor mapping
| Concern | Sponsor | Role |
|---|---|---|
| Knowledge / retrieval | **Moss** | sub-10 ms per-company hybrid index + query, product-filtered |
| Compose / refusal logic | (Clutch) | grounded cited compose; the accuracy guarantee |
| Model calls (compose LLM) | **TrueFoundry** AI Gateway (06) | routes the compose model call (fallback, cost, guardrails) |

## 8. Reuse (built code) + Open questions
- **Built:** the Unsiloed→Moss index (`ingest/ingest.py`, `inject_into_moss`,
  `MossClient`/`DocumentInfo`/`QueryOptions`/`MutationOptions`, `list_indexes`) — Clutch reads from
  the per-company `clutch-<company>` index it produces (01). Retrieval mechanics (state→query,
  hybrid `alpha`, load-once, local-cosine fallback) sit on the Moss SDK.
- **Not used:** cryptographic verification (Clutch grounds by citation, not signed verdicts);
  speculative prefetch; session-memory indexes.
- **Open questions:** per-company single index + `product_id` filter vs per-product indexes (default:
  per-company, filtered, per 00 §9); the right `min_score` floor + `alpha` per modality; whether
  voice ("Talk") needs tighter `top_k` to keep spoken answers short.

---
*Confirmation: this HLD defines Clutch's Retrieval & Grounding component — company-scoped Moss `retrieve()` + grounded, cited `compose()` with refusal — reading the built `ingest/` Moss index; grounding-by-citation (no cryptographic verification).*
