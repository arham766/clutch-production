"""OnDeviceConfig — role-named keys (config names roles, not vendors).

Reads the LLD 03-LOCAL §13 env surface. All keys have safe defaults so the package runs keyless.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class OnDeviceConfig:
    retrieval_mode: str = "on_device"        # on_device | local
    reason_endpoint: str = "auto"            # auto (local-first) | local | gateway
    local_index_path: str = "./clutch-local"  # dir: chunks.jsonl + catalog.json + manifest.json
    embed_model: str = "moss-minilm"         # on-device embedder role (stand-in if moss absent)
    local_reason_base_url: str = ""          # OpenAI-compatible local LLM URL (else template/gateway)
    retrieval_alpha: float = 0.70
    retrieval_top_k: int = 5
    # Lexical-cosine stand-in scores lower than real semantic embeddings; the floor is set for it.
    # A real Moss on-device embedder would use ~0.35 (LLD 03-LOCAL §5).
    retrieval_min_score: float = 0.20

    @classmethod
    def from_env(cls) -> "OnDeviceConfig":
        g = os.environ.get
        return cls(
            retrieval_mode=g("CLUTCH_RETRIEVAL_MODE", "on_device"),
            reason_endpoint=g("CLUTCH_REASON_ENDPOINT", "auto"),
            local_index_path=g("CLUTCH_LOCAL_INDEX_PATH", "./clutch-local"),
            embed_model=g("CLUTCH_EMBED_MODEL", "moss-minilm"),
            local_reason_base_url=g("LOCAL_REASON_BASE_URL", ""),
        )
