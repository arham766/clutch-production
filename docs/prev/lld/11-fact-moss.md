# Fixalong LLD 11 — FACT × Moss (write + read)
**Implements:** HLD 11 · **Module(s):** `ingest/fact_moss.py`, `ingest/ingest.py` (built), `agent/knowledge/verify.py` (new) · **Build tier:** 0 (Moss) / 1 (FACT) · **Status:** Draft v0.1

## 1. Responsibility
The shared knowledge+trust layer. **WRITE (cold, once per model):** NFC-normalize each chunk,
sign it as a FACT envelope (`asserts="extracted_from"`, trust tier per risk, `fresh_until` TTL),
flatten the attestation into Moss string-only metadata, and index it under `fixalong-<model>` with
batched create-or-upsert. **READ (hot, every turn):** reconstruct the envelope from a retrieved
chunk's `text`+`metadata` and verify it offline against `{KB_ISSUER}`, yielding a `Verdict`
(ACT/HEDGE/ESCALATE/REFUSE). This module owns *signing* and *verifying* only. It does **not**
produce chunks (LLD 01/10), build the Moss query or speak the verdict (LLD 02/12), or run
TrueFoundry guardrails (LLD 06). The bulk of it is **already built** in `ingest/fact_moss.py` +
`ingest/ingest.py`; this LLD documents that code and specs the thin `verify.py` wrapper + the
`index_exists` registry helper.

## 2. Files & public surface
| File | Exported surface |
|---|---|
| `ingest/fact_moss.py` *(built)* | `load_kb_key`, `sign_chunk`, `sign_chunks`, `envelope_to_metadata`, `reconstruct_envelope`, `verify_document`, `verify_moss_doc`, `Decision`, `Verdict`; metadata-key consts `FACT_ENVELOPE`, `FACT_ISSUER`, `FACT_ACTIONABILITY`, `FACT_FRESH_UNTIL` |
| `ingest/ingest.py` *(built)* | `inject_into_moss(...)`, `_to_documents`, `_batches`, `parse_and_chunk`, CLI `main` (`--sign`, `--kb-key`, `--fresh-days`, `--recreate`) |
| `agent/knowledge/verify.py` *(new, read path)* | `verify_chunk(chunk: Chunk, *, trusted_issuers, clock=None) -> Chunk`, `verify_chunks(chunks, *, trusted_issuers, clock=None) -> list[Chunk]`, `KB_ISSUER` resolver |
| `agent/knowledge/registry.py` *(new, write/onboard)* | `index_exists(model_slug, *, client=None) -> bool`, `index_name(model_slug) -> str` |

## 3. Dependencies
- **Libs/SDKs:** `fact` (`sign_originator`, `verify`, `nfc_normalize`, `Trust`, `Actionability`, `SigningKey`, `generate_keypair`, `Envelope`, `VerifyResult`, `ProtocolError`, `ErrorCode`, `fact.timeutil.Clock`); `moss` (`MossClient`, `DocumentInfo`, `MutationOptions`); stdlib `base64/json/os/pathlib`.
- **Internal:** `chunker.chunk_result` (produces `{id,text,metadata}` chunks — LLD 01); `agent/contracts.py` (`Chunk`, `Verdict`); read path is consumed by `agent/knowledge/retrieve.py` (LLD 02).
- **Env vars:** `FACT_KB_PRIVATE_KEY` (base64 of 32 raw private bytes), `FACT_KB_KEY_PATH` (default `.kb_key.b64`), `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`, `MOSS_INDEX_NAME`, `MOSS_MODEL_ID` (default `moss-minilm`).

