# Clutch — Work Division & the Connection Plan

**Status:** Draft v0.1 · **Read after [00 — Overview](../00-overview.md).** This is the *build plan*:
how the seven HLDs split into **four equal, non-colliding sections**, how **each `src/` subfolder is
self-contained**, and how a single **adapter layer** (`src/app.py`) connects them all at the end.

---

## 1. The principle: one spine, four limbs, one wiring file

The architecture already isolates change behind contracts and swap points (00 §3, §7). We exploit
that to parallelize the build:

- **The Spine** — the shared, *frozen* contracts (`src/contracts.py` + `src/events.py`). These are
  plain data shapes (dataclasses + the event JSON schema), **not** interfaces. Every subfolder may
  import the Spine and **nothing else of another section's**.
- **Four Limbs** — four disjoint sets of directories, one per engineer. **No `src/` subfolder imports
  a sibling subfolder**; no two engineers edit the same file. Each Limb is built and tested in
  isolation against simple **mock functions** standing in for its neighbours.
- **The wiring file** — a single file (`src/app.py`) that imports every subfolder and **connects them
  by passing each its dependencies** (the real functions in place of the mocks). This is the *adapter
  layer*; it is the only file that imports all four sections and it holds **no business logic** — just
  construction + wiring.

> Result: each engineer can reach "done + tested" without the others, and the final integration is a
> single, mechanical wiring pass in `src/app.py` — not a merge war.

## 2. Section ↔ engineer ↔ HLD map

| Section | Engineer | Owns (HLDs) | Owns (directories) |
|---|---|---|---|
| **A — Experience** | **Arman** (FE/Design) | 05 (client), 01 (console UI) | `console/`, `widget/` |
| **B — Platform** | **Arham** (Infra) | 01, 06 | `src/config.py`, `src/gateway.py`, `src/tenancy.py`, `src/onboarding/`, `src/api/`, `ingest/` (reuse), `deploy/` |
| **C — Cognition** | **Tonmoy** (Vision/Orch) | 02, 04 | `src/perception/`, `src/agent/` |
| **D — Inference** | **Fardin** (Serving) | 03, 05 (server) | `src/retrieval/`, `src/speech/`, `src/realtime/` |

Each section has its own detailed brief:
[11 — Arman](11-arman-frontend-experience.md) ·
[12 — Arham](12-arham-platform-infra.md) ·
[13 — Tonmoy](13-tonmoy-vision-orchestrator.md) ·
[14 — Fardin](14-fardin-inference-retrieval-voice.md).

## 3. The Spine (frozen shared contracts — owned by all, governed by Arham)

These artifacts are **frozen at kickoff**. A change requires an all-hands sign-off because every
section depends on them. They are the *only* things every section may import from outside its own
directories.

1. **`src/contracts.py`** — the dataclasses in 00 §3 (`CatalogEntry`, `ProblemSignals`,
   `IdentifyResult`, `Chunk`, `Company`, `SupportSession`, `RetrievalQuery`, `Answer`, `Modality`).
   *Already started.* Freeze it on day 1. These are data shapes only — no interfaces, no logic.
2. **`src/events.py`** — the data-channel event schema (05 §4): the typed JSON the server publishes
   and the widget renders (`answer` / `confirm` / `voice_state` / `latency`). It is a **shared
   contract**, so it lives at the `src/` root next to `contracts.py` (not inside any Limb), mirrored
   by `widget/src/events.ts` (TS). Fardin's `realtime/` *produces* these; Arman's widget *renders*
   them.
3. **The seam signatures** in §4 below — the exact function names + shapes neighbours call. These are
   documented agreements, **not** enforced `Protocol`/interface classes: a subfolder simply exposes
   the named functions and others call them (or, for cross-section calls, receive them via §7).

## 4. The Seams — one single path between each layer

Every adjacency between two sections is exactly **one** function signature. There is no second
back-channel; a layer talks to its neighbour only through its seam. This is the "single path of
connection between each layer" guarantee.

