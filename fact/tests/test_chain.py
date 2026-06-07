"""Multi-hop chain tests: relay and processor (HLD §4.5, §8.2)."""

from __future__ import annotations

import fact


def test_relay_extends_chain_and_preserves_originator(valid_env, key, clock):
    relay_key = fact.generate_keypair()
    relayed = fact.sign_relay(valid_env, relay_key, clock=clock)
    r = fact.verify(relayed, clock=clock)
    assert r.is_valid
    assert r.report.hops == 2
    assert r.report.originator == key.issuer            # originator preserved
    assert relayed.chain[1].role == fact.Role.RELAY
    assert relayed.chain[1].prev is not None


def test_multi_hop_relay_chain(valid_env, clock):
    env = valid_env
    keys = [fact.generate_keypair() for _ in range(3)]
    for k in keys:
        env = fact.sign_relay(env, k, clock=clock)
    r = fact.verify(env, clock=clock)
    assert r.is_valid
    assert r.report.hops == 4
    assert [h.role for h in r.report.chain_summary[1:]] == [fact.Role.RELAY] * 3


def test_processor_creates_new_envelope_with_derivation(valid_env, key, clock):
    proc_key = fact.generate_keypair()
    derived = fact.sign_processor(
        valid_env,
        new_content={"summary": "no violations on record"},
        new_content_type="fact://types/summary/v1",
        new_trust=fact.Trust("VERIFY", 0.9),
        signing_key=proc_key,
        evidence={"method": "llm-summary"},
        clock=clock,
    )
    # New envelope, new claim, fresh chain rooted at the processor.
    assert derived.claim.claim_hash != valid_env.claim.claim_hash
    assert derived.nonce != valid_env.nonce
    assert derived.chain[0].role == fact.Role.PROCESSOR
    assert derived.chain[0].derives_from == valid_env.claim.claim_hash
    r = fact.verify(derived, clock=clock)
    assert r.is_valid
    assert r.report.originator == proc_key.issuer


def test_chain_with_custom_resolver(valid_env, key, clock):
    # Verify works when keys come from a non-did:key resolver too.
    from .conftest import static_resolver_for

    resolver = static_resolver_for(key)
    assert fact.verify(valid_env, clock=clock, key_resolver=resolver).is_valid