## 4. Data structures (beyond shared contracts)
The agent-runtime `Verdict` in `contracts.py` is the 4-state `Literal`. `fact_moss` defines the
*richer* return type the verifier actually produces (mapped to the literal at the boundary):
```python
class Decision(str, Enum): ACT="ACT"; HEDGE="HEDGE"; ESCALATE="ESCALATE"; REFUSE="REFUSE"
@dataclass
class Verdict:                              # fact_moss.Verdict (built)
    decision: Decision
    reason: str
    result: Optional[VerifyResult] = None
    @property report(self) -> Optional[VerifyReport]   # only when result.is_valid
    @property speakable(self) -> bool                  # decision == ACT
```
**Signed-chunk on the wire** = a Moss `DocumentInfo` whose `text` is the NFC claim and whose
`metadata` carries the four `FACT_*` keys (plus repair fields from LLD 01/10). Every metadata
value is a `str` — Moss requires string-only metadata.

### 4.1 What lands in Moss metadata (`envelope_to_metadata`)
| key | value | why denormalized |
|---|---|---|
| `fact_envelope` | full envelope JSON, **`claim.content` stripped** (`d["claim"].pop("content")`), compact `separators=(",",":")` | lossless; `content` == `doc.text`, stored once |
| `fact_issuer` | `env.chain[0].issuer` (KB `did:key`) | the live loop trusts exactly this; quick filter |
| `fact_actionability` | `env.trust.actionability.value` (`ACT`/`VERIFY`/`ESCALATE`) | Moss metadata filter + UI |
| `fact_fresh_until` | `env.trust.fresh_until or ""` (RFC3339) | freshness filter without parsing the envelope |

## 5. Key functions (signatures from the built code — do not re-invent)

```python
# --- KEYS -----------------------------------------------------------------
load_kb_key(path: Optional[str] = None, *, env_var: str = "FACT_KB_PRIVATE_KEY",
            create: bool = True) -> SigningKey
#   priority: env_var(b64) > path file > generate(+write to path). did:key = key.issuer.
```

```python
# --- WRITE: sign ----------------------------------------------------------
sign_chunk(chunk: dict[str,Any], signing_key: SigningKey, *,
           content_type: str = "fact://types/kb/chunk/v1",
           actionability: Actionability | str = Actionability.ACT,
           coverage: float = 0.95,
           fresh_seconds: int = 30*86_400,
           clock: Optional[Clock] = None) -> dict[str,Any]
sign_chunks(chunks: list[dict], signing_key: SigningKey, **kwargs) -> list[dict]
```
Algorithm of `sign_chunk` (built):
1. `text = nfc_normalize(chunk["text"])` — sign exactly the bytes the verifier will rehash.
2. `evidence = {k: meta[k] for k in ("source","page","section") if meta.get(k)}; evidence["method"]="unsiloed-parse"` — provenance fields.
3. `env = sign_originator(content=text, content_type=..., asserts="extracted_from", evidence=evidence, trust=Trust(actionability, coverage, fresh_until_in_seconds=fresh_seconds, clock=clock), signing_key=signing_key, clock=clock)`.
4. `chunk["text"]=text; chunk["metadata"]={**meta, **envelope_to_metadata(env)}` — original repair metadata preserved, attestation merged in.
- **`asserts="extracted_from"`** encodes provenance: *this claim was extracted from this manual*, not authored/asserted as opinion.
- **Trust-tier policy (write-time, §5.2 HLD):** routine step → `Actionability.ACT` + long `fresh_seconds`; safety/electrical/"must verify" → `VERIFY` (read→HEDGE) or `ESCALATE`; firmware/service-bulletin → short `fresh_seconds` (goes stale → HEDGE downstream). Tier is passed per call by LLD 10; the CLI default is ACT + `--fresh-days*86400`.

```python
# --- WRITE: index (ingest.py) --------------------------------------------
async def inject_into_moss(chunks, *, project_id, project_key, index_name,
                           model_id, recreate) -> None
```
Algorithm (built): `_to_documents` → `DocumentInfo(id,text,metadata)`; `existing = {ix.name for ix in await client.list_indexes()}`; if `recreate and exists` → `delete_index`; if not exists → first batch via `create_index(index_name, head, model_id)`, rest via `add_docs(..., MutationOptions(upsert=True))`; if exists → upsert all batches. `_batches(items, 500)` chunks at `BATCH_SIZE=500` (returns `[[]]` for empty input so `create_index` always gets a head). **Index name = `fixalong-<model_slug>`** is the cache key (CLI passes `--index`/`$MOSS_INDEX_NAME`; LLD 10 formats `index_name(model_slug)`).

