# Section B — Arham · Platform Layer (Auth, Database, API & Infra)

**Engineer:** Arham — Platform & Infrastructure Engineer (auth, database, API setup)
**Owns HLDs:** [01 — Onboarding & Corpus](../01-onboarding-corpus.md) · [06 — Gateway, Multi-tenancy & Infra](../06-gateway-infra.md)
**Owns directories:** `src/config.py`, `src/gateway.py`, `src/tenancy.py`, `src/onboarding/`,
`src/api/` (HTTP backend), `ingest/` (reuse, built), `deploy/`, **and the wiring file `src/app.py`**
**Uses:** Firebase (Auth verify + Firestore DB + Storage) · TrueFoundry Gateway · Moss · Unsiloed
**Seams you own:** **S9** (Console API), **S5** (Gateway), **S6** (Tenancy), **S7** (Onboarding→Runtime) — [10 §4](10-work-division-and-fusing.md)

> **Independence ([10 §5](10-work-division-and-fusing.md)).** Your subfolders import only
> `src/contracts.py` + external vendors (Firebase, TrueFoundry, Moss) — **no other section**. **You
> expose:** the `/api/*` REST surface, `resolve_company` / `scope_session`, `route` / `gateway_client`,
> `onboard`. **Build against:** Firebase emulator (or a test project), `pre_parsed_json`, a real
> `clutch-demo` Moss index, `mock_gateway`. **You provide the mocks** C & D build against
> (`stub_tenancy`, `mock_gateway`). **Connect later:** you own `src/app.py` — the adapter layer (§7).

---

## 1. Your mission
You are the **substrate** and the **back office**. Four jobs:
1. **Auth + DB + storage** — Firebase Auth (who is this company), Firestore (the source of truth for
   companies/products/docs), Firebase Storage (the uploaded files).
2. **The Console API** (`src/api/`) — the REST endpoints Arman's console calls to create products,
   register uploads, kick off processing, and fetch the embed snippet.
3. **The onboarding pipeline** (01) — read uploaded docs → Unsiloed parse → chunk → Moss index +
   per-product catalog → mint `api_key` → emit one `Company`.
4. **Runtime plumbing** (06) — the TrueFoundry gateway (model routing), strict per-company isolation,
   `config.py`, deploy, and **`src/app.py`** (the wiring file where all four subfolders connect).

## 2. Scope

### 2a. Auth — Firebase (server side)
- Verify the **Firebase ID token** on every `/api/*` request (`firebase-admin` `verify_id_token`),
  yielding the `uid`. (Arman's frontend signs users in and attaches `Authorization: Bearer <idToken>`;
  you only verify.)
- Map `uid → company_id` via Firestore `users/{uid}`. First login → `companies/bootstrap` creates the
  company + the user mapping. A request with no/invalid token → **401**, never a default tenant.

### 2b. Database — Firestore (the company registry)
```
users/{uid}                                   = { email, company_id }
companies/{company_id}                         = { name, owner_uid, api_key, index_name="clutch-<id>", created_at }
companies/{company_id}/products/{product_id}   = { name, brand, aliases[], photo_path, status, created_at }
companies/{company_id}/products/{pid}/docs/{doc_id} = { doc_type, storage_path, parse_status }
```
`status` per product: `draft → uploaded → parsing → indexing → ready | error`. Firestore **is** the
multi-tenant registry that `resolve_company` reads (06 §3c).

### 2c. Storage — Firebase Storage
Files land at `companies/{company_id}/products/{product_id}/{filename}` (Arman uploads direct from the
browser under Storage rules scoped to the owner). Your pipeline **reads** these via the Admin SDK — no
large payloads through the API.

### 2d. The Console API — `src/api/` (S9 server half; FastAPI)
| Method + path | Does | Returns |
|---|---|---|
| `POST /api/companies/bootstrap` | first login: create `company` + `users/{uid}` map, mint `api_key`, set `index_name` | `{company_id, name}` |
| `GET /api/products` | list this company's products | `[{product_id, name, brand, status, doc_count}]` |
| `POST /api/products` | create a product folder `{name, brand, aliases?}` | `{product_id}` |
| `POST /api/products/{id}/docs` | register uploaded files `{docs:[{doc_id, doc_type, storage_path}], photo_path}` | `{ok}` |
| `POST /api/products/{id}/process` | kick off the onboarding pipeline (background) | `{job_id}` |
| `GET /api/products/{id}/status` | processing progress | `{status, progress, message}` |
| `GET /api/embed` | once ≥1 product `ready` | `{api_key, snippet}` |
| `POST /connection-details` | widget bootstrap (S1): `company_key + modality` → LiveKit token | `{token, url}` |

Every `/api/*` route runs the token-verify dependency and scopes all reads/writes to the caller's
`company_id`. **No cross-company access, ever** (00 §5).

