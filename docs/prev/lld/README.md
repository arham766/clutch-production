# Fixalong LLDs — conventions, template & shared contracts

Low-level designs for each part. Every LLD here is **implementation-ready**: concrete file
layout, data structures, function signatures, algorithms, libraries, config, errors, and a
test plan. Read the matching **[HLD](../README.md)** first for the *why*; the LLD is the *how*.

> All LLDs follow the template and conventions in this file. Restate nothing that's here —
> reference it.

## Index

| LLD | Part | HLD |
|---|---|---|
| [01](01-ingestion.md) | Ingestion (manual → chunks) | [01](../01-ingestion-and-repair-graph.md) |
| [02](02-retrieval.md) | Moss retrieval (query mechanics) | [02](../02-retrieval-moss.md) |
| [03](03-voice-livekit.md) | Voice / LiveKit loop | [03](../03-voice-livekit.md) |
| [04](04-perception.md) | Perception: KriNein gate + Qwen | [04](../04-perception-vision-qwen.md) |
| [05](05-state-machine.md) | Repair state machine + speak gate | [05](../05-repair-state-machine.md) |
| [06](06-safety.md) | Output guardrail + MCP tools | [06](../06-safety-and-control.md) |
| [07](07-persona.md) | Voice persona (MiniMax) | [07](../07-voice-persona-minimax.md) |
| [08](08-infra.md) | Infrastructure / config | [08](../08-infrastructure-aws.md) |
| [09](09-frontend.md) | Frontend / UX | [09](../09-frontend-ux.md) |
| [10](10-onboarding.md) | Onboarding flow | [10](../10-onboarding.md) |
| [11](11-fact-moss.md) | FACT × Moss (write + read) | [11](../11-fact-moss-knowledge-trust.md) |
| [12](12-live-loop.md) | Live loop wiring | [12](../12-live-repair-loop.md) |
| [13](13-orchestrator.md) | Session orchestrator | [13](../13-session-orchestrator.md) |

## Tech conventions
- **Language/runtime:** Python 3.11+, `uv` per package. Async (`asyncio`) on the live path.
- **Built & reused:** `fact/` (FACT lib), `ingest/` (`unsiloed_client.py`, `chunker.py`,
  `ingest.py`, `fact_moss.py`). LLDs must reuse these, not re-spec them.
- **Voice runtime:** LiveKit Agents SDK (the `livekit-moss-vercel` scaffold is the starting
  point). Frontend: the scaffold's Next.js `agent-react`.
- **Models:** Claude (via TrueFoundry gateway) for reasoning; Qwen (multimodal) for vision;
  MiniMax for TTS. Moss for retrieval; FACT for trust.
- **Data shapes:** plain dataclasses in `agent/contracts.py`; JSON over the LiveKit data
  channel agent→UI; Moss metadata is **string-valued only**.
- **Errors:** fail safe — on any uncertainty the agent stays silent / REFUSEs, never invents.
- **Determinism for demo:** deterministic FSM for traversal; LLM only for phrasing.

## Proposed code layout (the `agent/` runtime the LLDs target)
```
agent/
  contracts.py        # shared dataclasses (RepairState, Chunk, Verdict, VisualObservation, …)
  orchestrator.py     # LLD 13 — route onboard vs live, lifecycle, re-route
  session.py          # SessionState
  perception/
    gate.py           # LLD 04 — KriNein pHash frame gate (no SSIM/LPIPS)
    vision.py         # LLD 04 — Qwen observe → VisualObservation
  state/
    machine.py        # LLD 05 — RepairState updates + speak gate
    referent.py       # LLD 05 — pronoun resolution
  knowledge/
    retrieve.py       # LLD 02 — Moss query (state→query, prefetch)
    verify.py         # LLD 11 read — wraps ingest/fact_moss.verify_document
  onboarding/
    onboard.py        # LLD 10 — identify → MCP → Unsiloed → graph → (→ ingest sign+index)
  safety/
    guardrail.py      # LLD 06 — TrueFoundry output guardrail
    mcp.py            # LLD 06/10 — find_manual via MCP gateway + tool discovery
  voice/
    livekit_agent.py  # LLD 03 — LiveKit loop (STT, turn, barge-in)
    persona.py        # LLD 07 — MiniMax TTS, urgency modes
ui/                   # LLD 09 — Next.js (from agent-react scaffold)
```

