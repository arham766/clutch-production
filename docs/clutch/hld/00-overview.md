# Clutch HLD 00 — System Overview

**Status:** Draft v0.1 · **Read first.** Defines the two flows, the component map, and the
**shared contracts** every other HLD builds on.

Clutch = **live video support for hardware**. A support widget on a company's site with three
modes — **Type / Talk / See** — backed by an agent that's **grounded in that company's own docs**
and can **see the customer's device** through the camera. See [product overview](../README.md).

---

## 1. The two flows

### A — Company setup (cold, self-serve)
```
enterprise logs in → uploads manuals/spec sheets
  → Unsiloed parses → Moss indexes (per-company) → derive PRODUCT CATALOG (closed set for ID)
  → generate one-line embed snippet
```

### B — Customer support session (hot, in the widget)
```
customer on a generic support page → opens widget
  ├─ Type / Talk → Moss retrieval (company-scoped) → grounded answer
  └─ See (live camera, FRESH context):
       identify(frames, catalog) → { product_id, problem }   # vision: closed-set ID + problem
       → Moss retrieve scoped to company+product, driven by problem
       → grounded step-by-step over LiveKit (voice + on-feed guidance)
```

## 2. Component map (one HLD each)

| HLD | Component | Responsibility |
|---|---|---|
| 01 | [Onboarding & Corpus](01-onboarding-corpus.md) | Firebase login → upload → Unsiloed → Moss + catalog → embed snippet |
| 02 | [Perception](02-perception.md) | frames → product (closed-set) + problem signals (Qwen vision) |
| 03 | [Retrieval & Grounding](03-retrieval-grounding.md) | company-scoped Moss retrieval + grounded, cited compose |
| 04 | [Conversation Agent](04-agent.md) | the multi-modal support brain: state, when/what to say, escalation |
| 05 | [Realtime, Voice & Widget](05-realtime-voice-widget.md) | LiveKit (chat/voice/video) + Cartesia voice I/O + the embeddable widget |
| 06 | [Gateway, Multi-tenancy & Infra](06-gateway-infra.md) | TrueFoundry model routing/governance, Firebase auth + Firestore tenant registry, per-company isolation, deploy |

```
  COMPANY ─upload─▶ [01] Unsiloed→Moss + catalog ──────────────┐
                                                               ▼
  CUSTOMER ─widget─▶ [05] LiveKit (type/talk/see) ─▶ [04] Agent ─▶ [03] Moss retrieve+ground
                          │ frames                       │            ▲
                          ▼                              ▼            │ company-scoped
                       [02] Perception (product+problem) ─────────────┘
            all model calls routed via [06] TrueFoundry AI Gateway
```

## 3. Shared contracts (the spine — defined once, referenced everywhere)
Code home: `src/contracts.py` (already started). Moss metadata is **string-valued only**.

```python
Modality = Literal["text", "voice", "video"]

@dataclass
class CatalogEntry:          # one product in a company's catalog (from onboarding)
    product_id: str; name: str; brand: str = ""; aliases: list[str] = []
    description: str = ""; ref_image: bytes | None = None    # rep image from Unsiloed → image-match

@dataclass
class ProblemSignals:        # what the camera sees is wrong → drives the query
    error_codes: list[str] = []; indicators: list[str] = []; parts: list[str] = []
    damage: list[str] = []; summary: str = ""
    def to_query(self) -> str: ...

@dataclass
class IdentifyResult:        # output of perception (02)
    product_id: str | None; confidence: float; model_source: str   # ocr_label|visual_match|none
    brand: str = ""; model: str = ""; raw_text: str = ""
    candidates: list[Candidate] = []; needs_confirmation: bool = False
    problem: ProblemSignals = ProblemSignals()

@dataclass
class Chunk:                 # a retrieved doc chunk (03)
    id: str; text: str; metadata: dict[str, str]; score: float | None = None; source: str = ""

@dataclass
class Company:              # tenant (06) — the embed snippet carries the company key
    company_id: str; name: str; index_name: str            # Moss index "clutch-<company>"
    catalog: list[CatalogEntry]; api_key: str

@dataclass
class SupportSession:        # one customer session (04/05)
    session_id: str; company_id: str; modality: Modality
    product_id: str | None = None; phase: str = "active"; history: list[dict] = []

@dataclass
class RetrievalQuery:        # 04 → 03
    company_id: str; text: str; product_id: str | None = None; need: list[str] = []

@dataclass
class Answer:                # 03/04 → 05
    text: str; citations: list[dict] = []    # {doc, section, score} — grounding shown to user
```

## 4. The grounding guarantee (the core value)
The agent **only states facts pulled from the company's docs** — it composes from retrieved
`Chunk`s and cites them (`Answer.citations`), never improvises. If retrieval is empty/low-score,
it says so or escalates — it does not invent. (This replaces the dropped FACT layer; the guarantee
is "grounded in retrieved docs," surfaced as citations.)

