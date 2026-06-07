# Clutch HLD 01 — Onboarding & Corpus

**Status:** Draft v0.1 · **Build tier:** 0 · **Sponsor:** Unsiloed (parse) + Moss (index/host)
**Depends on:** company-uploaded products + docs + photos · **Consumed by:** Perception (02), Retrieval (03), Gateway/Tenancy (06)
**Reuses:** `ingest/` (built: `unsiloed_client.py`, `chunker.py`, `ingest.py`)

---

## 1. Purpose
The cold, self-serve setup path. In the Clutch console a company **creates its products**, and for
each product **uploads that product's docs** (user guide, service manual, spec sheet,
troubleshooting — *many docs per product*) plus **one clean reference photo**. Clutch turns this
into everything the runtime needs: a per-company **Moss index** of grounded chunks (each tagged
with its product + source doc), a per-company **product catalog** (one `CatalogEntry` per product,
with the uploaded photo for closed-set visual ID), an **API key**, and a one-line **embed
snippet**. Output is a single `Company` record (00 §3). *Log in, add products + docs, drop in one
line.*

## 2. Scope
**In:** console product creation + doc upload + product-photo upload; Unsiloed parse + poll per
doc; chunk + index into `clutch-<company>` with `product_id`/`doc_id`/`doc_type` metadata; catalog
assembly (one entry per product; `ref_image` = the uploaded photo); embed-snippet + API-key
generation; `Company` assembly. Strict per-tenant isolation.
**Out:** live retrieval/grounding (03), runtime vision/product ID (02), the widget + LiveKit
transport (05), gateway routing/governance (06). **No `find_manual`** — the company uploads docs
directly, so there is no manual-discovery step. **No image extraction** — the reference photo is
uploaded, not pulled from the docs.

## 3. Design (how it works; key decisions)
A product is a **group of docs + one photo**, declared explicitly in the console (most reliable
mapping). The parse→chunk→index spine reuses the built `ingest/` modules unchanged; Clutch adds
the product grouping, the product-scoped metadata, and catalog assembly.

1. **Console: declare products, attach docs + photo.** Company authenticates, creates a corpus,
   then for each product enters `{name, brand, aliases?}` and uploads (a) **one or more docs** and
   (b) **one reference photo**. Each uploaded doc is bound to its `product_id` and given a
   `doc_type` (user_guide / service_manual / spec_sheet / troubleshooting / other). This explicit
   grouping is the doc↔product link — no inference needed.
2. **Parse (default Unsiloed), per doc.** `UnsiloedClient.parse(source)` submits the async job and
   polls to `Succeeded`, returning `chunks[].segments[]` with `segment_type`, `markdown/content`,
   `page_number`, `bbox` (`ingest/unsiloed_client.py`, built). The parser is reached behind the
   `parse_doc(source) -> dict` adapter (`parse_provider`, 06), so it is **plug-and-play** (00 §7):
   any parser that emits the same segment shape into `chunk_result` drops in — Unsiloed is the default,
   not a hard dependency. Cold-path only, so a swap never affects a live session.
3. **Chunk + index (Moss).** `chunk_result(...)` produces self-contained, heading-prepended,
   string-metadata chunks (`ingest/chunker.py`); we attach `extra_metadata={"product_id", "doc_id",
   "doc_type"}` so retrieval (03) can filter by **product** and **cite the specific doc/section**.
   `inject_into_moss(...)` creates/upserts into `clutch-<company>` (`ingest/ingest.py`). **All docs
   of a product share one `product_id`** → a single query can pull across the user guide *and* the
   troubleshooting doc.
4. **Assemble catalog (one entry per product).** `CatalogEntry { product_id, name, brand, aliases,
   description, ref_image }` — `name/brand/aliases` from what the company entered (optionally
   enriched from doc titles); `description` a short blurb from the spec sheet; **`ref_image` = the
   uploaded product photo** (the closed-set visual-match target for Perception 02).
5. **Snippet + key.** Mint `api_key`, generate the one-line embed snippet carrying the company key,
   assemble + persist `Company`.

**Key decisions:** (a) **product = company-declared group of docs + one photo** (explicit > inferred);
(b) chunk metadata carries `product_id` + `doc_id` + `doc_type` so retrieval filters by product and
citations name the exact doc; (c) reference image is **uploaded**, not extracted; (d) one index +
one catalog per company — no cross-company anything; (e) **auth = Firebase** (sign-in + ID token);
the console splits into a **frontend** (`console/`, Arman) and an **HTTP backend** (`src/api/`, Arham)
over the REST surface in §4; product metadata + status live in **Firestore**, uploaded files in
**Firebase Storage**; onboarding is **incremental** (company created at first login; products parsed
on demand via `/process`).

## 4. Data & interfaces (PRODUCES / CONSUMES; reference 00 contracts)
**Produces** (00 §3): `Company { company_id, name, index_name="clutch-<company>", catalog: list[CatalogEntry], api_key }`,
and in Moss, chunks with string metadata `{product_id, doc_id, doc_type, section, page, source}`.
`CatalogEntry { product_id, name, brand, aliases, description, ref_image: bytes }` — one per product.