| # | Seam (the single path) | Producer → Consumer | Signature (frozen) |
|---|---|---|---|
| S1 | **Browser ⇄ Realtime** | Arman ⇄ Fardin | data-channel events (`events.py` ↔ `events.ts`) + `POST /connection-details(company_key, modality) -> token` |
| S2 | **Realtime ⇄ Agent** | Fardin ⇄ Tonmoy | `agent.on_user_turn(text) -> Answer?` · `agent.on_identify(IdentifyResult) -> Answer?` |
| S3 | **Agent ⇄ Perception** | Tonmoy internal → Tonmoy | `identify(frames, catalog, *, provider) -> IdentifyResult` |
| S4 | **Agent ⇄ Retrieval** | Tonmoy ⇄ Fardin | `retrieve_for(RetrievalQuery) -> Chunk[]` · `compose(query, chunks) -> Answer` |
| S5 | **All ⇄ Gateway** | Fardin/Tonmoy ⇄ Arham | `route(task) -> model` · `gateway_client(cfg) -> OpenAICompatClient` |
| S6 | **All ⇄ Tenancy** | everyone ⇄ Arham | `resolve_company(key) -> Company` · `scope_session(key, modality) -> SupportSession` |
| S7 | **Onboarding ⇒ Runtime** | Arham → C/D | `Company{index_name, catalog, api_key}` record |
| S8 | **Realtime ⇄ Perception** | Fardin → Tonmoy | sampled `frames: list[bytes]`; realtime calls Perception's `FrameGate.should_process(frame)` (02) before S3 `identify` — the VLM fires only on a meaningful view change |
| S9 | **Console UI ⇄ Console API** | Arman ⇄ Arham | `/api/*` REST (products · docs · process · status · embed) + Firebase ID token; files via Firebase Storage |

Rule: if you need data from another section that isn't on its seam, you don't reach in — you raise it
to the group and the seam is extended (and re-frozen). No private side-doors.

## 5. Each subfolder is self-contained — connected by passing functions

There are **no interface classes**. Each `src/` subfolder imports only the Spine (`contracts.py` /
`events.py`) and exposes a few **plain public functions**. When one subfolder needs another, it does
**not** import it — it **receives the function** as an argument, and `src/app.py` (§7) supplies the
real one. For solo work you pass a one-line **mock function** instead. That is the whole "connect
later via adapters" mechanism — ordinary Python, no `Protocol` layer.

| Subfolder | Owner | Exposes (public functions) | Receives (passed in by `app.py`) | Build-against mock |
|---|---|---|---|---|
| `tenancy.py` + `onboarding/` | **Arham** | `resolve_company`, `scope_session`, `onboard` | external vendors only | `stub_tenancy` (one fake `Company` + a real `clutch-demo` index) |
| `gateway.py` | **Arham** | `route`, `gateway_client` | external vendor only | `mock_gateway` (canned completions) |
| `api/` (`src/api/`) | **Arham** | `/api/*` routes, `/connection-details` | Firebase verify, `onboard`, `resolve_company` | Firebase emulator + `pre_parsed_json` |
| `retrieval/` | **Fardin** | `load_index`, `retrieve_for`, `compose` | `gateway_client` | `local_cosine` + `template_compose` |
| `perception/` | **Tonmoy** | `identify` | `gateway_client` (vision) | `mock_vision` (built) / `canned_identify` |
| `agent/` | **Tonmoy** | `ConversationAgent` (`on_user_turn`, `on_identify`) | `retrieve`, `compose` | `mock_retrieve` / `mock_compose` |
| `speech/` | **Fardin** | `get_stt`, `get_tts` | `cfg` | `mock_stt` / `mock_tts` |
| `realtime/` | **Fardin** | `run_session` (the voice loop) | the `agent` instance, `identify`, `stt`, `tts` | `mock_agent`, `canned_identify` |
| `console/` | **Arman** | (browser admin app) | `/api/*` + Firebase Auth/Storage | `mock_api` (canned products/status/embed) |
| `widget/` | **Arman** | (browser embed widget) | the live event stream + token | `mock_transport` (scripted events) |

Read it two ways:
- **"Receives" column** = a subfolder's inbound dependencies. Each is one passed-in function with a
  trivial mock → the subfolder builds and tests alone.
- **"Exposes" column** = what a subfolder hands the wiring file so others can receive it — plugging in
  the real function changes no caller.

The only mutual dependency is C↔D and it is symmetric: Tonmoy's `agent/` receives Fardin's
`retrieve`/`compose`; Fardin's `realtime/` receives Tonmoy's `agent`/`identify`. Each builds against
the other's mock, so neither blocks the other.

## 6. Independence proof — each section builds & demos alone

| Section | Inbound deps (received) | Mock that satisfies it | Can reach "done + tested" without anyone? |
|---|---|---|---|
| **A — Arman** | live events + token | `mock_transport` (scripted events, stub token) | **Yes** — full UI on replayed events |
| **B — Arham** | external vendors only | `pre_parsed_json`, real Moss/`clutch-demo`, `mock_gateway` | **Yes** — substrate has no section deps |
| **C — Tonmoy** | `gateway_client`, `retrieve`/`compose`, session catalog | `mock_gateway`, `local_cosine`+`template_compose`, `stub_tenancy` | **Yes** — FSM + `identify` green on mocks |
| **D — Fardin** | `gateway_client`, `agent`, `identify` | `mock_gateway`, `mock_agent`, `canned_identify` | **Yes** — voice loop + retrieval on mocks |

