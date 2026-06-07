"""Replay protection (HLD §4.7, LLD §A11.3 vec_neg_007)."""

from __future__ import annotations

import fact
from fact.errors import ErrorCode
from .conftest import NOW


def test_same_envelope_twice_is_replay(valid_env):
    cache = fact.InMemoryReplayCache()
    clock = fact.FixedClock(NOW)

    first = fact.verify(valid_env, clock=clock, seen_nonce_cache=cache)
    assert first.is_valid
    assert len(cache) == 1

    second = fact.verify(valid_env, clock=clock, seen_nonce_cache=cache)
    assert not second.is_valid
    assert second.error.code == ErrorCode.REPLAY_DETECTED


def test_failed_verification_does_not_consume_nonce(valid_env):
    cache = fact.InMemoryReplayCache()
    clock = fact.FixedClock(NOW)

    valid_env.chain[0].signature = fact.b64url_encode(b"\x00" * 64)  # will fail
    bad = fact.verify(valid_env, clock=clock, seen_nonce_cache=cache)
    assert not bad.is_valid
    assert len(cache) == 0   # nonce slot not consumed on failure


def test_distinct_envelopes_not_flagged(key):
    cache = fact.InMemoryReplayCache()
    clock = fact.FixedClock(NOW)
    for i in range(3):
        env = fact.sign_originator(
            content={"i": i}, content_type="fact://t/v1",
            trust=fact.Trust("ACT", 1.0), signing_key=key, clock=clock,
        )
        assert fact.verify(env, clock=clock, seen_nonce_cache=cache).is_valid
    assert len(cache) == 3


def test_cache_eviction_bounded():
    cache = fact.InMemoryReplayCache(max_size=2)
    cache.insert("a"); cache.insert("b"); cache.insert("c")
    assert len(cache) == 2
    assert not cache.contains("a")   # oldest evicted
    assert cache.contains("c")
