# FACT × Moss — Verifiable Retrieval (HLD 11 write side)

> The full architecture is **[HLD 11 — FACT × Moss: the Knowledge & Trust Layer](../docs/fixalong/11-fact-moss-knowledge-trust.md)**.
> This file documents the implemented **write side** (sign at ingest) + verify helper.

How the [FACT protocol](../fact/README.md) is wired into the Unsiloed → Moss ingestion
pipeline so the Fixalong repair agent only speaks facts it can **cryptographically prove**,
that **haven't gone stale**, with a **trust tier** telling it whether to *act, hedge,
escalate,* or *refuse*.

> **Two layers, one pipeline.**
> **Moss** answers *"what's the relevant fact?"* (retrieval, <10 ms).
> **FACT** answers *"can I trust this fact enough to act on it?"* (provenance, <1 ms, offline).

---

## 1. The big picture

```
 INGESTION  (issuer = the knowledge base's key)            RETRIEVAL  (verifier = the agent)
 ┌───────────────────────────────────────────┐            ┌──────────────────────────────────────┐
 │ Unsiloed parse → chunker → chunk{id,text,  │            │ user speaks → Moss query → top-k docs │
 │                              metadata}      │  Moss      │        │                              │
 │   │ sign_chunk(chunk, KB_KEY)               │  index     │        ▼                              │
 │   ▼  FACT envelope per chunk                │ ─────────▶ │ verify_document(text, metadata,       │
 │   • content   = chunk text (NOT duplicated) │            │     trusted_issuers={KB_ISSUER})      │
 │   • claim_hash= sha256(canonicalize(text))  │            │        │                              │
 │   • attestation flattened → Moss metadata   │            │        ▼  Verdict                     │
 │ DocumentInfo(id, text, metadata)            │            │  ACT / HEDGE / ESCALATE / REFUSE      │
 └───────────────────────────────────────────┘            └──────────────────────────────────────┘
```

- **Sign once, at ingestion.** You hold a single signing key — the *identity of the
  knowledge base*. Every chunk becomes a signed FACT envelope.
- **Verify on every retrieval, offline.** The agent reconstructs each retrieved
  chunk's envelope and verifies it with no network call, in well under a millisecond.

---

## 2. The core trick: the chunk text *is* the claim

A Moss document already carries `text`. We reuse it as the fact's content, so the
fact is **never stored twice**:

```
claim.content  ==  doc.text                     # the NFC-normalized chunk text
claim_hash     ==  sha256(canonicalize(text))   # integrity binds to that text
```

Only the **attestation** (signature, hashes, chain, trust block) is written to Moss
**metadata** — which is string-only, exactly what this needs. At query time we read
`text` + `metadata` back and rebuild the envelope.

NFC normalization is applied to the text **before signing** and again **before
verifying**, so "same data, different hash" Unicode bugs can't occur.

---

## 3. What gets stored in Moss metadata

