# Fixalong HLD 06 — Safety & Control (output guardrails + TrueFoundry)

**Status:** Draft v0.1 · **Build tier:** 1 · **Sponsor:** TrueFoundry
**Owns:** the **output** guardrail (is the generated instruction safe?) + model routing / MCP.
**Note:** the **input** trust gate (FACT signing + offline verify) is owned by
[HLD 11 (FACT × Moss)](11-fact-moss-knowledge-trust.md), not here. This doc's §2 describes that
gate for context, but HLD 11 is authoritative.
**Depends on:** retrieve + verify (HLD 11), State Machine (05) · **Consumed by:** Voice (03/07)

---

## 1. Purpose

Fixalong gives **physical-world instructions** that can hurt someone (hot fuser, live wiring,
moving parts). Safety is a two-stage system:

> **FACT verifies the INPUT** — the retrieved manual fact is genuinely from the real manual,
> unaltered, and fresh.
> **TrueFoundry guardrails check the OUTPUT** — the generated spoken instruction is safe.

These layer; they don't compete. Together: *the agent can't act on a fabricated/tampered
fact, and can't speak an unsafe instruction even from a real one.*

## 2. Stage 1 — FACT input verification (provenance & freshness) — *owned by HLD 11*

> This stage is **owned by [HLD 11 (FACT × Moss)](11-fact-moss-knowledge-trust.md)** and is
> summarized here only to show how it feeds the output guardrail. See HLD 11 for the
> authoritative mechanics (signing, the verdict, the security model).

Each retrieved chunk carries a FACT attestation (signed in HLD 11's write side). Before a chunk
reaches the composer, the agent verifies it **offline (<1 ms)** and gets a verdict:

```python
from fact_moss import verify_document, Decision   # built
v = verify_document(chunk.text, chunk.metadata, trusted_issuers={KB_ISSUER})
```

| Verdict | Meaning | State-machine effect |
|---|---|---|
| **ACT** | signed by the KB key, fresh, tier ACT | usable as ground truth |
| **HEDGE** | stale, or tier VERIFY | usable but agent says "let me confirm…" |
| **ESCALATE** | valid signature but untrusted issuer / tier ESCALATE | don't speak; flag |
| **REFUSE** | unsigned / tampered (hash or signature fails) | drop the chunk entirely |

Why it matters for repair: **the agent literally cannot give a step that isn't in the
verified manual.** If someone poisons the knowledge base or a chunk is altered, it's REFUSED
— a normal RAG bot would happily speak it. Every spoken warning also carries provenance
("manual §3.2"), shown as a trust chip (09).

> Security model (did:key offline + `trusted_issuers`) is detailed in
> `ingest/FACT_INTEGRATION.md`. For the demo: local key, `did:key`, single trusted issuer.

## 3. Stage 2 — TrueFoundry output guardrails (instruction safety)

Before any drafted instruction is spoken, run it through a guardrail keyed on the current
hazards and state:

```jsonc
// input
{ "proposed_response": "Pull the wire out with pliers.",
  "machine_type": "printer", "current_state": "rear panel open",
  "risk_flags": ["electrical","heat","moving_parts"],
  "retrieved_warnings": ["fuser may be hot","unplug before servicing"] }
// output
{ "allowed": false,
  "reason": "electrical hazard: must unplug first",
  "safe_rewrite": "Unplug the printer first, wait two minutes, then remove the wire." }
```

Rules enforce, e.g.: if `risk_flags` includes electrical and the response touches wiring,
require an "unplug first" precondition; if near the fuser, require the hot-surface warning;
block tool use on energized parts. The State Machine speaks the `safe_rewrite`, never the
blocked draft.

## 4. TrueFoundry as the control plane (gateway + MCP + routing)

Beyond guardrails, TrueFoundry is the governed control plane:

- **AI Gateway:** all model calls go through one proxy → observability, policy, rate limits,
  failover. Claude is the primary; fallbacks (Bedrock, others) configured here.
- **MCP Gateway + tool discovery:** the agent's tools (`find_manual`, `parse_manual`,
  `search_moss`, `classify_part`, `get_warning`, `escalate_to_human`) are registered behind
  one MCP Gateway. The agent does **not** query MCP servers directly or carry a hardcoded
  tool list — it **discovers** the authoritative tool set **dynamically at runtime** by asking
  the gateway, which queries its registered MCP servers and returns only the tools allowed for
  the current identity/environment/policy context. Discovery is a privileged, policy-enforced,
  auditable operation (OAuth 2.0 / federated identity), not a passive metadata lookup.
  - *Why it matters for Fixalong:* a repair agent giving physical-world instructions must not
    be able to reach arbitrary tools. Gateway-mediated discovery means we can scope tools per
    context — e.g., `escalate_to_human` only enabled for enterprise/high-risk sessions, or a
    customer-specific manual server surfaced only for that customer's technicians — and every
    tool the agent can see is centrally governed and logged.
- **Model routing:** task-appropriate models behind one gateway.

| Task | Model class |
|---|---|
| fast conversational answer | low-latency voice/chat model |
| visual part recognition | multimodal (Qwen, 04) |
| safety check / guardrail | stronger reasoning model |
| fallback answer | backup provider (Bedrock) |
| repair receipt | cheap text model |

## 5. Sequence (where safety sits in the loop)

```
retrieve (02) ─▶ FACT verify each chunk (Stage 1) ─▶ drop REFUSE / flag ESCALATE
              ─▶ compose draft (05, ground-truth only)
              ─▶ TrueFoundry guardrail (Stage 2) ─▶ allow | safe_rewrite | block→escalate
              ─▶ speak (07)
```

## 6. Interfaces
```python
verify_document(text, metadata, trusted_issuers) -> Verdict        # FACT (built)
guardrail(draft, state, retrieved) -> SafetyDecision               # TrueFoundry (+local rules)
route(task) -> model_handle                                        # TrueFoundry gateway (model routing)
discover_tools(context) -> list[ToolSpec]                          # TrueFoundry MCP gateway — dynamic, policy-scoped
call_tool(name, args) -> result                                    # TrueFoundry MCP gateway (auth + audit)
```

## 7. Failure modes & fallbacks
| Failure | Fallback |
|---|---|
| TrueFoundry gateway down | call Claude directly + run the **local** safety wrapper (same `guardrail` interface) |
| FACT not wired | agent runs without provenance gate; rely on guardrail only (degraded trust story) |
| Guardrail false-block | log + allow with the required warning prepended (fail toward caution, not silence) |
| Untrusted/empty retrieval | agent says it can't verify and offers to escalate — never fabricates |

## 8. The judge-facing framing
> "Repair advice can hurt you. So every fact the agent speaks is cryptographically verified
> to come from the real manual (FACT), and every instruction is safety-guardrailed before
> it's spoken (TrueFoundry). It physically can't make up a step, and it won't tell you to
> grab a live wire."

## 9. Open questions
- Guardrail as TrueFoundry-native config vs our function calling through the gateway (start
  local function, migrate to native guardrails if time).
- Whether ESCALATE routes to a real human handoff in the demo or just a visible flag.
