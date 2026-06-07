# Fixalong LLD 06 — Output guardrail + MCP tools
**Implements:** HLD 06 · **Module(s):** agent/safety/guardrail.py, agent/safety/mcp.py · **Build tier:** 1 · **Status:** Draft v0.1

## 1. Responsibility
This module is Stage 2 of the safety system (HLD 06 §3): it takes a *drafted* spoken
instruction (composed by the State Machine, 05, from FACT-verified chunks only) and decides
whether it is **physically safe to speak**. It does **not** do FACT input verification — that is
LLD 11 (`verify_document`), which already ran upstream and attached `verdict` to each `Chunk`.
The guardrail enforces a concrete local hazard rule set and, when reachable, the TrueFoundry
output-guardrail; it **fails toward caution** (prepend the required warning, never go silent).
The same file's MCP half (`agent/safety/mcp.py`) owns gateway-mediated **dynamic tool
discovery** and `find_manual`, plus **model routing** (task→model handle) through the gateway.

## 2. Files & public surface
```
agent/safety/guardrail.py
  guardrail(draft: str, state: RepairState, retrieved: list[Chunk]) -> SafetyDecision
  _local_guardrail(draft, state, retrieved) -> SafetyDecision   # rule engine, always available
  _tf_guardrail(draft, ctx) -> SafetyDecision | None            # TrueFoundry call; None on miss
  RULES: list[Rule]                                             # ordered local rule set
agent/safety/mcp.py
  discover_tools(context: dict) -> list[ToolSpec]               # gateway, policy-scoped
  call_tool(name: str, args: dict) -> dict                      # gateway, auth+audit
  find_manual(brand: str, model: str, object_type: str, *, scope: ProductIdentity | None = None) -> ManualRef
    # scope (optional): session identity → lets the gateway surface enterprise/private manual servers
  route(task: str) -> ModelHandle                               # task -> model via gateway
  _client() -> MCPGatewayClient                                 # cached streamable-http client
```
`SafetyDecision`, `ToolSpec`, `ModelHandle`, `Rule` are defined in §4. `ManualRef`,
`RepairState`, `Chunk` are shared contracts (README §"Shared contracts") — not redefined here.

## 3. Dependencies
- **Libs:** `mcp` (`mcp.client.streamable_http.streamablehttp_client` + `ClientSession`),
  `httpx` (TrueFoundry REST guardrail + manufacturer fallback search), `anthropic`
  (direct-Claude fallback for rewrite when gateway down), `re`, `asyncio`.
- **Internal:** `agent/contracts.py` (`ManualRef`, `RepairState`, `Chunk`). Reuses
  `ingest/fact_moss.py` only transitively (verdicts already on chunks). No import of FACT here.
- **Env:** `TRUEFOUNDRY_API_KEY`, `TRUEFOUNDRY_GATEWAY_URL` (AI gateway base, OpenAI-compatible),
  `TRUEFOUNDRY_MCP_URL` (MCP gateway streamable-http endpoint), `TRUEFOUNDRY_GUARDRAIL_ID`
  (named output guardrail, optional), `ANTHROPIC_API_KEY` (direct fallback),
  `FIXALONG_ROUTE_MAP` (optional JSON override of task→model).

## 4. Data structures (beyond shared contracts)
```python
@dataclass
class SafetyDecision:
    allowed: bool                       # may the draft be spoken as-is?
    reason: str                         # human-readable, shown as trust chip (09)
    safe_rewrite: str | None = None     # if not allowed: the text to speak instead
    required_warnings: list[str] = field(default_factory=list)  # prepended verbatim
    source: str = "local"               # "local" | "truefoundry" | "merged"

@dataclass
class Rule:
    id: str
    risk: str                           # hazard tag this rule keys on
    matches: Callable[[str, RepairState, list[Chunk]], bool]
    required_precondition: str | None   # phrase that MUST already be in the draft
    warning: str | None                 # warning to inject if precondition absent
    block: bool = False                 # True = never speak (tool-use on energized parts)

@dataclass
class ToolSpec:
    name: str; description: str; input_schema: dict; server: str

@dataclass
class ModelHandle:
    model: str; base_url: str; api_key: str   # OpenAI-compatible gateway target
```

## 5. Key functions

### 5.1 `guardrail(draft, state, retrieved)` — the orchestrator
```
ctx = build_ctx(draft, state, retrieved)        # see §6 for ctx shape
local = _local_guardrail(draft, state, retrieved)   # ALWAYS run — source of truth on failure
if local.block_hard:                            # rule with block=True hit -> never speak
    return local                                # (allowed=False, safe_rewrite=escalation line)
tf = None
try:
    tf = _tf_guardrail(draft, ctx)              # may return None (no guardrail configured) or raise
except (httpx.HTTPError, TimeoutError) as e:
    log.warning("TF guardrail down, local-only: %s", e)   # HLD 06 §7 fallback
return _merge(local, tf)                         # union of warnings; stricter allowed wins
```
`_merge`: `allowed = local.allowed AND (tf.allowed if tf else True)`;
`required_warnings = dedupe(local + tf)`; if either supplies a `safe_rewrite`, keep the
**TrueFoundry** one when present (it saw the full policy), else local's; `source="merged"`.

