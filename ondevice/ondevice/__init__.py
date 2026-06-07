"""On-device (on-prem edge) retrieval & grounding — LLD 03-LOCAL, built as an ISOLATED package.

Imports nothing from `src/` and is imported by nothing in the main pipeline. It satisfies the same
(retrieve_for, compose) shape the Agent's S4 seam expects, so it can later drop into the main
`make_retrieval` registry unchanged — but for now it stands entirely alone and runs keyless.

    from ondevice import make_retrieval, OnDeviceConfig
    retrieve_for, compose = make_retrieval(OnDeviceConfig())
"""

from __future__ import annotations

from .compose import compose_answer, resolve_reason_endpoint, template_compose
from .config import OnDeviceConfig
from .contracts import Answer, Chunk, ConfigError, RetrievalQuery
from .local_store import LocalIndexStore
from .registry import REASON_ENDPOINTS, RETRIEVERS
from .retriever import OnDeviceMossRetriever

__all__ = [
    "make_retrieval", "OnDeviceConfig", "OnDeviceMossRetriever", "LocalIndexStore",
    "Answer", "Chunk", "RetrievalQuery", "ConfigError", "template_compose",
    "RETRIEVERS", "REASON_ENDPOINTS", "resolve_reason_endpoint",
]


def make_retrieval(cfg, gateway_client=None, local_client=None):
    """Read config → registry lookup → return (retrieve_for, compose). Fail loud on a bad mode."""
    if cfg.retrieval_mode not in RETRIEVERS:
        raise ConfigError(f"retrieval_mode={cfg.retrieval_mode!r} not in {list(RETRIEVERS)}")
    if cfg.reason_endpoint not in REASON_ENDPOINTS:
        raise ConfigError(f"reason_endpoint={cfg.reason_endpoint!r} not in {list(REASON_ENDPOINTS)}")
    retriever = RETRIEVERS[cfg.retrieval_mode](cfg)

    async def retrieve_for(query: RetrievalQuery):
        return await retriever.retrieve(query.company_id, query.text, query.product_id,
                                        top_k=cfg.retrieval_top_k, min_score=cfg.retrieval_min_score)

    async def compose(query_text: str, chunks):
        return await compose_answer(query_text, chunks, cfg=cfg, gateway_client=gateway_client,
                                    local_client=local_client, min_score=cfg.retrieval_min_score)

    return retrieve_for, compose
