"""Timestamps and clock-skew (HLD §7.4, LLD §A5).

One canonical parser, one canonical formatter, and a skew comparator. All
timestamps MUST carry an explicit timezone; naive timestamps are rejected at the
single point where time enters the system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .errors import ErrorCode, ProtocolError


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Injectable clock for tests."""

    def __init__(self, instant: datetime):
        if instant.tzinfo is None:
            raise ValueError("FixedClock requires an aware datetime")
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


def parse_rfc3339(s: str) -> datetime:
    """Parse an RFC 3339 timestamp. Rejects naive (timezone-less) values.

    Accepts ``Z`` and ``±HH:MM`` offsets and fractional seconds.
    """
    if not isinstance(s, str) or not s:
        raise ProtocolError(ErrorCode.MALFORMED, "timestamp: empty or non-string")
    try:
        dt = datetime.fromisoformat(s)  # Python 3.11 accepts 'Z' and offsets
    except ValueError as e:
        raise ProtocolError(ErrorCode.MALFORMED, f"timestamp: {e}") from e
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ProtocolError(ErrorCode.MALFORMED, f"timestamp without timezone: {s!r}")
    return dt.astimezone(timezone.utc)


def format_rfc3339(t: datetime) -> str:
    """Emit a UTC ``Z``-suffixed timestamp; fractional seconds only if non-zero."""
    if t.tzinfo is None:
        raise ValueError("format_rfc3339 requires an aware datetime")
    t = t.astimezone(timezone.utc)
    if t.microsecond:
        s = t.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0")
    else:
        s = t.strftime("%Y-%m-%dT%H:%M:%S")
    return s + "Z"


def is_before(now: datetime, threshold: datetime, skew_seconds: float) -> bool:
    """True if ``now`` is before ``threshold`` even allowing for skew tolerance."""
    return (threshold - now).total_seconds() > skew_seconds


def is_after(now: datetime, threshold: datetime, skew_seconds: float) -> bool:
    """True if ``now`` is after ``threshold`` even allowing for skew tolerance."""
    return (now - threshold).total_seconds() > skew_seconds
