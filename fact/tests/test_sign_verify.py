"""Positive protocol vectors + properties (LLD §A11.3-§A11.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest

import fact
from .conftest import NOW


def test_originator_verifies(valid_env, key, clock):
    r = fact.verify(valid_env, clock=clock)
    assert r.is_valid
    assert r.report.originator == key.issuer
    assert r.report.hops == 1
    assert r.report.trust_hint == fact.Actionability.ACT
    assert r.report.stale is False
    assert r.report.coverage == pytest.approx(0.98)


def test_byte_roundtrip_preserves_validity(valid_env, clock):
    data = fact.serialize(valid_env)
    restored = fact.deserialize(data)
    assert restored == valid_env
    assert fact.verify(restored, clock=clock).is_valid


def test_signature_is_deterministic(key, clock):
    # Same inputs (fixed nonce + issued_at) -> identical signature bytes (Ed25519).
    kwargs = dict(
        content={"a": 1},
        content_type="fact://t/v1",
        trust=fact.Trust("ACT", 1.0),
        signing_key=key,
        nonce="AAAAAAAAAAAAAAAAAAAAAA",
        issued_at="2026-06-04T12:00:00Z",
    )
    e1 = fact.sign_originator(**kwargs)
    e2 = fact.sign_originator(**kwargs)
    assert e1.chain[0].signature == e2.chain[0].signature


def test_fresh_until_makes_stale_when_expired(key, clock):
    env = fact.sign_originator(
        content={"a": 1}, content_type="fact://t/v1",
        trust=fact.Trust("ACT", 1.0, fresh_until_in_seconds=-10, clock=clock),
        signing_key=key, clock=clock,
    )
    r = fact.verify(env, clock=clock, skew_tolerance=0)
    assert r.is_valid          # stale is NOT an error
    assert r.report.stale is True


def test_not_before_in_future_is_not_yet_valid(key, clock):
    future = fact.format_rfc3339(NOW + timedelta(hours=1))
    env = fact.sign_originator(
        content={"a": 1}, content_type="fact://t/v1",
        trust=fact.Trust("ACT", 1.0, not_before=future),
        signing_key=key, clock=clock,
    )
    r = fact.verify(env, clock=clock, skew_tolerance=0)
    assert not r.is_valid
    assert r.error.code == fact.ErrorCode.NOT_YET_VALID


def test_trusted_issuers_policy(valid_env, key, clock):
    r = fact.verify(valid_env, clock=clock, trusted_issuers={key.issuer})
    assert r.report.issuer_trusted is True
    r2 = fact.verify(valid_env, clock=clock, trusted_issuers={"did:key:zSomeoneElse"})
    assert r2.is_valid and r2.report.issuer_trusted is False  # validity != trust policy


def test_tamper_any_field_breaks_verification(valid_env, clock):
    # Property: mutating the claim content invalidates the envelope.
    valid_env.claim.content["violation"] = "MANY"
    assert not fact.verify(valid_env, clock=clock).is_valid


def test_string_content_supported(key, clock):
    env = fact.sign_originator(
        content="Acme is SOC 2 Type II certified.",
        content_type="fact://types/kb/chunk/v1",
        trust=fact.Trust("VERIFY", 0.8), signing_key=key, clock=clock,
    )
    assert fact.verify(env, clock=clock).is_valid
