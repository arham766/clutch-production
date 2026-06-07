# Clutch HLD 06 — Gateway, Multi-tenancy & Infra

**Status:** Draft v0.1 · **Build tier:** 1 · **Sponsor:** TrueFoundry
**Depends on:** — (this is the substrate) · **Consumed by:** all components (02–05 route models here; everyone reads config)

> Architecture, the two flows, and the **shared contracts** are defined in
> [00 — Overview](00-overview.md). This doc references `Company`/`SupportSession` etc.; it does not redefine them.

---

## 1. Purpose
The plumbing every other Clutch component stands on. It does three things: (1) routes the **LLM +
vision** model calls — **Qwen vision** + **MiniMax** (the reasoning brain) — through **one**
OpenAI-compatible **TrueFoundry AI Gateway** endpoint, so adapters carry one key and get fallback,
cost tracking, and built-in guardrails for free (**Cartesia STT/TTS run as LiveKit plugins, not via
the gateway**); (2) provides **identity & per-company isolation** — Firebase Auth + the Firestore
tenant registry, exposed via the `src/api/` backend — so a session only ever touches its own Moss
index + catalog; (3) centralizes config + secrets and pins the single-instance demo deploy. It owns no
business logic — it is the routing layer, the auth + tenant registry, the `src/api/` HTTP surface, and
`config.py`.

## 2. Scope
**In:** the gateway adapter contract (`base_url` → TrueFoundry); model routing + fallback + cost +
guardrails; the company registry and tenant scoping; `src/config.py` (env, singletons, secrets);
demo deploy topology. Agent Gateway governance described as the **enterprise/stretch** layer.
**Out:** what each model is *used for* (02 vision, 05 voice, 03/04 reasoning own that); Moss indexing
mechanics (01/03); MCP tool gateway (companies upload docs — no runtime tools needed).

## 3. Design (how it works; key decisions)

