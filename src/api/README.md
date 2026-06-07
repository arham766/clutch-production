# `src/api/` — HTTP backend (Section B · Arham)

The REST surface the console frontend and the widget call (HLD
[01](../../docs/clutch/hld/01-onboarding-corpus.md) + [06](../../docs/clutch/hld/06-gateway-infra.md)).
FastAPI. Every `/api/*` route verifies a **Firebase ID token**, maps `uid → company_id`, and scopes
all access to that tenant — no cross-company access, ever.

- **Exposes (S9, console):** `POST /api/companies/bootstrap`, `GET/POST /api/products`,
  `POST /api/products/{id}/docs`, `POST /api/products/{id}/process`,
  `GET /api/products/{id}/status`, `GET /api/embed`
- **Exposes (S1, widget):** `POST /connection-details` → scoped LiveKit token
- **Receives (passed in):** Firebase verify, `onboard` (onboarding pipeline), `resolve_company`
- **Imports only:** `src/contracts.py` + `onboarding/` (own section) + external vendors
- **Build-against mock:** Firebase emulator/test project + `pre_parsed_json`

**Auth:** Firebase Auth (client signs in → ID token; this layer verifies via `firebase-admin`).
**DB:** Firestore (`users`, `companies`, `products`, `docs`). **Files:** Firebase Storage at
`companies/{company_id}/products/{product_id}/…`.

See the brief: [12 — Arham](../../docs/clutch/hld/engineers/12-arham-platform-infra.md) and the
connection plan [10 §4 (S9)](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
