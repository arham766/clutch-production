"""Chain construction and the verification loop (LLD §A9.2-§A9.3)."""

from __future__ import annotations

from .attestation import verify_attestation_signature
from .canonical import canonicalize
from .crypto import CryptoSuite, b64url_encode
from .errors import ErrorCode, ProtocolError
from .identity import KeyResolver
from .timeutil import parse_rfc3339
from .types import Attestation, Envelope, Role


def compute_prev_hash(att: Attestation) -> str:
    """b64url(sha256(canonicalize(full attestation, signature included)))."""
    return b64url_encode(_sha256(canonicalize(att.to_dict())))


def _sha256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha256(data).digest()


def verify_chain(env: Envelope, *, key_resolver: KeyResolver, suite: CryptoSuite) -> None:
    """Walk the chain enforcing structural rules, bindings, key state, signatures.

    Raises ``ProtocolError`` (with cursor) on the first failure. Returns None on success.
    """
    prev_hash = None
    for i, att in enumerate(env.chain):
        # 6a. Structural rules.
        # The root (index 0) is either an ORIGINATOR (asserts a real-world source,
        # derives_from=null) or a PROCESSOR (a derived fact, derives_from=source
        # claim_hash) per HLD §4.5/§8.2. Both have prev=null. Only RELAY may follow.
        if i == 0:
            if att.prev is not None:
                raise ProtocolError(ErrorCode.MALFORMED, "root must have prev=null", cursor=i)
            if att.role == Role.ORIGINATOR:
                if att.derives_from is not None:
                    raise ProtocolError(ErrorCode.MALFORMED, "originator must not derive", cursor=i)
            elif att.role == Role.PROCESSOR:
                if att.derives_from is None:
                    raise ProtocolError(ErrorCode.MALFORMED, "processor root must set derives_from", cursor=i)
            else:
                raise ProtocolError(ErrorCode.MALFORMED, "first link must be ORIGINATOR or PROCESSOR", cursor=i)
        else:
            if att.role != Role.RELAY:
                raise ProtocolError(ErrorCode.MALFORMED, "only RELAY allowed after the root", cursor=i)
            if att.prev != prev_hash:
                raise ProtocolError(ErrorCode.BROKEN_CHAIN, cursor=i)

        # 6b. Required binding fields
        if att.claim_hash != env.claim.claim_hash:
            raise ProtocolError(ErrorCode.CLAIM_MISMATCH, cursor=i)
        if att.nonce != env.nonce:
            raise ProtocolError(ErrorCode.NONCE_MISMATCH, cursor=i)

        # 6c. Timestamp shape (must include timezone)
        att_time = parse_rfc3339(att.issued_at)  # raises MALFORMED if naive/bad

        # 6d. Resolve key honoring rotation/revocation state
        record = key_resolver.resolve(att.issuer, att.key_id)
        if record is None:
            raise ProtocolError(ErrorCode.UNKNOWN_ISSUER, cursor=i)
        if record.revoked_retroactive:
            raise ProtocolError(ErrorCode.KEY_REVOKED, "retroactively revoked", cursor=i)
        if record.revoked_at is not None and att_time >= record.revoked_at:
            raise ProtocolError(ErrorCode.KEY_REVOKED, cursor=i)
        if record.valid_until is not None and att_time > record.valid_until:
            raise ProtocolError(ErrorCode.KEY_EXPIRED, "after valid_until", cursor=i)
        if record.valid_from is not None and att_time < record.valid_from:
            raise ProtocolError(ErrorCode.KEY_EXPIRED, "before valid_from", cursor=i)

        # 6e. Signature
        try:
            verify_attestation_signature(att, record.public_key, suite)
        except ProtocolError as e:
            raise e.with_cursor(i)

        prev_hash = compute_prev_hash(att)
