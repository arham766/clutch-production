"""FACT — Facts, Attested, Chained, Transferable. Protocol library (v0.2)."""

from __future__ import annotations

from .api import sign_originator, sign_processor, sign_relay, verify
from .canonical import canonicalize, nfc_normalize
from .crypto import DEFAULT_SUITE, b64url_decode, b64url_encode, fresh_nonce
from .envelope import deserialize, serialize
from .errors import ErrorCode, ProtocolError
from .identity import (
    CompositeResolver,
    DidKeyResolver,
    KeyRecord,
    KeyResolver,
    SigningKey,
    StaticResolver,
    decode_did_key,
    encode_did_key,
    generate_keypair,
)
from .replay import InMemoryReplayCache, ReplayCache
from .timeutil import FixedClock, SystemClock, format_rfc3339, parse_rfc3339
from .types import (
    VERSION,
    Actionability,
    Attestation,
    ChainHop,
    Claim,
    Envelope,
    Role,
    SizeLimits,
    Trust,
    VerifyReport,
    VerifyResult,
)

__version__ = "0.2.0"

__all__ = [
    # api
    "sign_originator", "sign_relay", "sign_processor", "verify",
    # envelope
    "serialize", "deserialize",
    # identity
    "generate_keypair", "SigningKey", "KeyRecord", "KeyResolver",
    "DidKeyResolver", "StaticResolver", "CompositeResolver",
    "encode_did_key", "decode_did_key",
    # types
    "Envelope", "Claim", "Attestation", "Trust", "Role", "Actionability",
    "VerifyResult", "VerifyReport", "ChainHop", "SizeLimits", "VERSION",
    # crypto / canonical
    "canonicalize", "nfc_normalize", "b64url_encode", "b64url_decode",
    "fresh_nonce", "DEFAULT_SUITE",
    # replay / time / errors
    "InMemoryReplayCache", "ReplayCache",
    "SystemClock", "FixedClock", "parse_rfc3339", "format_rfc3339",
    "ProtocolError", "ErrorCode",
]