```python
# --- READ: reconstruct + verify (fact_moss.py) ---------------------------
reconstruct_envelope(text: str, metadata: dict[str,str]) -> Envelope
verify_document(text: str, metadata: dict[str,str], *,
                trusted_issuers: Optional[set[str]] = None,
                clock: Optional[Clock] = None,
                seen_nonce_cache: Any = None,
                skew_tolerance: float = 300.0) -> Verdict
verify_moss_doc(doc, **kwargs) -> Verdict   # doc.text / doc.metadata convenience
```
`reconstruct_envelope` rehydrates `claim.content` from the returned `text`
(`d.setdefault("claim",{})["content"]=nfc_normalize(text)`), then `Envelope.from_dict`; raises
`ProtocolError(MALFORMED)` if `fact_envelope` missing. `verify_document` short-circuits REFUSE if
no envelope, REFUSE on malformed JSON, else `verify(env, trusted_issuers=..., clock=..., ...)` and
maps the `VerifyResult` through `_decide`.

`_decide` (built) — the verdict ladder:
```
not res.is_valid                       -> REFUSE  ("invalid: <ErrorCode>")     # tamper/unsigned
not report.issuer_trusted              -> ESCALATE ("issuer not in trusted set")
report.trust_hint == ESCALATE          -> ESCALATE
report.stale                           -> HEDGE   ("fact is stale")
report.trust_hint == VERIFY            -> HEDGE
else                                   -> ACT     ("verified, trusted, fresh")
```

```python
# --- READ wrapper (agent/knowledge/verify.py, NEW) -----------------------
KB_ISSUER: str                         # = load_kb_key(create=False).issuer  (or SessionState.kb_issuer)
def verify_chunk(chunk: Chunk, *, trusted_issuers={KB_ISSUER}, clock=None) -> Chunk:
    v = verify_document(chunk.text, chunk.metadata, trusted_issuers=trusted_issuers, clock=clock)
    chunk.verdict = v.decision.value           # attach ACT|HEDGE|ESCALATE|REFUSE onto the Chunk
    return chunk
def verify_chunks(chunks, *, trusted_issuers={KB_ISSUER}, clock=None) -> list[Chunk]:
    return [verify_chunk(c, trusted_issuers=trusted_issuers, clock=clock) for c in chunks]
```
`verify.py` is a *thin* adapter: it calls the built `verify_document`, stamps the 4-state literal
onto `Chunk.verdict`, and is the only place LLD 02 imports for trust. It performs **no** Moss I/O.

```python
# --- WRITE registry/cache (agent/knowledge/registry.py, NEW) -------------
def index_name(model_slug: str) -> str: return f"fixalong-{model_slug}"
async def index_exists(model_slug: str, *, client: MossClient | None = None) -> bool:
    client = client or moss_singleton()        # process Moss client (LLD 08); callers pass only model_slug
    return index_name(model_slug) in {ix.name for ix in await client.list_indexes()}
```
Mirrors the existence probe already inlined in `inject_into_moss` (`list_indexes()`), exposed so
LLD 10 can short-circuit onboarding on a cache hit. Optional fast path: a small KV keyed by
`model_slug` (HLD 11 §6, open question §14).

## 6. Control flow / sequence
**WRITE (cold, LLD 10 → here):** `chunk_result` → `sign_chunks(chunks, kb_key, tier, fresh)` →
each chunk gains `FACT_*` metadata → `inject_into_moss(signed, index_name=fixalong-<model>)` →
`list_indexes` check → `create_index(head)` then batched `add_docs(upsert=True)` → register in
cache. Runs once per model; amortized across every session for that model.

