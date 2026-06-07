"""Public surface: three sign functions and one verify (HLD §8, LLD §A10.2).

Everything else in the package is internal. ``verify`` returns a structured
``VerifyResult`` — never a bare boolean — so the receiver makes the
act / verify / escalate decision from facts the protocol has proven.
"""

from __future__ import annotations

from typing import Any, Optional

from .attestation import make_signed_attestation
from .canonical import canonicalize
from .chain import compute_prev_hash, verify_chain
from .crypto import (
    CLAIM_HASH_B64_LEN,
    DEFAULT_SUITE,
    b64url_encode,
    get_suite,
    is_supported_suite,
    is_valid_nonce_shape,
    fresh_nonce,
    sha256,
)
from .envelope import serialize
from .errors import ErrorCode, ProtocolError
from .identity import DidKeyResolver, KeyResolver, SigningKey
from .replay import ReplayCache
from .timeutil import Clock, SystemClock, format_rfc3339, is_after, is_before, parse_rfc3339
from .types import (
    VERSION,
    Actionability,
    Attestation,
    Claim,
    ChainHop,
    Envelope,
    Role,
    SizeLimits,
    Trust,
    VerifyReport,
    VerifyResult,
)


def _claim_hash(content: Any) -> str:
    return b64url_encode(sha256(canonicalize(content)))


def _now_str(clock: Optional[Clock], issued_at: Optional[str]) -> str:
    if issued_at is not None:
        return issued_at
    return format_rfc3339((clock or SystemClock()).now())


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #
def sign_originator(
    content: Any,
    content_type: str,
    trust: Trust,
    signing_key: SigningKey,
    *,
    evidence: Optional[dict] = None,
    asserts: str = "true_as_of",
    nonce: Optional[str] = None,
    issued_at: Optional[str] = None,
    suite: str = DEFAULT_SUITE,
    clock: Optional[Clock] = None,
) -> Envelope:
    """Originate a fact: hash the content, sign the first attestation."""
    suite_obj = get_suite(suite)
    claim = Claim(content=content, content_type=content_type, claim_hash=_claim_hash(content))
    env_nonce = nonce or fresh_nonce()
    att = make_signed_attestation(
        role=Role.ORIGINATOR,
        asserts=asserts,
        issued_at=_now_str(clock, issued_at),
        claim_hash=claim.claim_hash,
        nonce=env_nonce,
        signing_key=signing_key,
        suite=suite_obj,
        prev=None,
        derives_from=None,
        evidence=evidence or {},
    )
    return Envelope(v=VERSION, alg=suite, nonce=env_nonce, claim=claim, chain=[att], trust=trust)


def sign_relay(
    env: Envelope,
    signing_key: SigningKey,
    *,
    asserts: str = "relayed_unchanged",
    issued_at: Optional[str] = None,
    evidence: Optional[dict] = None,
    clock: Optional[Clock] = None,
) -> Envelope:
    """Append a RELAY attestation. Caller MUST have verified ``env`` first."""
    suite_obj = get_suite(env.alg)
    prev_hash = compute_prev_hash(env.chain[-1])
    att = make_signed_attestation(
        role=Role.RELAY,
        asserts=asserts,
        issued_at=_now_str(clock, issued_at),
        claim_hash=env.claim.claim_hash,
        nonce=env.nonce,
        signing_key=signing_key,
        suite=suite_obj,
        prev=prev_hash,
        derives_from=None,
        evidence=evidence or {},
    )
    return Envelope(
        v=env.v, alg=env.alg, nonce=env.nonce, claim=env.claim,
        chain=env.chain + [att], trust=env.trust,
    )


def sign_processor(
    env: Envelope,
    new_content: Any,
    new_content_type: str,
    new_trust: Trust,
    signing_key: SigningKey,
    *,
    evidence: Optional[dict] = None,
    asserts: str = "derives_from",
    nonce: Optional[str] = None,
    issued_at: Optional[str] = None,
    suite: str = DEFAULT_SUITE,
    clock: Optional[Clock] = None,
) -> Envelope:
    """Transform a fact into a new derived fact in a NEW envelope (HLD §4.5).

    The new originator-equivalent attestation records ``derives_from`` = the
    source claim's hash. Caller MUST have verified ``env`` first.
    """
    suite_obj = get_suite(suite)
    claim = Claim(content=new_content, content_type=new_content_type, claim_hash=_claim_hash(new_content))
    env_nonce = nonce or fresh_nonce()
    att = make_signed_attestation(
        role=Role.PROCESSOR,
        asserts=asserts,
        issued_at=_now_str(clock, issued_at),
        claim_hash=claim.claim_hash,
        nonce=env_nonce,
        signing_key=signing_key,
        suite=suite_obj,
        prev=None,
        derives_from=env.claim.claim_hash,
        evidence=evidence or {},
    )
    return Envelope(v=VERSION, alg=suite, nonce=env_nonce, claim=claim, chain=[att], trust=new_trust)


