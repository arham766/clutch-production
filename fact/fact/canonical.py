"""JCS canonicalization (RFC 8785) — the highest-risk module (HLD §5, LLD §A6).

Per the LLD we do NOT roll our own canonicalizer; we delegate to a vetted RFC 8785
library and confine the risk to this one wrapper. Duplicate-key and NaN/Infinity
rejection happen here so every other module can assume clean input.
"""

from __future__ import annotations

import unicodedata
from typing import Any

import rfc8785

from .errors import ErrorCode, ProtocolError


def canonicalize(value: Any) -> bytes:
    """Return the RFC 8785 canonical byte serialization of a JSON value.

    Raises ``ProtocolError(MALFORMED)`` on values JCS cannot represent
    (NaN, Infinity, non-JSON types).
    """
    try:
        return rfc8785.dumps(value)
    except ProtocolError:
        raise
    except Exception as e:  # defensive: never leak a third-party exception type
        raise ProtocolError(ErrorCode.MALFORMED, f"canonicalization: {e}") from e


def nfc_normalize(s: str) -> str:
    """Unicode NFC normalization helper.

    JCS does not normalize strings; callers MUST normalize user-supplied strings
    to NFC before canonicalization to avoid 'same data, different hash' bugs.
    """
    return unicodedata.normalize("NFC", s)