**READ (hot, LLD 02 retrieve → here, every turn):**
`retrieve(state)` builds the query and calls `Moss.query` → `Chunk[]` →
`verify_chunks(chunks, trusted_issuers={KB_ISSUER})` stamps `chunk.verdict` →
LLD 02/12 **drops** every `REFUSE` chunk, keeps ACT as ground truth, and **flags** HEDGE/ESCALATE
for the live loop to hedge/escalate (never spoken as plain fact). The agent literally cannot speak
a chunk it cannot prove came from the trusted manual.

## 7. Config & tuning
| Param | Default | Notes |
|---|---|---|
| `DEFAULT_CONTENT_TYPE` | `fact://types/kb/chunk/v1` | envelope content type |
| `fresh_seconds` | `30*86_400` (CLI `--fresh-days 30`) | per-tier; short for firmware/bulletins |
| `coverage` | `0.95` | trust coverage hint |
| `actionability` | `Actionability.ACT` | per-risk tier (see §5) |
| `BATCH_SIZE` | `500` | Moss create/upsert batch (`ingest.py`) |
| `MOSS_MODEL_ID` | `moss-minilm` | embedding model; index name embeds `<model>` |
| `skew_tolerance` | `300.0` s | verify clock skew; tests pass `0` for exact staleness |
| `trusted_issuers` | `{KB_ISSUER}` | the same-key invariant (§8) |

## 8. Security model & error handling (fail-safe)
**Two independent checks:** (1) **integrity** — `verify` recomputes `claim_hash` from the rehydrated
`text` and checks the Ed25519 signature; (2) **issuer policy** — `report.issuer_trusted` against
`trusted_issuers={KB_ISSUER}`. `did:key` puts the public key *in* the envelope (no key server), so
the issuer set is what makes a valid signature *mean* "from our manual" — anyone can mint a key.

| Situation | Verdict | Cause in code |
|---|---|---|
| chunk has no `fact_envelope` | REFUSE | early `if not metadata.get(FACT_ENVELOPE)` |
| envelope JSON malformed | REFUSE | `reconstruct_envelope` raises → caught |
| stored `text` edited after signing | REFUSE | `not res.is_valid` (`HASH_MISMATCH`) |
| signed by attacker's key | ESCALATE | `not report.issuer_trusted` (integrity ok) |
| legit but past `fresh_until` | HEDGE | `report.stale` |
| tier VERIFY | HEDGE | `trust_hint == VERIFY` |
| legit, trusted, fresh | ACT | all checks pass |

**Same-key invariant:** the key that *signs* the index (`load_kb_key`) must equal the key the live
loop *trusts* (`trusted_issuers={KB_ISSUER}`, where `KB_ISSUER == kb_key.issuer`). Persist the key
(`FACT_KB_PRIVATE_KEY` env or `.kb_key.b64`); regenerating it makes **every** chunk read ESCALATE
(valid signature, untrusted issuer). `SessionState.kb_issuer` carries the trusted issuer per session.
**Fallbacks (fail-safe):** Moss unreachable → local cosine over the same embeddings, same `retrieve`
interface; empty/low-confidence retrieval → widen filter / lower `alpha`, never invent; FACT not
wired at write → unsigned index → read returns REFUSE (trust gate lost loudly, not silently).
`verify_document` never raises on bad input — it returns REFUSE.

## 9. Latency / perf
| Op | Side | Budget |
|---|---|---|
| `sign_chunk` (Ed25519) | write | sub-ms; whole-manual sign is **offline** |
| `create_index` / upsert | write | seconds, once per model (offline) |
| `load_index` | read | ~3–5 ms, once at session start |
| `Moss.query` (in-process) | read | **<10 ms** |
| `verify_document` per chunk | read | **<1 ms**, offline, no network |

