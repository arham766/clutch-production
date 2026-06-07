"""On-the-wire data model (HLD §4, LLD §A3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, timezone
from enum import Enum
from typing import Any, Optional

from .errors import ErrorCode, ProtocolError
from .timeutil import Clock, SystemClock, format_rfc3339

VERSION = "fact/0.2"


class Role(str, Enum):
    ORIGINATOR = "ORIGINATOR"
    RELAY = "RELAY"
    PROCESSOR = "PROCESSOR"


class Actionability(str, Enum):
    ACT = "ACT"
    VERIFY = "VERIFY"
    ESCALATE = "ESCALATE"


def _require(d: dict, key: str) -> Any:
    if key not in d:
        raise ProtocolError(ErrorCode.MALFORMED, f"missing field {key!r}")
    return d[key]


# --------------------------------------------------------------------------- #
# Claim
# --------------------------------------------------------------------------- #
@dataclass
class Claim:
    content: Any
    content_type: str
    claim_hash: str

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "content_type": self.content_type,
            "claim_hash": self.claim_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Claim":
        if not isinstance(d, dict):
            raise ProtocolError(ErrorCode.MALFORMED, "claim: not an object")
        return cls(
            content=_require(d, "content"),
            content_type=_require(d, "content_type"),
            claim_hash=_require(d, "claim_hash"),
        )


# --------------------------------------------------------------------------- #
# Attestation
# --------------------------------------------------------------------------- #
@dataclass
class Attestation:
    issuer: str
    key_id: str
    role: Role
    asserts: str
    issued_at: str
    claim_hash: str
    nonce: str
    prev: Optional[str] = None
    derives_from: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    ext: dict = field(default_factory=dict)
    signature: str = ""

    def to_dict(self) -> dict:
        """Full attestation, signature included. ``prev``/``derives_from`` are
        always present (explicit null when absent), per HLD §4.3."""
        return {
            "issuer": self.issuer,
            "key_id": self.key_id,
            "role": self.role.value if isinstance(self.role, Role) else self.role,
            "asserts": self.asserts,
            "issued_at": self.issued_at,
            "prev": self.prev,
            "derives_from": self.derives_from,
            "claim_hash": self.claim_hash,
            "nonce": self.nonce,
            "evidence": self.evidence,
            "ext": self.ext,
            "signature": self.signature,
        }

    def signing_body(self) -> dict:
        """The signed body: every field except ``signature`` (HLD §4.3)."""
        body = self.to_dict()
        del body["signature"]
        return body

    @classmethod
    def from_dict(cls, d: dict) -> "Attestation":
        if not isinstance(d, dict):
            raise ProtocolError(ErrorCode.MALFORMED, "attestation: not an object")
        role_raw = _require(d, "role")
        try:
            role = Role(role_raw)
        except ValueError:
            raise ProtocolError(ErrorCode.MALFORMED, f"attestation: bad role {role_raw!r}")
        evidence = d.get("evidence", {})
        ext = d.get("ext", {})
        if not isinstance(evidence, dict) or not isinstance(ext, dict):
            raise ProtocolError(ErrorCode.MALFORMED, "attestation: evidence/ext must be objects")
        return cls(
            issuer=_require(d, "issuer"),
            key_id=_require(d, "key_id"),
            role=role,
            asserts=_require(d, "asserts"),
            issued_at=_require(d, "issued_at"),
            claim_hash=_require(d, "claim_hash"),
            nonce=_require(d, "nonce"),
            prev=d.get("prev"),
            derives_from=d.get("derives_from"),
            evidence=evidence,
            ext=ext,
            signature=_require(d, "signature"),
        )


# --------------------------------------------------------------------------- #
# Trust (advisory; not part of any signed body)
# --------------------------------------------------------------------------- #
class Trust:
    def __init__(
        self,
        actionability: Actionability | str = Actionability.ACT,
        coverage: float = 1.0,
        reason: str = "",
        fresh_until: Optional[str] = None,
        not_before: Optional[str] = None,
        *,
        fresh_until_in_seconds: Optional[float] = None,
        clock: Optional[Clock] = None,
    ):
        self.actionability = (
            actionability if isinstance(actionability, Actionability)
            else Actionability(actionability)
        )
        self.coverage = float(coverage)
        self.reason = reason
        self.not_before = not_before
        if fresh_until_in_seconds is not None:
            now = (clock or SystemClock()).now().astimezone(timezone.utc)
            self.fresh_until = format_rfc3339(now + timedelta(seconds=fresh_until_in_seconds))
        else:
            self.fresh_until = fresh_until

    def to_dict(self) -> dict:
        d: dict = {
            "actionability": self.actionability.value,
            "coverage": self.coverage,
            "reason": self.reason,
        }
        if self.fresh_until is not None:
            d["fresh_until"] = self.fresh_until
        if self.not_before is not None:
            d["not_before"] = self.not_before
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Trust":
        if not isinstance(d, dict):
            raise ProtocolError(ErrorCode.MALFORMED, "trust: not an object")
        try:
            action = Actionability(_require(d, "actionability"))
        except ValueError:
            raise ProtocolError(ErrorCode.MALFORMED, "trust: bad actionability")
        return cls(
            actionability=action,
            coverage=d.get("coverage", 1.0),
            reason=d.get("reason", ""),
            fresh_until=d.get("fresh_until"),
            not_before=d.get("not_before"),
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Trust) and self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        return f"Trust({self.to_dict()})"


# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #
@dataclass
class Envelope:
    v: str
    alg: str
    nonce: str
    claim: Claim
    chain: list[Attestation]
    trust: Trust

    def to_dict(self) -> dict:
        return {
            "v": self.v,
            "alg": self.alg,
            "nonce": self.nonce,
            "claim": self.claim.to_dict(),
            "chain": [a.to_dict() for a in self.chain],
            "trust": self.trust.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Envelope":
        if not isinstance(d, dict):
            raise ProtocolError(ErrorCode.MALFORMED, "envelope: not an object")
        chain_raw = _require(d, "chain")
        if not isinstance(chain_raw, list):
            raise ProtocolError(ErrorCode.MALFORMED, "envelope: chain must be a list")
        return cls(
            v=_require(d, "v"),
            alg=_require(d, "alg"),
            nonce=_require(d, "nonce"),
            claim=Claim.from_dict(_require(d, "claim")),
            chain=[Attestation.from_dict(a) for a in chain_raw],
            trust=Trust.from_dict(_require(d, "trust")),
        )


# --------------------------------------------------------------------------- #
# Verify results (LLD §A3.2)
# --------------------------------------------------------------------------- #
@dataclass
class ChainHop:
    issuer: str
    key_id: str
    role: Role
    issued_at: str


@dataclass
class VerifyReport:
    originator: str
    hops: int
    trust_hint: Actionability
    coverage: float
    stale: bool
    issuer_trusted: bool
    fresh_until: Optional[str]
    nonce: str
    chain_summary: list[ChainHop]


@dataclass
class VerifyResult:
    ok: bool
    report: Optional[VerifyReport] = None
    error: Optional[ProtocolError] = None

    @property
    def is_valid(self) -> bool:
        return self.ok

    @classmethod
    def valid(cls, report: VerifyReport) -> "VerifyResult":
        return cls(ok=True, report=report)

    @classmethod
    def invalid(cls, error: ProtocolError) -> "VerifyResult":
        return cls(ok=False, error=error)


# --------------------------------------------------------------------------- #
# Options (LLD §A3.3)
# --------------------------------------------------------------------------- #
@dataclass
class SizeLimits:
    envelope_max_bytes: int = 2 * 1024 * 1024
    claim_max_bytes: int = 1 * 1024 * 1024
    chain_max_hops: int = 16
