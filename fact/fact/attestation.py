"""Sign and verify a single attestation link (LLD §A9.1)."""

from __future__ import annotations

from typing import Optional

from .canonical import canonicalize
from .crypto import CryptoSuite, b64url_decode, b64url_encode
from .errors import ErrorCode, ProtocolError
from .identity import SigningKey
from .types import Attestation, Role


def make_signed_attestation(
    *,
    role: Role,
    asserts: str,
    issued_at: str,
    claim_hash: str,
    nonce: str,
    signing_key: SigningKey,
    suite: CryptoSuite,
    prev: Optional[str] = None,
    derives_from: Optional[str] = None,
    evidence: Optional[dict] = None,
    ext: Optional[dict] = None,
) -> Attestation:
    att = Attestation(
        issuer=signing_key.issuer,
        key_id=signing_key.key_id,
        role=role,
        asserts=asserts,
        issued_at=issued_at,
        claim_hash=claim_hash,
        nonce=nonce,
        prev=prev,
        derives_from=derives_from,
        evidence=evidence or {},
        ext=ext or {},
        signature="",
    )
    body_bytes = canonicalize(att.signing_body())
    sig = signing_key.sign(body_bytes)
    att.signature = b64url_encode(sig)
    return att


def verify_attestation_signature(att: Attestation, public_key: bytes, suite: CryptoSuite) -> None:
    """Raise ``ProtocolError(BAD_SIGNATURE)`` if the link's signature is invalid."""
    sig_bytes = b64url_decode(att.signature)
    if len(sig_bytes) != suite.sig_len:
        raise ProtocolError(ErrorCode.BAD_SIGNATURE, "signature length")
    body_bytes = canonicalize(att.signing_body())
    if not suite.verify(public_key, body_bytes, sig_bytes):
        raise ProtocolError(ErrorCode.BAD_SIGNATURE, "signature verification failed")
