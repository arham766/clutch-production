"""OnDeviceMossRetriever — in-process, offline retrieval behind the same contract as the cloud path.

Loads the session once (mirrors MossRetriever), queries it, widens a weak product-scoped result to
company-wide before giving up, and stamps a client-side latency span. No cloud read; no push.
"""

from __future__ import annotations

import asyncio
import time

from .contracts import Chunk
from .local_store import LocalIndexStore


def _passing(chunks: list[Chunk], min_score: float) -> list[Chunk]:
    return [c for c in chunks if (c.score or 0.0) >= min_score]


class OnDeviceMossRetriever:
    def __init__(self, store: LocalIndexStore, cfg):
        self._store = store
        self._cfg = cfg
        self._session = None
        self._lock = asyncio.Lock()
        self.last_latency_ms: float | None = None

    async def _ensure_session(self):
        async with self._lock:
            if self._session is None:
                self._session = self._store.build_session()

    async def retrieve(self, company_id, text, product_id, *, top_k, min_score) -> list[Chunk]:
        await self._ensure_session()
        t0 = time.perf_counter()

        hits = self._session.query(text, top_k=top_k, product_id=product_id)
        passing = _passing(hits, min_score)
        # widen: product-scoped came back weak → retry company-wide before refusing (reuse-of-LLD §4.2)
        if product_id and not passing:
            hits = self._session.query(text, top_k=top_k, product_id=None)
            passing = _passing(hits, min_score)

        self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 2)  # in-process, no network
        return passing
