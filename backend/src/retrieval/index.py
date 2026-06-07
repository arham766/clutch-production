"""MossRetriever — the real Moss cloud read path (LLD 03 §5).

Lazy `from moss import ...` (mirrors qwen_gateway.py): the mock/CI path never installs moss.
Load-once cache (per-index asyncio.Lock so concurrent first-touches don't double-load), hybrid
query with alpha blend, product_id metadata filter inside QueryOptions, client-side latency span
(the result carries no time_taken_ms), and a defensive doc→Chunk mapper.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from src.contracts import Chunk

from .base import index_name


class MossRetriever:
    def __init__(
        self,
        project_id: str,
        project_key: str,
        *,
        alpha: float = 0.70,
        default_top_k: int = 5,
    ):
        self._project_id = project_id
        self._project_key = project_key
        self._alpha = alpha
        self._default_top_k = default_top_k
        self._client = None
        self._loaded: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_latency_ms: Optional[float] = None

    def _client_(self):
        if self._client is None:
            from moss import MossClient  # lazy: mock path needs no moss install

            self._client = MossClient(self._project_id, self._project_key)
        return self._client

    def _query_options(self, top_k: int, product_id: Optional[str]):
        from moss import QueryOptions  # lazy

        filt = None
        if product_id:
            filt = {
                "$and": [
                    {"field": "product_id", "condition": {"$eq": product_id}}
                ]
            }
        return QueryOptions(top_k=top_k, alpha=self._alpha, filter=filt)

    async def load_index(self, company_id: str) -> str:
        name = index_name(company_id)
        if name in self._loaded:
            return name  # idempotent hot path
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name not in self._loaded:  # double-checked
                await self._client_().load_index(name)
                self._loaded.add(name)
        return name

    @staticmethod
    def _to_chunk(doc) -> Chunk:
        raw_meta = getattr(doc, "metadata", None) or {}
        meta = {str(k): str(v) for k, v in dict(raw_meta).items()}
        meta.setdefault("source", "")
        meta.setdefault("section", "")
        score = getattr(doc, "score", None)
        return Chunk(
            id=str(getattr(doc, "id", "")),
            text=str(getattr(doc, "text", "")),
            metadata=meta,
            score=float(score) if score is not None else None,
            source=meta.get("source", ""),
        )

    async def search(
        self, company_id: str, text: str, product_id: Optional[str], top_k: int
    ) -> list[Chunk]:
        name = await self.load_index(company_id)
        opts = self._query_options(top_k or self._default_top_k, product_id)
        t0 = time.perf_counter()
        res = await self._client_().query(name, text, opts)  # filter rides inside opts (§5.1)
        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
        # opportunistic override only when the SDK reports a *positive* server-side number
        # (moss 1.4 returns time_taken_ms=0); otherwise keep the real client-side span.
        override = getattr(res, "time_taken_ms", None)
        if override is not None and float(override) > 0:
            self.last_latency_ms = float(override)
        return [self._to_chunk(d) for d in (getattr(res, "docs", None) or [])]