## 5. Multi-tenancy (baseline)
One Moss index per company (`clutch-<company>`), one catalog per company. The **embed snippet
carries the company key**; every session is scoped to that company's index + catalog. No
cross-company retrieval, ever. Company sign-in is **Firebase Auth**; the tenant registry + onboarding
store is **Firestore**, surfaced through the `src/api/` backend (06).

## 6. Stack / sponsors — model & modality routing
**Every model named below is a DEFAULT, not a hard dependency.** Clutch treats end models (LLM,
vision, STT, TTS, parse) as commodities that *will* change — each sits behind a provider-agnostic
swap point so a vendor is replaced with a config/adapter change, never a rewrite. See the
**model-portability guarantee** (§7).

| Layer | Default model | Swap point (how it's replaced — never a caller rewrite) |
|---|---|---|
| Transport (chat/voice/video) | **LiveKit** | infra, not a "model" — framework-level |
| Reasoning brain (grounded answers) | **MiniMax** LLM | logical `reason.compose` name on the TrueFoundry Gateway → repoint to any OpenAI-compatible model |
| Vision (See — device + problem) | **Qwen-VL** | `VisionProvider` protocol (02) + logical `vision.identify` on the Gateway |
| Voice STT (Talk + See) | **Cartesia** STT | `STTProvider` adapter (05), a LiveKit plugin → swap to any LiveKit-supported STT |
| Voice TTS (Talk + See) | **Cartesia** TTS | `TTSProvider` adapter (05), a LiveKit plugin → swap to any LiveKit-supported TTS |
| Parse | **Unsiloed** | `parse_doc` adapter (01) → swap parser behind the same chunk contract |
| Retrieval | **Moss** (host) | `retrieve()` interface (03) with local-cosine fallback |
| Gateway / governance | **TrueFoundry** AI Gateway | the swap substrate itself (routes LLM + vision); Agent Gateway (stretch) |

The pipeline: **STT → reasoning LLM (grounded in Moss) → TTS**, with **vision** feeding the brain on
the See path. LLM + vision calls route through the TF gateway (swap by logical name); STT/TTS run as
LiveKit plugins behind speech adapters (swap by adapter); parse + retrieval sit behind their own
interfaces. Default vendors today: MiniMax / Qwen / Cartesia / Unsiloed / Moss.

## 7. Model-portability guarantee (plug-and-play)
End models are commodities and **will** be swapped (cost, latency, quality, availability, sponsor
changes). This is a first-class guarantee, equal in standing to grounding (§4): the architecture
isolates that churn so a model swap touches **one adapter or one config line**, never the callers.
- **No component imports a provider SDK directly.** LLM + vision route through the TrueFoundry
  Gateway by *logical task name* (`reason.compose`, `vision.identify`, `embed`) — swapping a model is
  a gateway/config change. STT, TTS, vision, and parse each sit behind a small **provider protocol**
  (`STTProvider`/`TTSProvider` in 05, `VisionProvider` in 02, `parse_doc` in 01) with a mock + a real
  impl, so a vendor swap is one new adapter, not edits in every caller.
- **Config names roles, not vendors.** `src/config.py` keys are by *role* — `reason_model`,
  `vision_model`, `stt_provider`, `tts_provider` — **never** `qwen_*`/`cartesia_*`/`minimax_*`, so the
  wiring never bakes a vendor in (06 §4).
- **Contracts are model-neutral.** The shared dataclasses (§3) carry no provider-specific fields;
  e.g. `IdentifyResult.model_source` describes the *signal* (`ocr_label`/`visual_match`), not the vendor.
- **Every model layer has a fallback** (per-HLD §6): gateway auto-failover for LLM/vision, an alternate
  provider / LiveKit default for voice, local-cosine for retrieval, a deterministic mock for all.

Reading rule for the rest of the HLDs: wherever a vendor is named (MiniMax, Qwen, Cartesia, Unsiloed),
read it as **"the default behind this swap point,"** not a fixed choice.

## 8. Reuse (built code)
- **Unsiloed → Moss ingestion** = the corpus build. *Built* in `ingest/`.
- **Perception (`identify`)** — built in `src/perception/` (closed-set product ID + problem signals).
- **Retrieval/grounding + LiveKit** build on the Moss SDK + the `livekit-moss-vercel` scaffold (`moss-research/`).
- Not used: `find_manual`/MCP, FACT, AWS. MiniMax = reasoning brain; Cartesia = voice I/O.

## 9. Open questions
- Per-company single index + `product` metadata filter vs per-product indexes (default: per-company, product-filtered).
- Where the embed snippet authenticates the company (public widget key vs signed token).
- Whether "See" can hand off to a human (Agent Gateway handoff) — Tier-2.
