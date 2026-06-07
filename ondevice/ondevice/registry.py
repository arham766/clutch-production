"""Config-keyed registry — selection is data, not branch code (LLD 03-LOCAL §4.1).

A new backend is one table entry + a config value, never an edit to make_retrieval.
"""

from __future__ import annotations

from .compose import resolve_reason_endpoint  # noqa: F401  (kept here so the table is the surface)
from .local_store import LocalIndexStore
from .retriever import OnDeviceMossRetriever

# retrieval_mode → builds a Retriever-shaped backend
RETRIEVERS = {
    "on_device": lambda cfg: OnDeviceMossRetriever(
        LocalIndexStore(cfg.local_index_path, embed_model=cfg.embed_model), cfg),
    # "local" (keyless cosine fallback) is the same backend over an empty/temp store in this package;
    # the main pipeline's LocalCosineRetriever fills that role there.
    "local": lambda cfg: OnDeviceMossRetriever(
        LocalIndexStore(cfg.local_index_path, embed_model=cfg.embed_model), cfg),
}

# reason_endpoint resolution lives in compose.resolve_reason_endpoint; named here for parity with LLD.
REASON_ENDPOINTS = ("local", "gateway", "auto")