```python
# Console-side product model (new)
Product   = { "product_id", "name", "brand", "aliases", "docs": [DocUpload], "photo": bytes }
DocUpload = { "doc_id", "doc_type", "source" }    # doc_type ∈ user_guide|service_manual|spec_sheet|troubleshooting|other

# Onboarding surface (composes built ingest/ modules)
parse_doc(source) -> dict                          # ingest/unsiloed_client.UnsiloedClient.parse (built)
chunk_result(result, *, source, extra_metadata) -> list[dict]   # ingest/chunker.py (built)
inject_into_moss(chunks, *, index_name="clutch-<company>", recreate, ...)  # ingest/ingest.py (built)
build_catalog_entry(product) -> CatalogEntry       # NEW — uses the uploaded photo as ref_image
onboard(company_id, product) -> Company            # NEW — process ONE product (incremental): parse each
                                                   #        doc → tag → index; (re)build catalog entry

# Console auth + API surface (NEW — frontend = console/ [Arman]; HTTP backend = src/api/ [Arham])
# Auth: Firebase Auth — frontend sends the ID token; backend verify_id_token -> uid -> company_id
# DB:   Firestore (companies / products / docs);  Files: Firebase Storage (per-company path)
POST /api/companies/bootstrap   -> {company_id}     # first login: create Company, mint api_key
GET|POST /api/products          -> list | {product_id}
POST /api/products/{id}/docs    -> register uploaded files {doc_id, doc_type, storage_path}
POST /api/products/{id}/process -> {job_id}         # runs onboard(company_id, product) in background
GET  /api/products/{id}/status  -> {status}         # draft→uploaded→parsing→indexing→ready|error
GET  /api/embed                 -> {api_key, snippet}
```

**Consumed by:** Perception (02) reads `Company.catalog` (closed set + `ref_image` for image-match);
Retrieval (03) reads `Company.index_name`, filters chunks by `product_id`, and cites `doc_type`/
`section`; Gateway/Tenancy (06) reads `api_key`/`company_id` to scope every session.

## 5. Sequence / flow
```
company → console (console/): landing → Firebase sign in / sign up
  first login → POST /api/companies/bootstrap → Company created, api_key minted     [NEW]
  create a PRODUCT (name, brand, aliases) → POST /api/products
  upload docs[] + photo → Firebase Storage (companies/<id>/products/<pid>/…)         [NEW]
                        → POST /api/products/{pid}/docs (register paths + doc_type)
  click Process → POST /api/products/{pid}/process → onboard(company_id, product):
    for each doc:  UnsiloedClient.parse(source)                                      [built]
                   chunk_result(extra_metadata={product_id, doc_id, doc_type})       [built]
    build_catalog_entry(product)   # ref_image = uploaded photo                      [NEW]
    inject_into_moss(chunks, index_name="clutch-<company>")                          [built]
    Firestore status: parsing → indexing → ready   (console polls GET …/status)
  when ready → GET /api/embed → render embed snippet (carries data-clutch-key)
  → Company{company_id, name, index_name, catalog, api_key}  →  06 registry; 02/03 consume
```
Embed snippet (one line the company pastes into its support page):
```html
<script src="https://cdn.clutch.ai/widget.js" data-clutch-key="<api_key>"></script>
```
`data-clutch-key` resolves (via 06) to the company's `index_name` + `catalog`, binding every
website session to exactly that tenant.

## 6. Failure modes & fallbacks (fail-safe)
| Failure | Fallback |
|---|---|
| Unsiloed slow/erroring (offline cold path) | retry; commit pre-parsed JSON per doc — ingestion never blocks a live session |
| Company uploads no product photo | `ref_image=None`; Perception (02) degrades to OCR-label + name/description closed-set ID |
| Spec/headers too sparse to describe a product | company-entered `name/brand` always present (declared in console); `description` optional |
| Empty parse result for a doc | `chunk_result` returns `[]` (built); other docs of the product still index; doc flagged "no text" |
| Moss index create vs upsert | `inject_into_moss` branches on existence; `--recreate` rebuilds a tenant cleanly |
| Adding a doc to an existing product later | upsert new chunks with the same `product_id`; catalog entry unchanged |

## 7. Sponsor mapping
- **Unsiloed** — parses every uploaded doc (async submit→poll). `ingest/unsiloed_client.py`.
- **Moss** — hosts the per-company index `clutch-<company>`; string-only metadata incl. `product_id`,
  `doc_id`, `doc_type`. `ingest/ingest.py`.
- **Firebase** — **Auth** (company sign-in + ID token), **Firestore** (companies / products / docs +
  processing status — the tenant registry 06 reads), and **Storage** (uploaded docs + photos).
- **TrueFoundry (06)** — not on the cold path, but stores/serves the `Company` record and resolves
  the snippet's `data-clutch-key` at runtime.

## 8. Reuse + Open questions
**Reuse:** the parse→chunk→index spine is **built** in `ingest/` and used as-is —
`unsiloed_client.parse`, `chunker.chunk_result` (heading-aware, string metadata), `inject_into_moss`
(create/upsert, batched). Clutch's onboarding has **no `find_manual`** (the company uploads docs
directly), no FACT signing, no image extraction, and no repair-graph builder. The only new code is
the console product model, `build_catalog_entry` (photo → `ref_image`), and the `onboard`
orchestrator (mint `api_key` + snippet).

**Open questions:**
- Console editing/versioning of products + docs (re-upload, swap photo) — Tier-1.
- Snippet auth: public widget key vs signed token (00 §9).
- Optional auto-enrich of `name/aliases` from doc titles to reduce console typing.

This HLD closes the cold path: company creates products + uploads docs/photos → Unsiloed/Moss
(product-tagged chunks) + per-product catalog → embed snippet, emitting one `Company` that
Perception (02) and Retrieval (03) consume per tenant.
