"""Self-contained data shapes for the on-device backend.

Deliberately mirrors src/contracts.py (Chunk / Answer / RetrievalQuery) field-for-field so this
package is **drop-in compatible** with the main pipeline later — but imports nothing from src/, so
the on-device build stays fully isolated and liftable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)     # {source, section, page, product_id, ...}
    score: Optional[float] = None
    source: str = ""

    @property
    def product_id(self) -> Optional[str]:
        return self.metadata.get("product_id")


@dataclass
class Answer:
    text: str
    citations: list = field(default_factory=list)    # [{doc, section, score}]

    @property
    def is_refusal(self) -> bool:
        return not self.text or not self.citations


@dataclass
class RetrievalQuery:
    company_id: str
    text: str
    product_id: Optional[str] = None


@dataclass
class CatalogEntry:
    product_id: str
    name: str = ""
    brand: str = ""


class ConfigError(ValueError):
    """Raised on an unknown retrieval_mode / reason_endpoint — fail loud at startup, not a KeyError."""
