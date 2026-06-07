"""Closed error taxonomy (HLD §9, LLD §A4).

The library raises ``ProtocolError`` and only ``ProtocolError``. Crypto, JSON, and
I/O exceptions are caught at module boundaries and translated into one of the
``ErrorCode`` values below — callers never see a third-party stack trace.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    UNSUPPORTED_ALG = "UNSUPPORTED_ALG"
    MALFORMED = "MALFORMED"
    SIZE_LIMIT_EXCEEDED = "SIZE_LIMIT_EXCEEDED"
    CHAIN_TOO_LONG = "CHAIN_TOO_LONG"
    HASH_MISMATCH = "HASH_MISMATCH"
    BROKEN_CHAIN = "BROKEN_CHAIN"
    CLAIM_MISMATCH = "CLAIM_MISMATCH"
    NONCE_MISMATCH = "NONCE_MISMATCH"
    UNKNOWN_ISSUER = "UNKNOWN_ISSUER"
    KEY_REVOKED = "KEY_REVOKED"
    KEY_EXPIRED = "KEY_EXPIRED"
    BAD_SIGNATURE = "BAD_SIGNATURE"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    NOT_YET_VALID = "NOT_YET_VALID"


class ProtocolError(Exception):
    """A protocol-level failure carrying a closed error code.

    ``context`` is human-readable debugging detail and MUST NOT be used for
    control flow. ``cursor`` is the chain index where the error occurred, if any.
    """

    __slots__ = ("code", "context", "cursor")

    def __init__(self, code: ErrorCode, context: str = "", cursor: Optional[int] = None):
        self.code = code
        self.context = context
        self.cursor = cursor
        loc = f" at chain[{cursor}]" if cursor is not None else ""
        super().__init__(f"{code.value}{loc}: {context}" if context else f"{code.value}{loc}")

    def with_cursor(self, cursor: int) -> "ProtocolError":
        if self.cursor is None:
            self.cursor = cursor
        return self
