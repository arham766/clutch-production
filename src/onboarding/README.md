# `src/onboarding/` — Onboarding & Corpus pipeline (Section B · Arham)

Cold setup path (HLD [01](../../docs/clutch/hld/01-onboarding-corpus.md)). Triggered by
`POST /api/products/{id}/process` (see [`src/api/`](../api/README.md)): reads a product's uploaded docs
from Firebase Storage and turns them into the runtime corpus.

- **Exposes:** `onboard(company_id, product) -> Company`, `build_catalog_entry(product) -> CatalogEntry`
- **Pipeline (per doc):** `parse_doc` (default Unsiloed) → `chunk_result` with
  `extra_metadata {product_id, doc_id, doc_type}` → `inject_into_moss("clutch-<company>")`;
  `build_catalog_entry` sets `ref_image` from the uploaded photo
- **Writes:** Firestore product `status` (`parsing → indexing → ready`) so the console poll shows
  progress; the per-product catalog + the company `api_key`
- **Receives (passed in):** `parse_doc`, Moss client, Storage reader
- **Imports only:** `src/contracts.py` + external vendors — no sibling subfolder
- **Build-against mock:** `pre_parsed_json` (committed parse fixtures)
- **Reuses:** the built `ingest/` spine (`unsiloed_client`, `chunker`, `ingest`)

See the brief: [12 — Arham](../../docs/clutch/hld/engineers/12-arham-platform-infra.md) and the
connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