Verify is pure CPU (one hash + one Ed25519 verify + dict lookups) — cheap enough to run on every
retrieved chunk every turn. Signing is amortized to zero on the hot path.

## 10. Test plan (extends `ingest/tests/test_fact_moss.py`)
The built suite already covers: sign adds `FACT_*` metadata + all-string values; envelope omits
`content` but keeps `claim_hash`; ACT roundtrip; tampered text → REFUSE (`HASH_MISMATCH`); unsigned
→ REFUSE; foreign issuer → ESCALATE (`result.is_valid`); stale → HEDGE; VERIFY tier → HEDGE;
ESCALATE tier; `verify_moss_doc` wrapper; `load_kb_key` create/reload + env; sign→chunker pipeline.
**Add (same file, reuse `kb_key`/`clock`/`_chunk` fixtures):**
- `test_envelope_to_metadata_keys` — exact `{FACT_ENVELOPE,FACT_ISSUER,FACT_ACTIONABILITY,FACT_FRESH_UNTIL}` set; `fact_actionability=="ACT"`; `fact_fresh_until` is RFC3339; compact JSON has no spaces.
- `test_fresh_until_reflects_tier_ttl` — sign with `fresh_seconds=3600` → `FACT_FRESH_UNTIL` == NOW+1h under FixedClock.
- `test_malformed_envelope_refused` — set `metadata[FACT_ENVELOPE]="{not json"` → REFUSE, reason contains "malformed".
- `test_nfc_normalization_roundtrip` — text with a decomposed accent (NFD) signs and verifies ACT; a differently-composed copy still ACTs (both normalize equal); a real edit REFUSEs.
- `test_reconstruct_envelope_missing_raises` — `reconstruct_envelope(text, {})` raises `ProtocolError(MALFORMED)`.
- `test_default_fresh_seconds` — unsigned `fresh_seconds` → 30-day default in metadata.
**New `agent/knowledge/tests/test_verify.py`** (read wrapper; mock = a `Chunk`, no Moss):
- `test_verify_chunk_stamps_verdict` — signed chunk → `verify_chunk` sets `chunk.verdict=="ACT"`.
- `test_verify_chunks_drops_nothing_but_flags` — mix of ACT/HEDGE/REFUSE chunks → verdicts stamped; assert caller-side drop of REFUSE leaves ACT+HEDGE (composition with LLD 02).
- `test_same_key_invariant` — verify with a *different* `trusted_issuers` → ESCALATE (regenerated-key scenario).
**New `agent/knowledge/tests/test_registry.py`** (mock `MossClient.list_indexes`):
- `test_index_name` — `index_name("laserjet-pro-m404")=="fixalong-laserjet-pro-m404"`.
- `test_index_exists_hit_miss` — stub `list_indexes` returning a fake `.name` set; hit/miss both asserted.
Run: `uv run pytest ingest/tests/test_fact_moss.py agent/knowledge/tests`.

## 11. Build checklist
0. **(built)** `fact_moss.py` sign/verify + `ingest.py inject_into_moss` + CLI `--sign`. Verify suite green.
1. **(built)** Confirm `load_kb_key` persistence (env > file > generate) and `did:key` stability across reloads.
2. Add the §10 sign-side tests to `ingest/tests/test_fact_moss.py`.
3. Write `agent/knowledge/verify.py` (`verify_chunk`/`verify_chunks`, `KB_ISSUER` from `SessionState.kb_issuer` / `load_kb_key(create=False).issuer`) — pure wrapper, no Moss I/O. Add `test_verify.py`.
4. Write `agent/knowledge/registry.py` (`index_name`, `index_exists`) for LLD 10's onboarding short-circuit. Add `test_registry.py`.
5. Wire into LLD 02 `retrieve`: `Moss.query` → `verify_chunks` → drop REFUSE, flag HEDGE/ESCALATE, pass ACT through.
6. Pin & persist the KB key in deploy config (`FACT_KB_PRIVATE_KEY`); document the same-key invariant for ops.