### 5.2 `_local_guardrail` — the rule-eval algorithm (deterministic, hot-path)
```
hazards = {h["type"] for h in state.hazards} | _risk_from_chunks(retrieved)
fired, warnings, hard_block = [], [], False
draft_l = draft.lower()
for rule in RULES:
    if rule.risk not in hazards:        continue
    if not rule.matches(draft_l, state, retrieved):   continue
    if rule.block:                                      # e.g. tool-use on energized part
        hard_block = True; fired.append(rule.id); continue
    if rule.required_precondition and rule.required_precondition not in draft_l:
        warnings.append(rule.warning); fired.append(rule.id)
if hard_block:
    return SafetyDecision(False, "blocked: "+",".join(fired),
                          safe_rewrite=_escalate_line(state), source="local")
if warnings:
    rewrite = " ".join(warnings) + " " + draft        # FAIL TOWARD CAUTION: prepend, don't drop
    return SafetyDecision(False, "missing precondition: "+",".join(fired),
                          safe_rewrite=rewrite, required_warnings=warnings, source="local")
return SafetyDecision(True, "no hazard rule fired", source="local")
```
**Concrete local rule set** (`RULES`, evaluated in order — HLD 06 §3):
| id | risk | fires when draft… | requires / action |
|---|---|---|---|
| `unplug-before-wiring` | electrical | mentions wire/terminal/connector/solder AND state lacks `unplugged` | precondition `"unplug"`; warning *"Unplug the unit and wait two minutes first."* |
| `hot-surface-fuser` | heat | mentions fuser/heating element/just printed | warning *"The fuser can be hot enough to burn — let it cool."* |
| `no-tool-on-energized` | electrical | mentions pliers/screwdriver/probe AND NOT unplugged | **block=True** (tool use on energized parts) |
| `moving-parts-power-off` | moving_parts | mentions gear/roller/belt/fan AND power on | precondition `"power off"`; warning *"Turn the unit off so nothing moves while you reach in."* |
| `sharp-edge` | sharp | mentions blade/cutter/sheet-metal edge | warning *"Mind the sharp edge — wear a glove if you have one."* |
`_risk_from_chunks` reads each `Chunk.risk_level` and `chunk.metadata` warning text to widen
the hazard set even when the FSM state under-reports (defense in depth).

### 5.3 `_tf_guardrail(draft, ctx)` — TrueFoundry output guardrail call
Prefer the gateway's named guardrail if `TRUEFOUNDRY_GUARDRAIL_ID` is set; the gateway runs it
server-side and returns the decision JSON of HLD 06 §3:
```
POST {TRUEFOUNDRY_GATEWAY_URL}/v1/guardrails/{GUARDRAIL_ID}/validate
  headers: Authorization: Bearer {TRUEFOUNDRY_API_KEY}
  json: { proposed_response: draft, machine_type: ctx.machine_type,
          current_state: ctx.current_state, risk_flags: ctx.risk_flags,
          retrieved_warnings: ctx.retrieved_warnings }
-> { allowed, reason, safe_rewrite } -> SafetyDecision(source="truefoundry")
```
If `GUARDRAIL_ID` is unset, fall back to a guardrail-as-prompt through the routed
`safety check` model (§5.5): a stronger reasoning model is asked to return the same JSON shape,
parsed identically. Return `None` only when neither path is configured (pure local mode).

### 5.4 `discover_tools` / `find_manual` / `call_tool` — MCP gateway (streamable-http)
Tools are **never** hardcoded (HLD 06 §4). Discovery is a privileged, policy-scoped call:
```
async def discover_tools(context):
    async with streamablehttp_client(
        TRUEFOUNDRY_MCP_URL, headers=_mcp_headers(context)) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools          # gateway returns only policy-allowed tools
    return [ToolSpec(t.name, t.description, t.inputSchema, _server_of(t)) for t in tools]
```
`_mcp_headers(context)` carries `Authorization: Bearer {API_KEY}` plus context claims
(`x-fixalong-env`, `x-fixalong-risk`, `x-session-id`) so the gateway scopes the tool set
(e.g. `escalate_to_human` only for enterprise/high-risk — HLD 06 §4).
```
async def find_manual(brand, model, object_type, *, scope=None):
    tools = await discover_tools({"risk": "low", "scope": scope})
    if any(t.name == "find_manual" for t in tools):
        r = await call_tool("find_manual",
              {"brand": brand, "model": model, "object_type": object_type})
        return ManualRef(**r)                              # gateway audits the call
    return await _direct_manual_search(brand, model, object_type)   # §8 fallback
```
`call_tool` opens the same session and calls `session.call_tool(name, args)`, parsing the
structured result; every call is logged/audited by the gateway.

