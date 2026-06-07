"""Retrieval & Grounding — the only path from the Agent's brain to the company docs (LLD 03).

Public surface (S4): load_index, retrieve, retrieve_for, compose, make_retrieval.

Imports ONLY contracts (and a passed-in gateway_client via make_retrieval). Never imports a
sibling subfolder. Two interchangeable backends (MossRetriever / LocalCosineRetriever) sit behind
one Protocol; the widening + Moss→local fallback policy lives in the public retrieve().
"""

from __future__ import annotations

from typing import Optional

from contracts import Answer, Chunk, RetrievalQuery

from .base import Retriever, index_name
from .compose import (
    gateway_compose,
    gateway_compose_race,
    gateway_compose_stream_raw,
    template_compose,
    to_citation,
)
from .index import MossRetriever
from .local_cosine import LocalCosineRetriever
from .query import build_query_text
from .settings import RetrievalSettings

__all__ = [
    "load_index",
    "retrieve",
    "retrieve_for",
    "compose",
    "make_retrieval",
    # building blocks (also used by the e2e harness + unit tests)
    "Retriever",
    "MossRetriever",
    "LocalCosineRetriever",
    "template_compose",
    "gateway_compose",
    "gateway_compose_stream_raw",
    "gateway_compose_race",
    "to_citation",
    "build_query_text",
    "index_name",
    "RetrievalSettings",
]

# Module-level default backend for the zero-config public functions (mock, keyless).
_DEFAULT_BACKEND: Retriever = LocalCosineRetriever()


async def load_index(company_id: str, *, _backend: Optional[Retriever] = None) -> str:
    """Resolve + load + warm clutch-<company>. Idempotent (second call is a cache hit)."""
    backend = _backend or _DEFAULT_BACKEND
    return await backend.load_index(company_id)


def _all_below(chunks: list[Chunk], min_score: float) -> bool:
    return not any((c.score is not None and c.score >= min_score) for c in chunks)


async def retrieve(
    company_id: str,
    text: str,
    product_id: Optional[str] = None,
    *,
    top_k: int = 5,
    min_score: float = 0.35,
    _primary: Optional[Retriever] = None,
    _fallback: Optional[Retriever] = None,
) -> list[Chunk]:
    """Hybrid retrieve with the widening policy (§5.4) + Moss→local fallback (§4).

    1. Query as asked (product_id filter).
    2. If [] or every hit < min_score → re-query company-wide (drop product_id).
    3. Still empty → return [] (never invent).
    On a primary backend error, fall through to the local-cosine fallback over the same query.
    """
    primary = _primary or _DEFAULT_BACKEND

    async def _run(backend: Retriever) -> list[Chunk]:
        hits = await backend.search(company_id, text, product_id, top_k)
        # widening: weak/empty first pass → company-wide re-query
        if product_id and (not hits or _all_below(hits, min_score)):
            widened = await backend.search(company_id, text, None, top_k)
            if widened:
                return widened
        return hits

    try:
        return await _run(primary)
    except Exception:
        fallback = _fallback or LocalCosineRetriever()
        return await _run(fallback)


async def retrieve_for(
    query: RetrievalQuery,
    *,
    top_k: int = 5,
    min_score: float = 0.35,
    _primary: Optional[Retriever] = None,
    _fallback: Optional[Retriever] = None,
) -> list[Chunk]:
    """Convenience over retrieve(): builds query text from the RetrievalQuery (§6)."""
    text = build_query_text(query)
    return await retrieve(
        query.company_id,
        text,
        query.product_id,
        top_k=top_k,
        min_score=min_score,
        _primary=_primary,
        _fallback=_fallback,
    )


def compose(answer_query: str, chunks: list[Chunk], *, min_score: float = 0.35) -> Answer:
    """Module-level default compose == template_compose (deterministic, no LLM, zero deps).

    The LLM-backed version is produced by make_retrieval(); empty text is the refusal flag.
    """
    return template_compose(answer_query, chunks, min_score=min_score)


def make_retrieval(cfg=None, gateway_client=None):
    """Factory used by src/app.py. Binds index config + the compose LLM; returns
    (retrieve_for, compose) closures the Agent calls with no extra arguments.

    gateway_client is None  → compose == template_compose (mock-first, no LLM).
    gateway_client present   → compose calls gateway_compose via the injected client.
    Moss creds present       → primary backend is MossRetriever, local-cosine is the fallback.
    """
    s = RetrievalSettings.from_config(cfg)

    if s.moss_project_id and s.moss_project_key:
        primary: Retriever = MossRetriever(
            s.moss_project_id, s.moss_project_key, alpha=s.alpha, default_top_k=s.top_k
        )
    else:
        primary = LocalCosineRetriever()
    fallback: Retriever = LocalCosineRetriever()

    async def _retrieve_for(query: RetrievalQuery) -> list[Chunk]:
        return await retrieve_for(
            query,
            top_k=s.top_k,
            min_score=s.min_score,
            _primary=primary,
            _fallback=fallback,
        )

    if gateway_client is None:
        def _compose(answer_query: str, chunks: list[Chunk], *, min_score: float = s.min_score) -> Answer:
            return template_compose(answer_query, chunks, min_score=min_score)

        return _retrieve_for, _compose

    async def _compose_llm(answer_query: str, chunks: list[Chunk], *, min_score: float = s.min_score) -> Answer:
        return await gateway_compose(
            answer_query,
            chunks,
            client=gateway_client,
            model=s.reason_model,
            min_score=min_score,
        )

    return _retrieve_for, _compose_llm
