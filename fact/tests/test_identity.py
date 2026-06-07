"""did:key, base58, and resolver tests (HLD §7, LLD §A8)."""

from __future__ import annotations

import pytest

import fact
from fact.errors import ErrorCode
from fact.identity import b58_decode, b58_encode, decode_did_key, encode_did_key


def test_did_key_roundtrip(key):
    issuer = key.issuer
    assert issuer.startswith("did:key:z6Mk")  # Ed25519 multicodec marker
    assert decode_did_key(issuer) == key.public_key_bytes
    assert encode_did_key(key.public_key_bytes) == issuer


def test_base58_roundtrip():
    import os

    for _ in range(20):
        data = os.urandom(32)
        assert b58_decode(b58_encode(data)) == data


def test_base58_preserves_leading_zeros():
    data = b"\x00\x00\x01\x02"
    assert b58_encode(data).startswith("11")
    assert b58_decode(b58_encode(data)) == data


def test_didkey_resolver_offline(key):
    rec = fact.DidKeyResolver().resolve(key.issuer, key.key_id)
    assert rec is not None
    assert rec.public_key == key.public_key_bytes


def test_didkey_resolver_rejects_non_didkey():
    assert fact.DidKeyResolver().resolve("did:web:example.com", "k1") is None


def test_decode_bad_did_key_raises():
    with pytest.raises(fact.ProtocolError) as ei:
        decode_did_key("did:key:zNotARealKey!!!")
    assert ei.value.code == ErrorCode.UNKNOWN_ISSUER


def test_signing_key_serialization_roundtrip(key):
    restored = fact.SigningKey.from_private_bytes(key.private_bytes_raw())
    assert restored.issuer == key.issuer
    assert restored.public_key_bytes == key.public_key_bytes
