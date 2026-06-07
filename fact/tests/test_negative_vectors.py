"""Negative vectors — invalid envelopes rejected with the right error (LLD §A11.3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

import fact
from fact.errors import ErrorCode
from .conftest import NOW, static_resolver_for


def _expect(env, code, **verify_kwargs):
    verify_kwargs.setdefault("clock", fact.FixedClock(NOW))
    r = fact.verify(env, **verify_kwargs)
    assert not r.is_valid, f"expected {code}, got VALID"
    assert r.error.code == code, f"expected {code}, got {r.error.code}"
    return r


def test_hash_mismatch_tampered_claim(valid_env):
    valid_env.claim.content["violation"] = "MANY"      # signatures intact
    _expect(valid_env, ErrorCode.HASH_MISMATCH)


def test_broken_chain(valid_env):
    relayed = fact.sign_relay(valid_env, fact.generate_keypair(), clock=fact.FixedClock(NOW))
    relayed.chain[1].prev = "A" * 43                    # wrong back-reference
    r = _expect(relayed, ErrorCode.BROKEN_CHAIN)
    assert r.error.cursor == 1


def test_nonce_mismatch(valid_env):
    valid_env.chain[0].nonce = "B" * 22
    _expect(valid_env, ErrorCode.NONCE_MISMATCH)


def test_claim_mismatch(valid_env):
    valid_env.chain[0].claim_hash = "A" * 43
    _expect(valid_env, ErrorCode.CLAIM_MISMATCH)


def test_naive_timestamp_malformed(valid_env):
    valid_env.chain[0].issued_at = "2026-05-27T12:00:00"   # no timezone
    _expect(valid_env, ErrorCode.MALFORMED)


def test_chain_too_long(valid_env):
    valid_env.chain = valid_env.chain * 17
    _expect(valid_env, ErrorCode.CHAIN_TOO_LONG)


def test_empty_chain_malformed(valid_env):
    valid_env.chain = []
    _expect(valid_env, ErrorCode.MALFORMED)


def test_bad_signature(valid_env):
    valid_env.chain[0].signature = fact.b64url_encode(b"\x00" * 64)
    _expect(valid_env, ErrorCode.BAD_SIGNATURE)


def test_unsupported_version(valid_env):
    valid_env.v = "fact/1.0"
    _expect(valid_env, ErrorCode.UNSUPPORTED_VERSION)


def test_unsupported_alg(valid_env):
    valid_env.alg = "rsa-sha1-jcs"
    _expect(valid_env, ErrorCode.UNSUPPORTED_ALG)


def test_unknown_issuer(valid_env):
    _expect(valid_env, ErrorCode.UNKNOWN_ISSUER, key_resolver=fact.StaticResolver({}))


def test_key_revoked(valid_env, key):
    resolver = static_resolver_for(key, revoked_at=NOW - timedelta(days=1))
    _expect(valid_env, ErrorCode.KEY_REVOKED, key_resolver=resolver)


def test_key_revoked_retroactive(valid_env, key):
    resolver = static_resolver_for(key, revoked_retroactive=True)
    _expect(valid_env, ErrorCode.KEY_REVOKED, key_resolver=resolver)


def test_key_expired(valid_env, key):
    resolver = static_resolver_for(key, valid_until=NOW - timedelta(days=1))
    _expect(valid_env, ErrorCode.KEY_EXPIRED, key_resolver=resolver)


def test_key_not_yet_valid_uses_expired_code(valid_env, key):
    resolver = static_resolver_for(key, valid_from=NOW + timedelta(days=1))
    _expect(valid_env, ErrorCode.KEY_EXPIRED, key_resolver=resolver)


def test_signatures_before_revocation_still_valid(valid_env, key):
    resolver = static_resolver_for(key, revoked_at=NOW + timedelta(days=1))
    r = fact.verify(valid_env, clock=fact.FixedClock(NOW), key_resolver=resolver)
    assert r.is_valid   # issued before revoked_at -> still good


def test_size_limit_claim(valid_env):
    limits = fact.SizeLimits(claim_max_bytes=5)
    _expect(valid_env, ErrorCode.SIZE_LIMIT_EXCEEDED, size_limits=limits)


def test_duplicate_keys_rejected_on_deserialize():
    raw = '{"v":"fact/0.2","v":"fact/0.2","alg":"x","nonce":"n","claim":{},"chain":[],"trust":{}}'
    with pytest.raises(fact.ProtocolError) as ei:
        fact.deserialize(raw)
    assert ei.value.code == ErrorCode.MALFORMED
