"""Tests for the FACT <-> Moss bridge (sign at ingest, verify at retrieval)."""

from __future__ import annotations

from datetime import datetime, timezone

import fact
import pytest

import fact_moss
from fact_moss import (
    FACT_ENVELOPE,
    FACT_ISSUER,
    Decision,
    envelope_to_metadata,
    sign_chunk,
    verify_document,
)

NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return fact.FixedClock(NOW)


@pytest.fixture
def kb_key():
    return fact.generate_keypair()


def _chunk():
    return {
        "id": "acme-p2-0001",
        "text": "Acme is SOC 2 Type II certified and GDPR compliant.",
        "metadata": {"source": "acme.pdf", "section": "Security", "page": "2"},
    }


# ---------------------------------------------------------------- sign-side
def test_sign_chunk_adds_fact_metadata(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, clock=clock)
    assert FACT_ENVELOPE in c["metadata"]
    assert c["metadata"][FACT_ISSUER] == kb_key.issuer
    assert c["metadata"]["source"] == "acme.pdf"          # original metadata preserved
    # All metadata values must be strings (Moss requirement).
    assert all(isinstance(v, str) for v in c["metadata"].values())


def test_envelope_metadata_omits_content(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, clock=clock)
    import json

    env_json = json.loads(c["metadata"][FACT_ENVELOPE])
    assert "content" not in env_json["claim"]              # content lives in doc.text only
    assert env_json["claim"]["claim_hash"]                 # hash retained


# ---------------------------------------------------------------- verify-side
def test_roundtrip_act(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, clock=clock)
    v = verify_document(c["text"], c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.ACT
    assert v.speakable
    assert v.report.originator == kb_key.issuer


def test_tampered_text_is_refused(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, clock=clock)
    tampered = "Acme is NOT certified."                    # attacker edits the fact
    v = verify_document(tampered, c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.REFUSE
    assert "HASH_MISMATCH" in v.reason


def test_unsigned_chunk_is_refused(clock):
    c = _chunk()                                           # never signed
    v = verify_document(c["text"], c["metadata"], clock=clock)
    assert v.decision == Decision.REFUSE
    assert "unsigned" in v.reason


def test_foreign_issuer_escalates(kb_key, clock):
    # Signed by an attacker's own key; signature is valid but issuer isn't trusted.
    attacker = fact.generate_keypair()
    c = sign_chunk(_chunk(), attacker, clock=clock)
    v = verify_document(c["text"], c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.ESCALATE
    assert v.result.is_valid                                # integrity ok, trust not


def test_stale_fact_hedges(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, fresh_seconds=-3600, clock=clock)   # expired 1h ago
    v = verify_document(c["text"], c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock, skew_tolerance=0)
    assert v.decision == Decision.HEDGE
    assert "stale" in v.reason


def test_verify_tier_hedges(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, actionability="VERIFY", clock=clock)
    v = verify_document(c["text"], c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.HEDGE


def test_escalate_tier(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, actionability="ESCALATE", clock=clock)
    v = verify_document(c["text"], c["metadata"],
                        trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.ESCALATE


def test_verify_moss_doc_wrapper(kb_key, clock):
    c = sign_chunk(_chunk(), kb_key, clock=clock)

    class Doc:
        text = c["text"]
        metadata = c["metadata"]

    v = fact_moss.verify_moss_doc(Doc(), trusted_issuers={kb_key.issuer}, clock=clock)
    assert v.decision == Decision.ACT


# ---------------------------------------------------------------- key mgmt
def test_load_kb_key_creates_and_reloads(tmp_path):
    path = str(tmp_path / "kb.b64")
    k1 = fact_moss.load_kb_key(path)
    k2 = fact_moss.load_kb_key(path)
    assert k1.issuer == k2.issuer                          # stable across reloads


def test_load_kb_key_from_env(monkeypatch, kb_key):
    import base64

    monkeypatch.setenv("FACT_KB_PRIVATE_KEY",
                       base64.b64encode(kb_key.private_bytes_raw()).decode())
    loaded = fact_moss.load_kb_key()
    assert loaded.issuer == kb_key.issuer


# ---------------------------------------------------------------- pipeline
def test_sign_then_chunker_pipeline(kb_key, clock):
    from chunker import chunk_result

    sample = {"chunks": [{"segments": [
        {"segment_type": "SectionHeader", "content": "Pricing", "page_number": 1, "segment_id": "h"},
        {"segment_type": "Text", "markdown": "Pro plan is $40/mo.", "page_number": 1, "segment_id": "t"},
    ]}]}
    chunks = chunk_result(sample, source="acme.pdf")
    signed = fact_moss.sign_chunks(chunks, kb_key, clock=clock)
    for c in signed:
        v = verify_document(c["text"], c["metadata"],
                            trusted_issuers={kb_key.issuer}, clock=clock)
        assert v.decision == Decision.ACT
