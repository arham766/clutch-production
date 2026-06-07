# Fixalong LLD 13 — Session Orchestrator

**Implements:** HLD 13 · **Module(s):** agent/orchestrator.py, agent/session.py · **Build tier:** 0 · **Status:** Draft v0.1

## 1. Responsibility
The controller **above** the three flows. Per session and continuously, it (a) **routes** a `ProductIdentity` to `ONBOARD | LIVE | CLARIFY`, (b) owns the **session-lifecycle FSM** and the cold→hot handoff, (c) **re-routes** mid-session when the user points at a different machine (debounced/hysteresis), and (d) applies **cross-cutting degradation** routing. It never decides repair *content* (that's LLD 05/12); it decides *context*: which model's index is loaded/trusted and whether we're cold or hot. Glue only — it reuses LLD 10 (onboard), LLD 11 (index cache + load), LLD 12 (live loop).

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/session.py` | `SessionState` (per `contracts.py` §67), `Lifecycle` Literal, `Route` Literal, `new_session(stream)->SessionState` |
| `agent/orchestrator.py` | `run_session(stream)`, `route(identity)->Route`, `onboard_then_ready(identity)->str`, `go_live(index_name, identity)->None`, `on_identity_change(new_identity)->None`, `set_degraded(flag)`, `clear_degraded(flag)`, `_Debouncer`, `_OnboardSingleFlight` |

## 3. Dependencies
- **Internal:** `agent.contracts` (SessionState, ProductIdentity, RepairState); `agent.onboarding.onboard` (LLD 10 — `identify_product`, `onboard`); `agent.knowledge.verify`/registry (LLD 11 — `index_exists`, `load_session_index`); `agent.voice.livekit_agent` + `agent.state.machine` (LLD 12 — `go_live` target).
- **Libs/SDKs:** `asyncio` (single-flight, debounce, the live task); LiveKit Agents SDK (the `stream`); optional TrueFoundry SDK for phase/route observability.
- **Env vars:** `KB_ISSUER` (trusted issuer DID, passed to LLD 12), `ID_THRESHOLD`, `REROUTE_FRAMES`, `REROUTE_MARGIN`, `REROUTE_DEBOUNCE_MS` (all have code defaults, §7).

## 4. Data structures (beyond shared contracts)
`SessionState` is **reused as-is** from `contracts.py` (do not redefine). `lifecycle` ∈ `idle|identifying|onboarding|ready|live|degraded|ended`; `degraded` is a list of overlay flags (empty = healthy), independent of `lifecycle`.

```python
Route     = Literal["ONBOARD","LIVE","CLARIFY"]
Lifecycle = Literal["idle","identifying","onboarding","ready","live","degraded","ended"]

@dataclass
class _ReroutePending:        # debounce accumulator (one per orchestrator)
    model: str; count: int; first_seen_ms: float; max_conf: float
```

`_OnboardSingleFlight`: `dict[str, asyncio.Future[str]]` keyed by `model_slug` → resolves to `index_name`. `degraded` is the source of truth; `lifecycle == "degraded"` is set only when the session is **unusable** (no index), per §8.

## 5. Key functions

### `route(identity) -> Route` — the core decision (latency = a cache check, §9)
```python
def route(identity: ProductIdentity) -> Route:
    if identity.confidence < ID_THRESHOLD:        # vision unsure which machine
        return "CLARIFY"
    if index_exists(slug(identity.model)):        # LLD 11 registry/list_indexes — ms, in-proc
        return "LIVE"                             # cache hit → straight to hot loop
    return "ONBOARD"                              # cold path first, then live
```
`slug(model)` = lowercase, non-alnum→`-`, collapse repeats → `fixalong-<slug>` index name. The whole "onboard vs live" rule is: **low confidence → ask; known model → live; else onboard.**

### `run_session(stream)` — top-level entry, drives the FSM
```python
async def run_session(stream) -> None:
    s = new_session(stream)                       # lifecycle=idle
    _set(s, "identifying")
    s.identity = await identify_product(stream.frames)   # LLD 10 (Qwen+OCR)
    match route(s.identity):
        case "CLARIFY":
            s.identity = await ask_user_for_model(stream) # voice/UI prompt
            return await _resume_with(s)                  # one re-route, no recursion blowup
        case "ONBOARD":
            s.active_index = await onboard_then_ready(s)  # LLD 10 → LLD 11 write
        case "LIVE":
            _set(s, "ready")
            s.active_index = load_session_index(slug(s.identity.model))  # LLD 11, pre-warm
    await go_live(s)                              # LLD 12, trusted_issuers={KB_ISSUER}
```
`_resume_with` re-applies `route` once on the clarified identity (ONBOARD or LIVE only; a second CLARIFY → DEGRADED, §8).

### `onboard_then_ready(s) -> index_name` — single-flight per model (→ LLD 10/11)
```python
async def onboard_then_ready(s) -> str:
    key = slug(s.identity.model)
    if key in _inflight:                          # another session already onboarding this model
        return await _inflight[key]               # await the SAME index, don't re-run
    fut = _loop.create_future(); _inflight[key] = fut
    try:
        _set(s, "onboarding")                     # UI: "getting the manual… reading it…"
        r = await onboard(s.stream.frames, session=s, emit=_emit_progress)   # LLD 10 → OnboardResult
        s.identity = r.identity
        if r.cache_hit:                           # index already exists → nothing to persist
            index = index_name(r.model_slug)      # LLD 11 registry
        else:
            index = persist(r.chunks, r.model_slug)   # LLD 11 sign+inject (write)
        fut.set_result(index)
        return index
    except OnboardError as e:
        fut.set_exception(e); raise               # caller → DEGRADED (§8)
    finally:
        _inflight.pop(key, None)
```
On success → `_set(s, "ready")` then `load_session_index` (pre-warm) before `go_live`.

### `go_live(s) -> None` (→ LLD 12)
```python
async def go_live(s) -> None:
    load_session_index(s.active_index)            # idempotent pre-warm; ~3–5 ms (LLD 11)
    _set(s, "live")
    s.kb_issuer = KB_ISSUER
    await live_loop(s, trusted_issuers={KB_ISSUER},   # LLD 12 owns the turn loop
                    on_identity=on_identity_change,    # LLD 12 streams cheap IDs back
                    on_degrade=set_degraded)
```

### `on_identity_change(new_identity)` — continuous re-route, debounced (§7)
```python
def on_identity_change(new: ProductIdentity) -> None:
    cur = S.identity
    if slug(new.model) == slug(cur.model): _pending = None; return   # same machine, reset
    p = _pending
    margin_ok = new.confidence >= cur.confidence + REROUTE_MARGIN
    if p is None or p.model != slug(new.model):
        _pending = _ReroutePending(slug(new.model), 1, now_ms(), new.confidence); return
    p.count += 1; p.max_conf = max(p.max_conf, new.confidence)
    consistent = p.count >= REROUTE_FRAMES
    debounced  = now_ms() - p.first_seen_ms >= REROUTE_DEBOUNCE_MS
    if consistent and debounced and margin_ok:
        _pending = None
        schedule(_reroute(new))               # pause LIVE → route(new) → resume LIVE on new index
```
`_reroute` calls `route(new)`: LIVE (cache hit, instant) or ONBOARD (build new index, keep prior model LIVE meanwhile per §8), then swaps `active_index` and resumes — **session never ends**.

## 6. Control flow / sequence
1. `run_session` → `identifying` → `identify_product`.
2. `route`: CLARIFY (ask, re-route once) | LIVE (`ready`→pre-warm) | ONBOARD (`onboarding`→single-flight build→`ready`→pre-warm).
3. `go_live` → `live`; LLD 12 runs turns and streams cheap IDs to `on_identity_change` + faults to `set_degraded`.
4. Debounced model change → `_reroute` (back to step 2 without ending).
5. User ends → `ended` → repair receipt (LLD 12 §13). Any unrecoverable cross-cut fault → `degraded` overlay (recovers when cleared).

## 6a. FSM transition table
| From | Event | Guard | To | Action |
|---|---|---|---|---|
| idle | session start | — | identifying | `identify_product` |
| identifying | id done | conf≥thr & index_exists | ready | `load_session_index` (pre-warm) |
| identifying | id done | conf≥thr & !index_exists | onboarding | single-flight `onboard` |
| identifying | id done | conf<thr | identifying | CLARIFY → ask_user → re-route once |
| onboarding | index built | — | ready | pre-warm |
| onboarding | onboard fails | — | degraded | flag `onboard`; no index |
| ready | pre-warm done | — | live | `live_loop` (trusted_issuers) |
| live | identity change | N frames + margin + debounce | onboarding/ready | `_reroute(new)` (no end) |
| live | `fresh_until` lapse | active model stale | onboarding | re-onboard to refresh |
| live | user ends | — | ended | repair receipt |
| any | cross-cut fault | session unusable | degraded | `set_degraded(flag)` |
| degraded | fault clears | session usable | live | `clear_degraded(flag)` |

## 7. Config & tuning
| Param | Env | Default | Meaning |
|---|---|---|---|
| `ID_THRESHOLD` | `ID_THRESHOLD` | `0.55` | min `identity.confidence` to act (else CLARIFY) |
| `REROUTE_FRAMES` | `REROUTE_FRAMES` | `5` | consecutive consistent frames on the **new** model |
| `REROUTE_MARGIN` | `REROUTE_MARGIN` | `0.10` | new conf must beat current by this (hysteresis) |
| `REROUTE_DEBOUNCE_MS` | `REROUTE_DEBOUNCE_MS` | `1500` | min wall time before a switch commits |
A stray high-confidence frame can't switch machines: it must win N frames, the time gate, **and** the confidence margin. Same-model frames reset `_pending`.

## 8. Error handling & fallbacks (fail-safe)
| Fault | Routing |
|---|---|
| identity low conf | CLARIFY before onboarding (never fetch the wrong manual) |
| onboarding fails entirely | `set_degraded("onboard")` → DEGRADED live, no index: agent relays only user-narrated facts, **no grounded steps** |
| Moss/FACT unusable | LLD 11 local fallback first; if still unusable → `set_degraded("knowledge")`, agent stays silent vs guessing |
| vision down | stay LIVE; tell LLD 12 to use voice narration (same `update`) — `degraded=["vision"]` but lifecycle stays `live` |
| signing-key mismatch | everything reads ESCALATE → surface "knowledge not trusted," prompt re-onboard |
| re-route onboard stalls | keep LIVE on the **previous** model; UI "still getting the new manual…"; commit only when ready |
| cache/registry down | treat as miss → onboard (slower, correct); never serve an untrusted index |
`set_degraded(flag)` adds to `s.degraded` and flips `lifecycle→"degraded"` only when no usable index exists; `clear_degraded(flag)` removes it and restores `live` when the list empties.

## 9. Latency / perf notes
- **`route` = ms**: one `index_exists` registry/list_indexes check, in-process (LLD 11 §9). Common path is LIVE → effectively instant.
- **Onboarding (cold)**: seconds (LLD 10/11), cache-miss only; behind the `onboarding` UX state.
- **Hot path**: orchestrator adds negligible overhead; `live_loop` is <10 ms/turn (LLD 12). Pre-warm `load_session_index` (~3–5 ms) happens once at READY so the first query is hot.
- Re-route on a cache hit is instant; on a miss it builds in the background while prior model stays LIVE.

## 10. Test plan
**Unit (mock `identify_product`, `index_exists`, `onboard`, `load_session_index`, `live_loop`):**
- `route`: conf<thr → CLARIFY; conf≥thr & index_exists True → LIVE; & False → ONBOARD. Boundary at exactly `ID_THRESHOLD`.
- `run_session`: each branch reaches `go_live` with correct `active_index`; LIVE branch never calls `onboard`; ONBOARD branch calls `onboard` then `load_session_index`.
- Single-flight: two concurrent `onboard_then_ready` for the same slug → `onboard` invoked **once**, both get the same index; different slugs → two runs.
- Debounce/`on_identity_change`: < N frames → no switch; N frames but within debounce window → no switch; N frames + margin fail → no switch; N + margin + debounce → exactly one `_reroute`; same-model frames reset `_pending`; assert no flapping under an alternating-frame stream.
- Degradation: `set_degraded("onboard")` (no index) → lifecycle `degraded`; `set_degraded("vision")` (index present) → lifecycle stays `live`, flag set; `clear_degraded` restores.
**Integration:**
- Cold→hot: unknown model → ONBOARD → READY (pre-warm) → LIVE; assert phase transitions emitted in order.
- Mid-session re-route: live on model A, stream consistent model-B IDs → pause→onboard/cache→resume on B's index, session **not** ended.
- Onboard failure → DEGRADED live; agent relays user facts, emits no grounded steps.
- FSM property test: drive random valid events, assert only table transitions occur and `degraded`/`lifecycle` invariant holds.

## 11. Build checklist
1. **(Tier 0)** `SessionState`/`Lifecycle`/`Route` in `session.py` + `slug()` + `_set()` phase setter w/ observability hook.
2. `route()` + unit tests (pure, mock `index_exists`).
3. `run_session()` skeleton: identifying → route → go_live; mock LLD 10/12.
4. `onboard_then_ready()` with `_OnboardSingleFlight`; concurrency test.
5. `go_live()` pre-warm + `trusted_issuers={KB_ISSUER}`; wire `on_identity`/`on_degrade` callbacks.
6. `on_identity_change()` + `_Debouncer`/`_ReroutePending`; debounce + hysteresis tests.
7. `set_degraded`/`clear_degraded` + DEGRADED overlay invariant; tests.
8. CLARIFY path (`ask_user_for_model` + `_resume_with`).
9. Integration tests (cold→hot, re-route, onboard-fail) + emit phase/route/degradation events to TrueFoundry (optional).