`envelope_to_metadata()` flattens the envelope into these string keys (added
alongside the chunker's existing `source` / `section` / `page` / … metadata):

| Metadata key | Example | Purpose |
|---|---|---|
| `fact_envelope` | `{"v":"fact/0.2","alg":"ed25519-sha256-jcs","nonce":…,"claim":{"content_type":…,"claim_hash":…},"chain":[…],"trust":{…}}` | **Lossless** envelope JSON (content stripped). Used to verify. Handles multi-hop chains. |
| `fact_issuer` | `did:key:z6Mksb…` | Denormalized originator — for Moss metadata filtering / UI display. |
| `fact_actionability` | `ACT` | Denormalized trust tier — lets you filter low-trust facts at query time. |
| `fact_fresh_until` | `2026-07-04T12:00:00Z` | Denormalized freshness — for quick display. |

> The denormalized fields enable a neat trick: filter retrieval to only high-trust
> facts directly in Moss — `query(..., filter={"fact_actionability": {"$eq": "ACT"}})` —
> so untrusted facts never even reach the agent.

`fact_envelope` omits `claim.content` precisely because it equals `doc.text`; the
verifier injects it back on the way in.

---

## 4. Signing (ingestion)

`sign_chunk()` issues one FACT envelope per chunk. The role is **ORIGINATOR** with
`asserts="extracted_from"` and `evidence` carrying the source — i.e. *"this fact was
extracted from `acme.pdf`, page 2, by Unsiloed."*

```python
from fact_moss import load_kb_key, sign_chunk

kb_key = load_kb_key(".kb_key.b64")          # the KB's identity (created if absent)

signed = sign_chunk(
    {"id": "acme-p2-0001",
     "text": "Acme is SOC 2 Type II certified and GDPR compliant.",
     "metadata": {"source": "acme.pdf", "section": "Security", "page": "2"}},
    kb_key,
    actionability="ACT",         # or "VERIFY" / "ESCALATE" per fact sensitivity
    coverage=0.95,
    fresh_seconds=30 * 86400,    # how long this fact stays "fresh"
)
# signed["metadata"] now contains fact_envelope, fact_issuer, fact_actionability, fact_fresh_until
```

Run it through the whole pipeline with the CLI flag:

```bash
uv run python ingest.py docs/ --sign --fresh-days 30
# prints the KB issuer did:key — give that to the agent as its trusted issuer
```

---

## 5. Verifying (retrieval)

`verify_document()` reconstructs the envelope from a retrieved chunk and returns a
`Verdict`. Drop this into the LiveKit agent's retrieval hook, before Claude is given
the fact as ground truth:

```python
from fact_moss import verify_document, Decision

KB_ISSUER = "did:key:z6Mksb…"   # printed by the --sign run; the only issuer we trust

for doc in moss_results.docs:
    v = verify_document(doc.text, dict(doc.metadata),
                        trusted_issuers={KB_ISSUER})
    if v.decision == Decision.ACT:
        ground_truth.append(doc.text)              # safe to state
    elif v.decision == Decision.HEDGE:
        ground_truth.append(f"(confirm) {doc.text}")  # "let me double-check…"
    # ESCALATE / REFUSE: don't feed to the model; show a chip / route to a human
```

### The decision table

| Verdict | When | Agent behavior | UI chip |
|---|---|---|---|
| **ACT** | valid signature, trusted issuer, fresh, tier `ACT` | state it plainly | ✅ verified |
| **HEDGE** | valid + trusted but **stale**, or tier `VERIFY` | confirm before stating | ⚠️ verify |
| **ESCALATE** | valid signature but **issuer not trusted**, or tier `ESCALATE` | hand to a human | 🛑 escalate |
| **REFUSE** | signature/hash **fails** or chunk **unsigned** | never speak it | 🚫 unverified |

---

## 6. Security model — why `verify` is meaningful

With `did:key`, the public key is embedded in the envelope, so verification needs
**no key server** — but it also means *anyone* can mint a key and sign. So
verification is two independent checks:

1. **Integrity** — recompute `claim_hash` from `doc.text` and check the Ed25519
   signature. Catches any tampering of the text or the attestation.
2. **Issuer policy** — `trusted_issuers={KB_ISSUER}`. Only facts signed by *your*
   ingestion key are trusted; a valid signature from an unknown key escalates.

This is what makes the demo beats real (all verified live):

| Attack / situation | Result | Why |
|---|---|---|
| Doc added to Moss **without** a `fact_envelope` | **REFUSE** | unsigned — no attestation to verify |
| Stored fact's **text edited** after signing | **REFUSE** | `HASH_MISMATCH` — recomputed hash ≠ signed hash |
| Fact signed with an **attacker's own key** | **ESCALATE** | integrity ok, but issuer ∉ trusted set |
| Legit fact **past its `fresh_until`** | **HEDGE** | `stale` flag — don't state outdated info |
| Legit, trusted, fresh fact | **ACT** | all checks pass |

A normal RAG bot speaks all five. This one doesn't.

Observed output from `fact_moss` (deterministic, fixed clock):

```
signed/trusted   -> ACT       (verified, trusted, fresh)
tampered text    -> REFUSE    (invalid: HASH_MISMATCH)
unsigned         -> REFUSE    (unsigned: no fact_envelope in metadata)
foreign issuer   -> ESCALATE  (issuer not in trusted set)
stale            -> HEDGE     (fact is stale (past fresh_until))
VERIFY tier      -> HEDGE     (trust tier VERIFY)
```

---

## 7. Key management

The KB signing key **is** the knowledge base's cryptographic identity. Treat the
private key like a secret.

- **Load order** (`load_kb_key`): env var `FACT_KB_PRIVATE_KEY` (base64 of 32 raw
  bytes) → key file path → generate a fresh one (and write it to the path).
- **The public identity** is the `did:key` printed on `--sign`. The agent trusts
  exactly this string. No certificate, no registry — the key *is* the identity.
- **Rotation** (future): FACT supports `valid_from` / `valid_until` / `revoked_at`
  via a resolver; for the hackathon a single static `did:key` is enough.

```bash
# Persist a key once and reuse it for both ingestion and the agent:
export FACT_KB_PRIVATE_KEY=$(uv run python -c "import fact,base64;print(base64.b64encode(fact.generate_keypair().private_bytes_raw()).decode())")
```

---

## 8. Performance

- Signing is at ingestion time (off the hot path).
- **Verification is offline and sub-millisecond** — it adds essentially nothing to
  Moss's <10 ms retrieval. The pitch holds: *instant **and** verifiable, both with
  zero network.*

---

## 9. Files & tests

| File | Role |
|---|---|
| `fact_moss.py` | The bridge: `sign_chunk` / `sign_chunks`, `envelope_to_metadata`, `reconstruct_envelope`, `verify_document` / `verify_moss_doc`, `load_kb_key`, `Decision` / `Verdict` |
| `ingest.py` | `--sign` flag wires `sign_chunks` into the pipeline before Moss upload |
| `tests/test_fact_moss.py` | sign↔verify round-trip, tamper→REFUSE, unsigned→REFUSE, foreign issuer→ESCALATE, stale→HEDGE, tier mapping, key mgmt, full chunker pipeline |

```bash
cd ingest
uv sync
uv run pytest -q           # bridge + pipeline tests (no API keys needed)
```

## 10. Scope / limitations (hackathon build)
- Single-hop **ORIGINATOR** facts (one issuer = the KB). Multi-hop relay/processor
  chains are supported by the `fact` library and round-trip through `fact_envelope`,
  but ingestion currently issues one originator attestation per chunk.
- `did:key` only (fully offline). `did:web` + rotation/revocation are in the `fact`
  library's resolver design but not wired into ingestion.
- Trust tier / coverage are set per ingestion run; deriving them from Unsiloed
  extraction confidence (LLD §B4.3) is a natural next step.
