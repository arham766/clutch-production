"""FACT ⟷ Moss bridge.

Signs each Moss chunk as a FACT envelope at ingestion, and verifies it offline at
retrieval — so the repair agent only speaks facts it can cryptographically prove,
that haven't gone stale, with a trust tier telling it whether to ACT / HEDGE /
ESCALATE / REFUSE.

Core idea — the chunk text *is* the claim, so nothing is duplicated:

    claim.content  ==  doc.text          (NFC-normalized chunk text)
    claim_hash     ==  sha256(canonicalize(text))

The attestation (signature, hashes, chain, trust) is flattened into Moss metadata
(string-only, which Moss requires). At query time we reconstruct the envelope from
``text`` + ``metadata`` and verify it.

This module imports only ``fact`` (not ``moss``), so it is testable on its own.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from fact import (
    Actionability,
    Envelope,
    ErrorCode,
    ProtocolError,
    SigningKey,
    Trust,
    VerifyReport,
    VerifyResult,
    generate_keypair,
    nfc_normalize,
    sign_originator,
    verify,
)
from fact.timeutil import Clock

# --- Moss metadata keys written by the issuer ------------------------------- #
FACT_ENVELOPE = "fact_envelope"          # full envelope JSON, content stripped (lossless)
FACT_ISSUER = "fact_issuer"              # denormalized: the originator did:key
FACT_ACTIONABILITY = "fact_actionability"  # denormalized: ACT | VERIFY | ESCALATE
FACT_FRESH_UNTIL = "fact_fresh_until"    # denormalized: RFC3339 or ""

DEFAULT_CONTENT_TYPE = "fact://types/kb/chunk/v1"
DEFAULT_FRESH_SECONDS = 30 * 86_400      # 30 days


class Decision(str, Enum):
    ACT = "ACT"            # verified, trusted, fresh -> speak it plainly
    HEDGE = "HEDGE"        # verified but stale or VERIFY tier -> confirm before stating
    ESCALATE = "ESCALATE"  # verified but untrusted issuer / ESCALATE tier -> human
    REFUSE = "REFUSE"      # cannot verify integrity (unsigned/tampered) -> do not speak


@dataclass
class Verdict:
    decision: Decision
    reason: str
    result: Optional[VerifyResult] = None

    @property
    def report(self) -> Optional[VerifyReport]:
        return self.result.report if self.result and self.result.is_valid else None

    @property
    def speakable(self) -> bool:
        return self.decision == Decision.ACT


# --------------------------------------------------------------------------- #
# Key management — the knowledge base's signing identity
# --------------------------------------------------------------------------- #
def load_kb_key(
    path: Optional[str] = None,
    *,
    env_var: str = "FACT_KB_PRIVATE_KEY",
    create: bool = True,
) -> SigningKey:
    """Load the KB signing key from base64 (env var or file); optionally create one.

    Priority: ``env_var`` (base64 of 32 raw private bytes) > ``path`` file > generate.
    A freshly generated key is written to ``path`` (if given) so reruns are stable.
    """
    raw_b64 = os.getenv(env_var)
    if raw_b64:
        return SigningKey.from_private_bytes(base64.b64decode(raw_b64))

    if path:
        p = Path(path)
        if p.exists():
            return SigningKey.from_private_bytes(base64.b64decode(p.read_text().strip()))
        if create:
            key = generate_keypair()
            p.write_text(base64.b64encode(key.private_bytes_raw()).decode("ascii"))
            return key

    if create:
        return generate_keypair()
    raise FileNotFoundError(f"No KB key found (env {env_var} unset, path {path!r} missing)")


# --------------------------------------------------------------------------- #
# Sign — at ingestion
# --------------------------------------------------------------------------- #
def envelope_to_metadata(env: Envelope) -> dict[str, str]:
    """Flatten an envelope into Moss metadata (all values are strings).

    ``claim.content`` is omitted because it equals ``doc.text`` — stored once, in
    Moss's text field. The rest (chain, signatures, hashes, trust) is preserved
    losslessly in ``fact_envelope`` as JSON, with a few denormalized fields for
    Moss metadata filtering and quick UI display.
    """
    d = env.to_dict()
    d["claim"].pop("content", None)
    return {
        FACT_ENVELOPE: json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        FACT_ISSUER: env.chain[0].issuer,
        FACT_ACTIONABILITY: env.trust.actionability.value,
        FACT_FRESH_UNTIL: env.trust.fresh_until or "",
    }


def sign_chunk(
    chunk: dict[str, Any],
    signing_key: SigningKey,
    *,
    content_type: str = DEFAULT_CONTENT_TYPE,
    actionability: Actionability | str = Actionability.ACT,
    coverage: float = 0.95,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
    clock: Optional[Clock] = None,
) -> dict[str, Any]:
    """Issue a FACT envelope for one ingestion chunk and fold it into the chunk.

    ``chunk`` is ``{"id", "text", "metadata"}`` from the chunker. The chunk's text
    is NFC-normalized in place (so the signed bytes match what the verifier sees),
    and the attestation is merged into ``chunk["metadata"]``.
    """
    text = nfc_normalize(chunk["text"])
    meta = chunk.get("metadata") or {}

    evidence = {
        k: meta[k] for k in ("source", "page", "section") if meta.get(k)
    }
    evidence["method"] = "unsiloed-parse"

    env = sign_originator(
        content=text,
        content_type=content_type,
        asserts="extracted_from",
        evidence=evidence,
        trust=Trust(actionability, coverage, fresh_until_in_seconds=fresh_seconds, clock=clock),
        signing_key=signing_key,
        clock=clock,
    )

    chunk["text"] = text
    chunk["metadata"] = {**meta, **envelope_to_metadata(env)}
    return chunk


def sign_chunks(
    chunks: list[dict[str, Any]],
    signing_key: SigningKey,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    return [sign_chunk(c, signing_key, **kwargs) for c in chunks]


# --------------------------------------------------------------------------- #
# Verify — at retrieval
# --------------------------------------------------------------------------- #
def reconstruct_envelope(text: str, metadata: dict[str, str]) -> Envelope:
    """Rebuild the envelope from a retrieved chunk's text + metadata.

    Injects the content back from ``text`` (the chunk that Moss returned).
    Raises ``ProtocolError(MALFORMED)`` if the envelope JSON is missing/invalid.
    """
    raw = metadata.get(FACT_ENVELOPE)
    if not raw:
        raise ProtocolError(ErrorCode.MALFORMED, "no fact_envelope in metadata")
    d = json.loads(raw)
    d.setdefault("claim", {})["content"] = nfc_normalize(text)
    return Envelope.from_dict(d)


def verify_document(
    text: str,
    metadata: dict[str, str],
    *,
    trusted_issuers: Optional[set[str]] = None,
    clock: Optional[Clock] = None,
    seen_nonce_cache: Any = None,
    skew_tolerance: float = 300.0,
) -> Verdict:
    """Verify a retrieved chunk offline and return an action Verdict.

    Pass ``trusted_issuers={kb_key.issuer}`` so only facts signed by *your*
    ingestion key are trusted — a valid signature from an unknown key escalates
    rather than acts.
    """
    if not metadata.get(FACT_ENVELOPE):
        return Verdict(Decision.REFUSE, "unsigned: no fact_envelope in metadata")

    try:
        env = reconstruct_envelope(text, metadata)
    except (ProtocolError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        return Verdict(Decision.REFUSE, f"malformed envelope: {e}")

    res = verify(
        env,
        trusted_issuers=trusted_issuers,
        clock=clock,
        seen_nonce_cache=seen_nonce_cache,
        skew_tolerance=skew_tolerance,
    )
    return _decide(res)


def _decide(res: VerifyResult) -> Verdict:
    if not res.is_valid:
        return Verdict(Decision.REFUSE, f"invalid: {res.error.code.value}", res)
    r = res.report
    assert r is not None
    if not r.issuer_trusted:
        return Verdict(Decision.ESCALATE, "issuer not in trusted set", res)
    if r.trust_hint == Actionability.ESCALATE:
        return Verdict(Decision.ESCALATE, "trust tier ESCALATE", res)
    if r.stale:
        return Verdict(Decision.HEDGE, "fact is stale (past fresh_until)", res)
    if r.trust_hint == Actionability.VERIFY:
        return Verdict(Decision.HEDGE, "trust tier VERIFY", res)
    return Verdict(Decision.ACT, "verified, trusted, fresh", res)


def verify_moss_doc(doc: Any, **kwargs: Any) -> Verdict:
    """Convenience wrapper for a Moss result doc exposing ``.text`` and ``.metadata``."""
    return verify_document(doc.text, dict(doc.metadata or {}), **kwargs)