# --------------------------------------------------------------------------- #
# Verifying
# --------------------------------------------------------------------------- #
def verify(
    env: Envelope,
    *,
    key_resolver: Optional[KeyResolver] = None,
    trusted_issuers: Optional[set[str]] = None,
    skew_tolerance: float = 300.0,
    seen_nonce_cache: Optional[ReplayCache] = None,
    size_limits: Optional[SizeLimits] = None,
    clock: Optional[Clock] = None,
) -> VerifyResult:
    """Validate an envelope offline. Returns a structured ``VerifyResult``."""
    resolver = key_resolver or DidKeyResolver()
    limits = size_limits or SizeLimits()
    clk = clock or SystemClock()

    try:
        # 1. Static limits FIRST (DoS protection before any crypto)
        if len(serialize(env)) > limits.envelope_max_bytes:
            return VerifyResult.invalid(ProtocolError(ErrorCode.SIZE_LIMIT_EXCEEDED, "envelope"))
        if len(canonicalize(env.claim.content)) > limits.claim_max_bytes:
            return VerifyResult.invalid(ProtocolError(ErrorCode.SIZE_LIMIT_EXCEEDED, "claim"))
        if len(env.chain) > limits.chain_max_hops:
            return VerifyResult.invalid(ProtocolError(ErrorCode.CHAIN_TOO_LONG))
        if len(env.chain) == 0:
            return VerifyResult.invalid(ProtocolError(ErrorCode.MALFORMED, "empty chain"))

        # 2. Version + suite
        if env.v != VERSION:
            return VerifyResult.invalid(ProtocolError(ErrorCode.UNSUPPORTED_VERSION, env.v))
        if not is_supported_suite(env.alg):
            return VerifyResult.invalid(ProtocolError(ErrorCode.UNSUPPORTED_ALG, env.alg))
        suite_obj = get_suite(env.alg)

        # 3. Nonce shape
        if not is_valid_nonce_shape(env.nonce):
            return VerifyResult.invalid(ProtocolError(ErrorCode.MALFORMED, "nonce shape"))
        if len(env.claim.claim_hash) != CLAIM_HASH_B64_LEN:
            return VerifyResult.invalid(ProtocolError(ErrorCode.MALFORMED, "claim_hash shape"))

        # 4. Replay check BEFORE crypto (cache committed only after full success)
        if seen_nonce_cache is not None and seen_nonce_cache.contains(env.nonce):
            return VerifyResult.invalid(ProtocolError(ErrorCode.REPLAY_DETECTED))

        # 5. Content integrity — recompute, never trust
        recomputed = _claim_hash(env.claim.content)
        if recomputed != env.claim.claim_hash:
            return VerifyResult.invalid(ProtocolError(ErrorCode.HASH_MISMATCH))

        # 6. Walk the chain (structural rules, bindings, key state, signatures)
        verify_chain(env, key_resolver=resolver, suite=suite_obj)

        # 7. Temporal validity (clock skew)
        now = clk.now()
        if env.trust.not_before is not None:
            nb = parse_rfc3339(env.trust.not_before)
            if is_before(now, nb, skew_tolerance):
                return VerifyResult.invalid(ProtocolError(ErrorCode.NOT_YET_VALID))
        stale = False
        if env.trust.fresh_until is not None:
            fu = parse_rfc3339(env.trust.fresh_until)
            stale = is_after(now, fu, skew_tolerance)

        # 8. Issuer-trust policy
        originator = env.chain[0].issuer
        issuer_trusted = trusted_issuers is None or originator in trusted_issuers

        # 9. Commit nonce to replay cache (only after full success)
        if seen_nonce_cache is not None:
            seen_nonce_cache.insert(env.nonce, env.trust.fresh_until)

        report = VerifyReport(
            originator=originator,
            hops=len(env.chain),
            trust_hint=env.trust.actionability,
            coverage=env.trust.coverage,
            stale=stale,
            issuer_trusted=issuer_trusted,
            fresh_until=env.trust.fresh_until,
            nonce=env.nonce,
            chain_summary=[
                ChainHop(issuer=a.issuer, key_id=a.key_id, role=a.role, issued_at=a.issued_at)
                for a in env.chain
            ],
        )
        return VerifyResult.valid(report)

    except ProtocolError as e:
        return VerifyResult.invalid(e)