## Shared contracts (authoritative — restate by reference, do not redefine)
```python
# agent/contracts.py
@dataclass
class ProductIdentity: object_type:str; brand:str; model:str; model_source:str; confidence:float; raw_text:str
@dataclass
class ManualRef: model:str; title:str; uri:str; source:str; mime:str; confidence:float
@dataclass
class RepairNode: id:str; trigger_phrases:list[str]; preconditions:list[str]; instructions:list[str]; \
                  warnings:list[str]; parts:list[str]; hazards:list[dict]; conditions:list[str]; \
                  next:list[str]; source:dict
@dataclass
class Chunk: id:str; text:str; metadata:dict[str,str]; score:float|None=None; \
             type:str|None=None; part:str|None=None; risk_level:str|None=None; \
             verdict:str|None=None              # ACT|HEDGE|ESCALATE|REFUSE (attached after verify)
@dataclass
class VisualObservation: visible_object:str; candidate_parts:list[dict]; hazards:list[dict]; \
                         next_retrieval_queries:list[str]
@dataclass
class RepairState: session_id:str; object:dict; goal:str; current_node_id:str; phase:str; \
                   user_observations:list[dict]; visible_parts:list[dict]; hazards:list[dict]; \
                   actions_taken:list[str]; constraints:list[str]; rolling_summary:str=""
# RECALL MODEL: recall = RepairState (structured) + graph cursor (current_node_id) +
# recent-N user_observations + rolling_summary, all fed into compose(). Observations are
# SALIENT-ONLY (deduped state changes), not raw frames. Conversation is NOT indexed in Moss
# at runtime — see LLD 05 §Recall; the optional Moss `remember()` is a Tier-2 ephemeral
# per-session index (never the shared manual index).
@dataclass
class SessionState: session_id:str; lifecycle:str; identity:ProductIdentity; active_index:str; \
                    kb_issuer:str; degraded:list[str]; repair:RepairState
Verdict = Literal["ACT","HEDGE","ESCALATE","REFUSE"]   # agent-layer type; this is what Chunk.verdict holds
PersonaMode = Literal["normal","safety","diagnosis","confirm","expert"]
# NOTE: the BUILT ingest/fact_moss.py has its own `Verdict` *dataclass* (with `.decision: Decision` enum,
# `.reason`, `.result`) and a `Decision` enum. The agent layer never stores that object — `verify_chunk`
# (LLD 11) maps it to the string above via `.decision.value` and stamps it on `Chunk.verdict`. So
# everywhere in the agent runtime, a "verdict" is one of the 4 strings; the dataclass stays inside fact_moss.
Phase = Literal["diagnosing","guiding","confirming","warning","done"]
```

## LLD template (every LLD uses these sections)
```
# Fixalong LLD NN — <Part>
**Implements:** HLD NN · **Module(s):** agent/<path> · **Build tier:** N · **Status:** Draft v0.1

## 1. Responsibility (one paragraph — what this module does, bounded)
## 2. Files & public surface (file → exported funcs/classes)
## 3. Dependencies (libs/SDKs, internal modules, env vars)
## 4. Data structures (concrete types beyond the shared contracts)
## 5. Key functions (signature + algorithm/pseudocode for the hard parts)
## 6. Control flow / sequence (how a call moves through it)
## 7. Config & tuning (thresholds, params, defaults)
## 8. Error handling & fallbacks (fail-safe behavior)
## 9. Latency / perf notes (budget, hot vs cold)
## 10. Test plan (unit + integration; what to mock)
## 11. Build checklist (ordered steps to implement, Tier-0 first)
```
