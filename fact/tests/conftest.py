"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import fact

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return fact.FixedClock(NOW)


@pytest.fixture
def key():
    return fact.generate_keypair()


@pytest.fixture
def valid_env(key, clock):
    """A fresh, valid single-hop originator envelope."""
    return fact.sign_originator(
        content={"violation": "none", "parcel": "123"},
        content_type="fact://types/code_enforcement/violation/v1",
        trust=fact.Trust("ACT", 0.98, "clean record", fresh_until_in_seconds=3600, clock=clock),
        signing_key=key,
        clock=clock,
    )


def static_resolver_for(key, **record_kwargs):
    """A StaticResolver that returns the given key's pubkey with extra state."""
    rec = fact.KeyRecord(public_key=key.public_key_bytes, **record_kwargs)
    return fact.StaticResolver({(key.issuer, key.key_id): rec})