Each section therefore has a **standalone runnable harness** (`scripts/run_<section>_demo.py` or, for
Arman, a dev page) that exercises its Limb end-to-end against mocks — the acceptance gate before
integration day.

## 7. The wiring file — `src/app.py` (the adapter layer, owned by Arham)

`src/app.py` is the **one place** the four subfolders are connected. Every Limb depends only on the
functions passed into it; `app.py` decides — per `Config` — whether to pass the **real** function or
its **mock**. No subfolder imports a sibling; `app.py` is the only file that imports all of them and
it just passes functions around:

```python
def build_app(cfg):
    # B (Arham) — the substrate, wired first
    tenancy = real_tenancy(cfg) if cfg.real("tenancy") else stub_tenancy
    gateway = real_gateway(cfg) if cfg.real("gateway") else mock_gateway

    # D (Fardin) — retrieval + speech
    retrieve, compose = make_retrieval(cfg, gateway) if cfg.real("retrieval") else (local_cosine, template_compose)
    stt, tts          = get_stt(cfg), get_tts(cfg)

    # C (Tonmoy) — perception + agent.
    #   the agent RECEIVES retrieve/compose; agent/ never imports retrieval/
    identify = make_identify(gateway) if cfg.real("vision") else canned_identify
    def make_agent(session):
        return ConversationAgent(session, retrieve=retrieve, compose=compose)

    # D (Fardin) — realtime RECEIVES the agent + identify + speech;
    #   realtime/ never imports agent/ or perception/
    return run_session(tenancy, make_agent, identify, stt, tts)
```

**Mock-first** (00 §7): every passed-in function has a one-line mock, so each engineer wires their
Limb against the *others' mocks* from day 1; `app.py` flips each mock → the real function **one at a
time** (`cfg.real("…")`), so connecting is incremental and any seam reverts to its mock if it breaks.

## 8. Non-collision rules (same repo, four hands)

1. **Disjoint directories.** Ownership in §2 is exclusive. PRs that touch another section's dir get
   bounced unless it's the Spine.
2. **Spine changes are PRs of their own**, reviewed by all four, never bundled with feature work.
3. **`src/app.py` is append-only per section** — each engineer adds their one wiring line; merge
   conflicts there are trivial because the blocks are independent.
4. **No sibling imports.** A `src/` subfolder may import only the Spine; needing a sibling means you
   take it as a passed-in argument (§5), not an `import`.
5. **Tests live with their Limb** (`tests/<section>/…`); each section's suite must pass against mocks
   before integration day.
6. **`src/config.py` keys are role-named** (00 §7) — adding a config key is a Spine-adjacent PR
   (Arham reviews), so two sections never invent conflicting keys.

## 9. Build order & integration sequence (how the four connect)

```
Day 0  ── freeze Spine: contracts.py + events.py + seam signatures (all)
Phase 1 (parallel, against mocks):
   A  widget renders mock answer/confirm/voice/latency events, publishes mock track + intents
   B  config + gateway(route) + tenancy(resolve/scope) + onboarding(Company) green
   C  identify() (built) + ConversationAgent FSM green on mock retrieve/compose/vision
   D  retrieve/compose on real Moss; stt/tts adapters + LiveKit voice loop on mock agent
Phase 2 (connect seam-by-seam in src/app.py):
   S5+S6+S7  B online: real gateway/tenancy/Company feed C & D
   S4        D's real retrieve/compose passed into C's agent      → grounded answers real
   S3+S8     C's identify on real frames from D's sampler         → "See" real
   S2        D's voice loop drives C's real agent                 → Talk real
   S1        A's widget swaps mock events for D's live channel     → end-to-end
Phase 3  ── demo hardening: failure-mode drills (each HLD §6), latency badge real number
```

Each connection in Phase 2 is one seam, independently verifiable, and revertible to the mock if it
breaks — so integration never blocks on a single failing layer.

## 10. Why this is clean & scalable

- **One seam per layer** → the connection surface is countable (8 seams), each a frozen signature.
- **A mock for every passed-in function** → any layer can be developed, demoed, or swapped alone.
- **Swap points at every vendor edge** (gateway logical names, `get_stt`/`get_tts`, `parse_doc`,
  `retrieve`) → swapping a model/vendor is one `src/app.py` line, never a cross-section edit (00 §7).
- **No subfolder imports a sibling** — cross-section needs are passed in by `src/app.py`, so each
  subfolder depends on plain functions, not a neighbour's code; all four reach "done + tested" alone
  (§6) and connect with no caller edits.
- **Adding a 5th engineer / 5th layer** = add a directory, its public functions, a mock, one
  `src/app.py` wiring line. The system scales by the same pattern it was built on.
