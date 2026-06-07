"""Envelope (de)serialization and structural validation (LLD §A10.1).

No cryptography here — just bytes <-> typed records. Uses the frozen-bytes pattern:
``serialize`` canonicalizes once; the verifier may hash the literal bytes received.
"""

from __future__ import annotations

import json
from typing import Any

from .canonical import canonicalize
from .errors import ErrorCode, ProtocolError
from .types import Envelope


def serialize(env: Envelope) -> bytes:
    """Canonical (RFC 8785) byte serialization of an envelope."""
    return canonicalize(env.to_dict())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    seen: dict = {}
    for k, v in pairs:
        if k in seen:
            raise ProtocolError(ErrorCode.MALFORMED, f"duplicate object key {k!r}")
        seen[k] = v
    return seen


def deserialize(data: bytes | str) -> Envelope:
    """Parse envelope bytes/str into a typed ``Envelope``.

    Rejects duplicate object keys (a JCS/security requirement) and any structural
    violation as ``ProtocolError(MALFORMED)``.
    """
    try:
        parsed = json.loads(data, object_pairs_hook=_reject_duplicate_keys)
    except ProtocolError:
        raise
    except Exception as e:
        raise ProtocolError(ErrorCode.MALFORMED, f"json parse: {e}") from e
    return Envelope.from_dict(parsed)
