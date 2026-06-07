"""Replay cache (HLD §4.7, LLD §A10.3).

The protocol provides the *mechanism* (a signed, unique nonce per envelope); the
receiver chooses the *policy*. This is the default in-memory implementation,
bounded by size with simple FIFO eviction.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Protocol


class ReplayCache(Protocol):
    def contains(self, nonce: str) -> bool: ...
    def insert(self, nonce: str, fresh_until: Optional[str] = None) -> None: ...


class InMemoryReplayCache:
    def __init__(self, max_size: int = 100_000):
        self._max_size = max_size
        self._entries: "OrderedDict[str, Optional[str]]" = OrderedDict()

    def contains(self, nonce: str) -> bool:
        return nonce in self._entries

    def insert(self, nonce: str, fresh_until: Optional[str] = None) -> None:
        if nonce in self._entries:
            return
        self._entries[nonce] = fresh_until
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)  # evict oldest

    def __len__(self) -> int:
        return len(self._entries)
