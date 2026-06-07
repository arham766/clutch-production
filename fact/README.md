# FACT — Facts, Attested, Chained, Transferable

Reference implementation of the **FACT v0.2** protocol library (HLD §all, LLD Part A).

A *fact* travels as a signed **envelope** that any receiver can verify **offline** —
issuer authenticity, content integrity, chain-of-custody, and freshness — using only
the bytes in front of them plus a public key they already have. No callback to the
issuer, no central authority.

```python
from fact import generate_keypair, sign_originator, verify, Trust

key = generate_keypair()
env = sign_originator(
    content={"violation": "none", "parcel": "123"},
    content_type="fact://types/code_enforcement/violation/v1",
    trust=Trust(actionability="ACT", coverage=0.98, fresh_until_in_seconds=3600),
    signing_key=key,
)

result = verify(env)            # offline; did:key resolver by default
assert result.is_valid
print(result.report.originator, result.report.trust_hint, result.report.stale)
```

## Scope (v0.2 core)
- Crypto suite **`ed25519-sha256-jcs`** (mandatory suite).
- **`did:key`** identity resolution — fully offline. `did:web` via a pluggable resolver.
- Roles: **ORIGINATOR / RELAY / PROCESSOR**; multi-hop chains up to 16.
- Full verify pipeline: size limits → version/suite → nonce shape → replay → content
  integrity → chain walk (signatures, key state, structural rules) → temporal validity.
- Closed error taxonomy, replay cache, clock-skew handling, frozen-bytes serialization.

## Modules (LLD Part A)
| Module | Role |
|---|---|
| `types` | Envelope, Claim, Attestation, Trust, results, options |
| `errors` | `ProtocolError` + closed `ErrorCode` set |
| `timeutil` | RFC3339 parse/format (timezone-mandatory), skew compare |
| `canonical` | JCS (RFC 8785) canonicalization + NFC helper |
| `crypto` | Ed25519 + SHA-256 suite registry, b64url, nonce |
| `identity` | `did:key` / pluggable resolvers, key rotation state |
| `attestation` | sign / verify one link |
| `chain` | build & walk the chain |
| `envelope` | (de)serialization + structural validation |
| `replay` | in-memory replay cache |
| `api` | `sign_originator` / `sign_relay` / `sign_processor` / `verify` |

## Develop
```bash
uv sync
uv run pytest
```