### 2e. Onboarding pipeline (01) — triggered by `/process`
`onboard()` reads the product's registered docs from Storage, then per doc: `parse_doc` (default
Unsiloed, behind the adapter) → `chunk_result` with `extra_metadata {product_id, doc_id, doc_type}` →
`inject_into_moss("clutch-<company>")`; `build_catalog_entry(product)` sets `ref_image` from the
uploaded photo. It updates Firestore `status` (`parsing → indexing → ready`) so Arman's poll reflects
progress, and writes the catalog + `api_key`. **Reuse `ingest/` as-is.**

### 2f. Gateway (06)
`gateway_client(cfg)` (one `base_url` → TrueFoundry, one `TRUEFOUNDRY_API_KEY`); `route(task)->model`
for `vision.identify` / `reason.compose` / `embed`; fallback, cost tracking, PII + safe-instruction
guardrails as the last safety net. No component calls a provider SDK directly.

### 2g. Tenancy (06)
`resolve_company(key)->Company` (reads Firestore by `api_key`), `scope_session(key, modality)->SupportSession`
(stamps `company_id`). The widget's `data-clutch-key` resolves here. Strict isolation.

### 2h. Config + deploy + wiring
`src/config.py` (`load_config()`, frozen singletons, **role-named** keys, core-vs-feature creds,
fail-fast); single-instance demo deploy. **`src/app.py`** — import every subfolder, pass each the real
(or mock) functions it receives (10 §7). No business logic.

**Out:** the console *UI* (Arman owns `console/`); what models are *used for* (02–05); Moss query
mechanics (Fardin 03); vision/agent logic (Tonmoy 02/04).

## 3. What you build against (mock-first, day 1)
- **Firebase emulator** (or a throwaway project) for Auth + Firestore + Storage — no prod creds needed.
- Onboarding runs offline on **pre-parsed JSON per doc** (01 §6) — no live Unsiloed.
- The gateway exposes a **mock route** (canned completions) so C/D develop before any real key exists.
- `resolve_company` serves a **stub `Company`** (one fake tenant + a real `clutch-demo` Moss index).

## 4. The single paths you connect through
- **S9 Console API:** Arman's console calls `/api/*` with a Firebase ID token; you verify, scope by
  `company_id`, and drive onboarding. This is the *only* console↔backend wire.
- **S5 Gateway:** Tonmoy/Fardin call `gateway_client` + `route` — never a provider key/SDK.
- **S6 Tenancy:** everyone calls `resolve_company` / `scope_session`; `company_id` is the only tenant key.
- **S7 Onboarding→Runtime:** you emit `Company`; Perception reads `catalog`, Retrieval reads
  `index_name`, the widget resolves `data-clutch-key` to it.
- **S1 bootstrap:** `POST /connection-details` mints a scoped LiveKit token for Arman's widget.

## 5. Definition of done
- [ ] Firebase Auth verify on every `/api/*` route; `uid→company_id`; bad/no token → 401 (never a default tenant).
- [ ] Firestore schema (2b) live; `bootstrap` creates company + user map + `api_key` + `index_name`.
- [ ] Product CRUD + `/docs` registration + Storage reads work end-to-end.
- [ ] `/process` runs `onboard()` in the background, updating `status` so Arman's poll shows progress.
- [ ] On `ready`, `/api/embed` returns the snippet with the company key; Moss `clutch-<company>` has
      `{product_id, doc_id, doc_type}` chunks and a catalog with `ref_image` from the photo.
- [ ] `gateway_client` + `route` resolve `vision.identify` / `reason.compose` via one TF endpoint, with
      fallback + a working safe-instruction guardrail.
- [ ] `resolve_company` / `scope_session` enforce isolation; `load_config()` fails fast on a missing core cred.
- [ ] `/connection-details` issues a scoped LiveKit token from a company key.
- [ ] `src/app.py` builds the full runtime from `Config`, passing mocks or real functions per seam.

## 6. Failure modes you own (01 §6, 06 §6)
Invalid/expired Firebase token → 401, no tenant leak. Unsiloed slow → retry / pre-parsed JSON (cold
path never blocks a live session); per-doc parse failure → mark that doc `error`, keep the rest, never
block the product. Gateway primary errors → auto-failover; fully down → direct-provider fallback +
local safe check. Missing core cred → `ConfigError`, process exits. Unknown company key → reject.

## 7. Handoffs
- **To Arman:** the `/api/*` contract (§2d) + Firebase project config (Auth + Storage rules) + the
  `/connection-details` route.
- **To Tonmoy & Fardin:** `gateway_client`, `route`, `resolve_company`, `scope_session`, `Config`, a
  stub `Company` + a real `clutch-demo` index.
- **You assemble `src/app.py`** — each engineer hands you one wiring line to register there.

## 8. Stretch / open questions
Agent Gateway governance (per-company cost attribution, tracing, RBAC) — wire if time, else the slide
(06 §3b). Embed-snippet auth: public widget key vs signed token (00 §9). Multi-user per company /
roles. Webhook (vs poll) for processing status.
