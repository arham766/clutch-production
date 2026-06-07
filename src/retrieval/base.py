"""The Retriever Protocol every backend implements (LLD 03 §4).

Two interchangeable backends sit behind this Protocol: MossRetriever (index.py, the real
cloud read path) and LocalCosineRetriever (local_cosine.py, the Moss-free fallback + mock).
Both return list[Chunk]; callers never see which one served.
"""

from __future__ import annotations

from typing import Optional, Protocol

from contracts import Chunk


class Retriever(Protocol):
    async def load_index(self, company_id: str) -> str:
        """Resolve + warm clutch-<company_id>. Returns the loaded index name. Idempotent."""
        ...

    async def search(
        self, company_id: str, text: str, product_id: Optional[str], top_k: int
    ) -> list[Chunk]:
        """Hybrid query of the company index, product_id metadata filter, top-k → Chunk[]."""
        ...


def index_name(company_id: str) -> str:
    """The only naming rule (00 §5)."""
    return f"clutch-{company_id}"