**3a. AI Gateway = one endpoint for every model (core TrueFoundry use).**
Each adapter is constructed with `base_url` pointed at the gateway and a single `TRUEFOUNDRY_API_KEY`;
it then calls the standard OpenAI chat/embeddings/vision shape with a logical model name. The gateway
resolves provider, applies **fallback** (primary → backup on error/timeout), records **cost per call**,
and runs **built-in guardrails** — PII redaction on transcripts and a **safe-instruction check** on
hardware advice (don't tell a customer to touch a live wire / hot part), the Clutch analogue of the old
output guardrail. The grounding guarantee (00 §4) stays upstream in the agent; the gateway is the
*last* safety net on generated text. No component calls a provider SDK directly.

**This logical-name indirection is the LLM/vision swap point (00 §7).** Callers ask for
`reason.compose` / `vision.identify`; `route(task)->model` resolves the logical task to whatever model
is configured. Replacing MiniMax or Qwen with another OpenAI-compatible model is a `route` table /
config edit — zero changes in 02/03/04. STT/TTS are the exception: they are **not** gateway-routed
(LiveKit plugins), so their swap point is the speech-adapter layer in 05, not this table.

| Logical call | Model class (default) | Used by |
|---|---|---|
| `vision.identify` | multimodal VLM (default **Qwen-VL**) | Perception (02) |
| `reason.compose` | reasoning LLM / the brain (default **MiniMax**) | Agent (04) / Grounding (03) |
| `embed` | embedding model | retrieval path (03, if not Moss-native) |
| *(voice STT/TTS)* | speech provider (default **Cartesia**) — **NOT gateway-routed**; swapped via 05 speech adapters | Realtime/Voice (05) |

**3b. Agent Gateway = governance (stretch / enterprise).**
Clutch is **one support agent per company**, which maps cleanly onto TrueFoundry's Agent Gateway as the
control plane: **per-company cost attribution**, **step-level tracing**, **RBAC**, retries, and
guardrails — framework-agnostic, sitting above the AI Gateway it uses for model routing. Wired if time
permits; otherwise it is the enterprise-governance slide. (Background: AI Gateway = LLM routing,
MCP Gateway = tools [not needed], Agent Gateway = the whole-agent control plane.)

**3c. Multi-tenancy.**
A **company registry** — backed by **Firestore** — maps `company_id → {index_name "clutch-<company>",
catalog, api_key, owner_uid}`. The embed
snippet carries the **company key**; on session open it resolves the `Company` and stamps `company_id`
onto the `SupportSession`. Every Moss retrieval (03) and every catalog lookup (02) is scoped by that id —
**strict isolation, no cross-company retrieval, ever** (00 §5). The registry is the single authority for
"which index + which catalog" and the gateway tags each model call with the `company_id` for attribution.

**3d. Infra / config.**
`src/config.py` loads `.env` (uv everywhere), validates the creds each subsystem needs, and exposes
frozen singletons: Moss, the parser (default Unsiloed), the TrueFoundry gateway, LiveKit, and the
gateway-routed model clients (reason/vision) — all **role-named, never vendor-named** (00 §7). **Core** creds (Moss, gateway,
LiveKit) are mandatory — nothing runs without retrieval + reasoning + transport; feature creds degrade
gracefully. Loaded **once** at boot and threaded through; no module re-reads `os.environ`.

**3e. Auth, DB & the Console API (Firebase + `src/api/`).**
Company **authentication is Firebase Auth**: the console frontend (`console/`, Arman) signs the user
in and sends a **Firebase ID token**; the HTTP backend (`src/api/`, Arham) calls `verify_id_token` and
maps `uid → company_id` (Firestore `users/{uid}`). **Firestore** is the tenant registry + product/doc
store (3c); **Firebase Storage** holds uploaded files. `src/api/` is the single REST surface for both
the console onboarding flow (products / docs / process / status / embed — see 01 §4) and the widget
bootstrap (`/connection-details`). Every `/api/*` call is token-verified and scoped to its
`company_id` — the same isolation guarantee as model routing (a bad/missing token → 401, never a
default tenant).

## 4. Data & interfaces (PRODUCES / CONSUMES — see 00 contracts)
Reuses `Company` and `SupportSession` from 00 verbatim. New surface lives in `src/config.py` +
`src/gateway.py` + `src/tenancy.py` + `src/api/`:

```python
# config.py — singletons + secrets (uv, .env)
@dataclass(frozen=True)
class Config:
    moss_api_key: str; moss_base_url: str                         # MOSS_*            (core)
    tf_api_key: str; tf_base_url: str                             # TRUEFOUNDRY_*     (core, OpenAI-compatible)
    livekit_url: str; livekit_api_key: str; livekit_api_secret: str  # LIVEKIT_*      (core)
    firebase_project_id: str; firebase_credentials: str             # FIREBASE_*        (core: auth + DB + storage)
    # Role-named, NOT vendor-named (00 §7 portability): swap a model by changing the value, not the key.
    reason_model: str                                            # gateway logical name (default: MiniMax)
    vision_model: str                                            # gateway logical name (default: Qwen-VL)
    embed_model: str                                             # gateway logical name (embeddings, if not Moss-native)
    stt_provider: str; tts_provider: str                        # LiveKit speech plugin ids (default: cartesia / cartesia)
    parse_provider: str                                          # onboarding parser id (default: unsiloed)
    unsiloed_api_key: str | None                                 # feature cred; degrade if absent
    degraded: tuple[str, ...]                                     # subsystems running without creds

def load_config(env_path: str | None = None) -> Config: ...       # called once at boot; ConfigError on missing core

# gateway.py — every model client points base_url here (the ONE TrueFoundry use)
def gateway_client(cfg: Config) -> OpenAICompatClient: ...        # base_url=tf_base_url, key=tf_api_key
def route(task: str) -> str: ...                                  # logical task -> model name (table §3a)

# tenancy.py — the company registry (strict isolation), backed by Firestore
def resolve_company(company_key: str) -> Company: ...             # embed key -> Company via Firestore (index, catalog, api_key)
def scope_session(company_key: str, modality) -> SupportSession:  # stamps company_id; everyone scopes by it

# api/ — the HTTP backend (FastAPI): every route Firebase-verified + company-scoped
def verify_token(authorization: str) -> str: ...                 # Firebase ID token -> uid -> company_id
# /api/* (console onboarding, see 01 §4) + POST /connection-details (widget bootstrap) live here
```

## 5. Sequence / flow
```
boot:    load_config() ─▶ build gateway_client + Moss/LiveKit/Qwen clients (all model clients → gateway base_url)
session: widget(company_key) ─▶ resolve_company ─▶ SupportSession{company_id}
         02/03/04/05 call ─▶ route(task) ─▶ gateway_client ─▶ [fallback · cost · guardrail] ─▶ provider
         retrieval/ID ─────▶ ALWAYS scoped to company_id's index_name + catalog (no cross-tenant)
```

## 6. Failure modes & fallbacks (fail-safe)
| Failure | Fallback |
|---|---|
| Gateway primary model errors/times out | gateway auto-fails over to backup provider (same call, no app change) |
| Gateway endpoint fully down | adapter falls back to a direct provider client (same OpenAI shape) + local safe-instruction check |
| Guardrail false-block on safe advice | fail toward caution: prepend required warning rather than go silent; log it |
| Unknown / missing `company_key` | reject session — never default to another tenant's index (isolation first) |
| Missing **core** cred at boot | `ConfigError` naming the var; process exits before any session opens (fail fast) |
| Missing **feature** cred (Unsiloed) | recorded in `cfg.degraded`; owning module runs its documented fallback |

## 7. Sponsor mapping
**TrueFoundry** is the headline: **AI Gateway** = one OpenAI-compatible endpoint, one key, for **Qwen
vision + MiniMax (the reasoning brain)**, with fallback, cost tracking, and PII + safe-instruction
guardrails. **Agent Gateway** = the enterprise governance layer over Clutch's one-agent-per-company
model (cost attribution, tracing, RBAC) — wired if time, else the slide. **Cartesia STT/TTS** are
LiveKit plugins (not gateway-routed). Moss, LiveKit, Unsiloed are reached around this substrate.
**Firebase** is the back-office substrate: **Auth** (company sign-in), **Firestore** (the tenant
registry + onboarding store), and **Storage** (uploaded files), surfaced through `src/api/`.

## 8. Reuse + Open questions
**Reuse:** the TrueFoundry AI Gateway (one OpenAI-compatible endpoint) for routing + output
guardrails (PII + safe-instruction checks); a single `config.py` with `load_config()` +
`degraded`/fail-fast; `route(task)->model` with a local guardrail fallback. Single-instance/container
demo deploy. **Not used:** FACT, MCP tool gateway, AWS Bedrock/S3, no runtime tools (the company
uploads docs). MiniMax = reasoning brain; Cartesia (voice I/O) runs as LiveKit plugins.
**Open questions:** (a) embed-snippet auth — public widget key vs signed token (00 §9); (b) Agent
Gateway wired live for the demo or slide-only; (c) per-company index vs product-filtered single index
(default per-company, per 00 §9) — registry abstracts either.

---
*Confirmation: one Clutch HLD written to `docs/clutch/hld/06-gateway-infra.md`, following 00's contracts and the README template exactly.*
