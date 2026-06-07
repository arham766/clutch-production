"""Cryptographic suite registry, hashing, and encoding (HLD §6, LLD §A7).

v0.2 ships exactly one mandatory suite: ``ed25519-sha256-jcs``. Unknown suite
identifiers are a hard error — never a silent fallback (prevents downgrade attacks).
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .errors import ErrorCode, ProtocolError

DEFAULT_SUITE = "ed25519-sha256-jcs"
NONCE_BYTES = 16  # 128-bit
NONCE_B64_LEN = 22  # b64url(16 bytes), unpadded
CLAIM_HASH_B64_LEN = 43  # b64url(sha256 -> 32 bytes), unpadded


# --------------------------------------------------------------------------- #
# base64url (no padding), strict
# --------------------------------------------------------------------------- #
def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise ProtocolError(ErrorCode.MALFORMED, "b64url: not a string")
    if "=" in s or "+" in s or "/" in s:
        raise ProtocolError(ErrorCode.MALFORMED, "b64url: non-canonical encoding")
    pad = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception as e:
        raise ProtocolError(ErrorCode.MALFORMED, f"b64url: {e}") from e


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def fresh_nonce() -> str:
    """16 random bytes from the OS CSPRNG, b64url-encoded."""
    return b64url_encode(os.urandom(NONCE_BYTES))


def is_valid_nonce_shape(nonce: str) -> bool:
    return isinstance(nonce, str) and len(nonce) == NONCE_B64_LEN


# --------------------------------------------------------------------------- #
# Suite registry
# --------------------------------------------------------------------------- #
def _ed25519_sign(private_raw: bytes, message: bytes) -> bytes:
    return Ed25519PrivateKey.from_private_bytes(private_raw).sign(message)


def _ed25519_verify(public_raw: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass(frozen=True)
class CryptoSuite:
    identifier: str
    sign: Callable[[bytes, bytes], bytes]
    verify: Callable[[bytes, bytes, bytes], bool]
    hash: Callable[[bytes], bytes]
    pubkey_len: int
    sig_len: int


_SUITES: dict[str, CryptoSuite] = {
    DEFAULT_SUITE: CryptoSuite(
        identifier=DEFAULT_SUITE,
        sign=_ed25519_sign,
        verify=_ed25519_verify,
        hash=sha256,
        pubkey_len=32,
        sig_len=64,
    ),
    # Reserved suites (HLD §6.1) are intentionally absent until activated.
}


def get_suite(identifier: str) -> CryptoSuite:
    suite = _SUITES.get(identifier)
    if suite is None:
        raise ProtocolError(ErrorCode.UNSUPPORTED_ALG, identifier)
    return suite


def is_supported_suite(identifier: str) -> bool:
    return identifier in _SUITES
