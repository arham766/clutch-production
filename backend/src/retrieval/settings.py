"""Role-named retrieval knobs, read (duck-typed) from a Config + lazily from env.

Config names roles, never vendors (00 §7). Vendor secrets (Moss creds) live in env and are
read lazily here / inside MossRetriever, never stored in config.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class RetrievalSettings:
    alpha: float = 0.70
    top_k: int = 5
    min_score: float = 0.35
    reason_model: str = "reason.compose"
    moss_project_id: str = ""
    moss_project_key: str = ""

    @classmethod
    def from_config(cls, cfg: Optional[object] = None) -> "RetrievalSettings":
        """Pull only the attrs we need from a Config (duck-typed); fall back to env + defaults."""

        def g(name: str, default):
            if cfg is not None and hasattr(cfg, name):
                val = getattr(cfg, name)
                if val is not None:
                    return val
            return default

        return cls(
            alpha=g("retrieval_alpha", 0.70),
            top_k=g("retrieval_top_k", 5),
            min_score=g("retrieval_min_score", 0.35),
            reason_model=g("reason_model", "reason.compose"),
            moss_project_id=g("moss_project_id", os.environ.get("MOSS_PROJECT_ID", "")),
            moss_project_key=g("moss_project_key", os.environ.get("MOSS_PROJECT_KEY", "")),
        )
