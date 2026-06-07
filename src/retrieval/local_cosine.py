"""LocalCosineRetriever — the Moss-free fallback AND the build-against mock (LLD 03 §8).

Same Retriever Protocol as MossRetriever, no network. Scores the query against each corpus chunk
with a deterministic, pure-python term-overlap relevance (no numpy), applies the product_id filter
in Python, sorts desc, and returns top-k Chunks with score in [0, 1] so min_score applies
uniformly. Dependency-free by design: the `--check` gate must pass on the base install. numpy
(declared in the optional `retrieval` group) is only relevant to a future dense-vector fallback.
"""

from __future__ import annotations

import re
from typing import Optional

from contracts import Chunk

from .base import index_name

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _bow(text: str) -> dict[str, float]:
    """Deterministic bag-of-words term-frequency vector (sparse, as a dict)."""
    vec: dict[str, float] = {}
    for tok in _tokens(text):
        vec[tok] = vec.get(tok, 0.0) + 1.0
    return vec


_STOP = {
    "the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "is", "are", "do",
    "i", "how", "my", "it", "with", "into", "from", "shows", "show", "this", "that",
    "you", "your", "can", "be", "at", "as", "by", "out",
}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Deterministic relevance in [0, 1].

    Mock-friendly: the overlap coefficient (shared content terms / min set size) over the query
    and chunk term SETS. This rewards "query terms present in the chunk" — the keyword-lean a
    hybrid retriever models — giving scores that meaningfully clear / fall below min_score (unlike
    a TF cosine, which is diluted to ~0.1 by long chunks). Inputs are the bag-of-words dicts so the
    Retriever Protocol's call sites stay unchanged.
    """
    qa = {t for t in a if t not in _STOP and (len(t) > 1 or t.isdigit())}
    qb = {t for t in b if t not in _STOP and (len(t) > 1 or t.isdigit())}
    if not qa or not qb:
        return 0.0
    inter = len(qa & qb)
    if inter == 0:
        return 0.0
    return inter / min(len(qa), len(qb))


def _default_corpus() -> list[Chunk]:
    """The clutch-demo fixture corpus: printer-manual snippets tagged with product_id.

    Shipped in-Limb because it is the real fallback path (not harness-only). The e2e harness
    layers a richer corpus on top; this default keeps the Limb self-contained and importable.
    """
    rows = [
        (
            "lj-m404-manual-p012-0001",
            "To clear a paper jam, power off the printer, open the rear access door, and gently "
            "pull the jammed sheet straight out. Do not tear the paper. Close the door and power on.",
            {"source": "lj-m404-manual.pdf", "section": "Clearing Paper Jams", "page": "12",
             "product_id": "lj-m404"},
        ),
        (
            "lj-m404-manual-p045-0002",
            "Error 13 indicates a paper jam in the fuser area. Turn off the device, wait ten "
            "minutes for the fuser to cool, then remove any caught paper from the rear of the unit.",
            {"source": "lj-m404-manual.pdf", "section": "Error Codes", "page": "45",
             "product_id": "lj-m404"},
        ),
        (
            "lj-m404-manual-p008-0003",
            "Load paper into the main tray with the print side down. Adjust the guides to the paper "
            "width. Overfilling the tray above the max line can cause misfeeds and jams.",
            {"source": "lj-m404-manual.pdf", "section": "Loading Paper", "page": "8",
             "product_id": "lj-m404"},
        ),
        (
            "warranty-p002-0004",
            "All devices carry a one year limited warranty covering manufacturing defects. The "
            "warranty does not cover damage from paper jams caused by non-approved media.",
            {"source": "warranty.pdf", "section": "Warranty Terms", "page": "2",
             "product_id": ""},
        ),
    ]
    return [
        Chunk(id=cid, text=text, metadata=meta, source=meta.get("source", ""))
        for cid, text, meta in rows
    ]


class LocalCosineRetriever:
    """Moss-free retriever over an in-memory corpus. Doubles as the deterministic mock."""

    def __init__(self, corpus: Optional[list[Chunk]] = None):
        self._corpus = list(corpus) if corpus is not None else _default_corpus()
        self._loaded: set[str] = set()

    async def load_index(self, company_id: str) -> str:
        name = index_name(company_id)
        self._loaded.add(name)
        return name

    async def search(
        self, company_id: str, text: str, product_id: Optional[str], top_k: int
    ) -> list[Chunk]:
        await self.load_index(company_id)
        qvec = _bow(text)
        scored: list[Chunk] = []
        for c in self._corpus:
            if product_id:
                if str(c.metadata.get("product_id", "")) != product_id:
                    continue
            score = _cosine(qvec, _bow(c.text))
            scored.append(
                Chunk(
                    id=c.id,
                    text=c.text,
                    metadata=dict(c.metadata),
                    score=float(score),
                    source=c.metadata.get("source", c.source),
                )
            )
        scored.sort(key=lambda c: (c.score or 0.0), reverse=True)
        return scored[: top_k or len(scored)]