### 5.5 `route(task)` — model routing
`route` maps a task to a `ModelHandle` pointing at the OpenAI-compatible gateway (HLD 06 §4
table). Default map (overridable by `FIXALONG_ROUTE_MAP`):
```
fast conversational answer -> low-latency voice/chat model
visual part recognition    -> Qwen multimodal (LLD 04 owns the call)
safety check / guardrail   -> stronger reasoning model (Claude)
fallback answer            -> Bedrock backup provider
repair receipt             -> cheap text model
```
All handles share `base_url=TRUEFOUNDRY_GATEWAY_URL`; callers use the handle with any
OpenAI-compatible client. The gateway gives observability, rate limits, failover (HLD 06 §4).

## 6. Control flow / sequence
```
State Machine (05) composes draft from ACT/HEDGE chunks (REFUSE already dropped by LLD 11)
  -> build_ctx: machine_type=state.object["type"], current_state=state.phase,
                risk_flags=union(state.hazards, chunk risk), retrieved_warnings=chunk warnings
  -> guardrail(draft, state, retrieved)
       -> _local_guardrail (always)         [hard block? return now]
       -> _tf_guardrail (best effort)
       -> _merge
  -> SafetyDecision back to FSM: speak safe_rewrite if !allowed else draft (HLD 06 §3 last line)
```
MCP path is invoked from onboarding (LLD 10) and `find_manual`; `route` from any model caller.

## 7. Config & tuning
- `GUARDRAIL_TIMEOUT_S = 1.5` (output guardrail must not stall the voice turn).
- `MCP_TIMEOUT_S = 4.0` (discovery/find_manual are cold-path, onboarding only).
- `MCP_CACHE_TTL_S = 60` — cache `discover_tools` per `(env,risk)` to avoid per-turn handshakes.
- Rule matching is case-insensitive substring/regex; keep phrase lists in `RULES` (one place).
- `required_warnings` are prepended **verbatim** — they are short, spoken-friendly sentences.

## 8. Error handling & fallbacks (fail-safe — HLD 06 §7)
| Failure | Behavior |
|---|---|
| TF gateway / guardrail down or timeout | `_tf_guardrail` swallowed; **local rules only**; rewrite via direct `anthropic` if a rewrite is needed |
| Guardrail false-block (local precondition) | do **not** silence — prepend `required_warnings`, return `allowed=False` + `safe_rewrite` (caution, not silence) |
| Hard block (`no-tool-on-energized`) | `safe_rewrite=_escalate_line` — tell user to stop and unplug; never the original draft |
| MCP gateway down | `find_manual` falls back to `_direct_manual_search` (manufacturer site via `httpx`) then user-provided manual upload; `discover_tools` returns `[]` and caller degrades |
| Empty / untrusted retrieval | guardrail not the place to fabricate — returns `allowed=True` only if no rule fires; FSM still gates on verdicts (LLD 11) |
| `route` map miss | default to the `safety check` (Claude) handle; log unknown task |

## 9. Latency / perf notes
- `_local_guardrail`: pure Python, microseconds — runs every spoken turn (hot path).
- `_tf_guardrail`: one HTTP round-trip, budgeted `≤1.5 s`; on timeout we already have the local
  decision, so the turn never blocks on the gateway.
- MCP discovery/`find_manual`: cold path (onboarding); cached `60 s`. Not on the voice loop.

## 10. Test plan
- **Unit (local rules, mock nothing):** wiring draft without "unplug" → `allowed=False`,
  warning prepended; pliers + energized → hard block + escalate rewrite; fuser mention →
  hot-surface warning; clean draft, no hazards → `allowed=True`. Table-driven over `RULES`.
- **Unit (merge):** local-block + tf-allow → blocked (stricter wins); warnings deduped.
- **Unit (fail-safe):** `_tf_guardrail` raises `httpx.HTTPError` → result equals local-only.
- **Unit (MCP):** fake `ClientSession` returning a tool list → `discover_tools` maps to
  `ToolSpec`; gateway with no `find_manual` tool → `_direct_manual_search` invoked.
- **Integration:** stub TrueFoundry HTTP (`respx`) returning the §3 JSON → `source="merged"`;
  in-memory MCP server (`mcp` test harness) for `discover_tools`/`call_tool` round-trip.
- **Mock:** TrueFoundry HTTP, MCP `streamablehttp_client`/`ClientSession`, `anthropic` client.
  Never mock the local rule engine — it is the safety floor.

## 11. Build checklist (Tier-0 first)
1. `SafetyDecision`, `Rule`, `ToolSpec`, `ModelHandle` dataclasses + `RULES` table.
2. `_local_guardrail` + `_risk_from_chunks` + `_escalate_line`; unit-test the table. **(Tier-0: ships safety with zero network.)**
3. `build_ctx` and `guardrail` orchestrator with TF-absent path returning local only.
4. `_tf_guardrail` REST call + JSON parse + `_merge`; env wiring (`TRUEFOUNDRY_*`).
5. `mcp.py`: `_client`/`_mcp_headers`, `discover_tools`, `call_tool`, cache.
6. `find_manual` (gateway tool) + `_direct_manual_search` fallback + user-upload path.
7. `route` + default map + `FIXALONG_ROUTE_MAP` override.
8. Direct-`anthropic` rewrite fallback for gateway-down rewrites; integration tests.
