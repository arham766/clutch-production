"""Identity and key resolution (HLD §7, LLD §A8).

``did:key`` is fully offline: the identifier *contains* the public key. The
verifier decodes the key directly — no network. ``did:web`` and custom resolvers
plug in via the ``KeyResolver`` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .crypto import DEFAULT_SUITE, b64url_encode
from .errors import ErrorCode, ProtocolError

# multicodec varint prefix for an Ed25519 public key: 0xed 0x01
_ED25519_MULTICODEC = bytes([0xED, 0x01])
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


# --------------------------------------------------------------------------- #
# base58btc (Bitcoin alphabet)
# --------------------------------------------------------------------------- #
def b58_encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _B58_ALPHABET[rem] + out
    # preserve leading zero bytes as '1'
    pad = 0
    for b in data:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def b58_decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch)
        if idx == -1:
            raise ProtocolError(ErrorCode.UNKNOWN_ISSUER, f"bad base58 char {ch!r}")
        n = n * 58 + idx
    full = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + full


# --------------------------------------------------------------------------- #
# did:key encode / decode
# --------------------------------------------------------------------------- #
def encode_did_key(public_raw: bytes) -> str:
    """Build a ``did:key`` identifier from a 32-byte Ed25519 public key."""
    if len(public_raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "did:key:z" + b58_encode(_ED25519_MULTICODEC + public_raw)


def decode_did_key(issuer: str) -> bytes:
    """Decode a ``did:key`` Ed25519 identifier to its 32-byte public key."""
    if not issuer.startswith("did:key:z"):
        raise ProtocolError(ErrorCode.UNKNOWN_ISSUER, f"not a did:key: {issuer!r}")
    decoded = b58_decode(issuer[len("did:key:z"):])
    if decoded[:2] != _ED25519_MULTICODEC:
        raise ProtocolError(ErrorCode.UNKNOWN_ISSUER, "unsupported did:key multicodec")
    key = decoded[2:]
    if len(key) != 32:
        raise ProtocolError(ErrorCode.UNKNOWN_ISSUER, "bad did:key length")
    return key


# --------------------------------------------------------------------------- #
# Key records and resolvers
# --------------------------------------------------------------------------- #
@dataclass
class KeyRecord:
    public_key: bytes
    suite: str = DEFAULT_SUITE
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_retroactive: bool = False


class KeyResolver(Protocol):
    def resolve(self, issuer: str, key_id: str) -> Optional[KeyRecord]: ...


class DidKeyResolver:
    """Fully offline resolver: the key lives inside the identifier."""

    def resolve(self, issuer: str, key_id: str) -> Optional[KeyRecord]:
        if not issuer.startswith("did:key:"):
            return None
        try:
            return KeyRecord(public_key=decode_did_key(issuer), suite=DEFAULT_SUITE)
        except ProtocolError:
            return None


class StaticResolver:
    """In-memory resolver keyed by (issuer, key_id) — useful for did:web sims/tests."""

    def __init__(self, records: Optional[dict[tuple[str, str], KeyRecord]] = None):
        self._records = dict(records or {})

    def add(self, issuer: str, key_id: str, record: KeyRecord) -> None:
        self._records[(issuer, key_id)] = record

    def resolve(self, issuer: str, key_id: str) -> Optional[KeyRecord]:
        return self._records.get((issuer, key_id))


class CompositeResolver:
    """Dispatches across resolvers by identifier scheme; first hit wins."""

    def __init__(self, resolvers: list[KeyResolver]):
        self._resolvers = resolvers

    def resolve(self, issuer: str, key_id: str) -> Optional[KeyRecord]:
        for r in self._resolvers:
            rec = r.resolve(issuer, key_id)
            if rec is not None:
                return rec
        return None


# --------------------------------------------------------------------------- #
# Signing key (caller-owned key material)
# --------------------------------------------------------------------------- #
class SigningKey:
    """A local Ed25519 signing key with its ``did:key`` identity.

    In production the private key would live in a KMS/HSM behind this same
    interface; here it is held in process for the reference implementation.
    """

    def __init__(self, private: Ed25519PrivateKey):
        self._private = private
        self._public_raw = private.public_key().public_bytes_raw()
        self._issuer = encode_did_key(self._public_raw)
        # For did:key, key_id is conventionally the multibase suffix.
        self._key_id = self._issuer[len("did:key:"):]

    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_bytes(self) -> bytes:
        return self._public_raw

    @property
    def suite(self) -> str:
        return DEFAULT_SUITE

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(message)

    def private_bytes_raw(self) -> bytes:
        return self._private.private_bytes_raw()

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "SigningKey":
        return cls(Ed25519PrivateKey.from_private_bytes(raw))


def generate_keypair() -> SigningKey:
    return SigningKey(Ed25519PrivateKey.generate())
