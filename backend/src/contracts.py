"""
Clutch shared contracts — the Spine.

Every component imports from here. These are pure data shapes (dataclasses),
never interfaces or logic. Frozen at kickoff; changes require all-hands sign-off.

Reference: HLD 00 §3 (system overview — shared contracts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

Modality = Literal["text", "voice", "video"]

DocType = Literal[
    "user_guide",
    "service_manual",
    "spec_sheet",
    "troubleshooting",
    "other",
]

ProductStatus = Literal[
    "draft",
    "uploaded",
    "parsing",
    "indexing",
    "ready",
    "error",
]

ParseStatus = Literal[
    "pending",
    "parsing",
    "succeeded",
    "failed",
]


# ---------------------------------------------------------------------------
# Onboarding / Corpus (HLD 01)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One product in a company's catalog (from onboarding).

    ``ref_image`` is the reference photo the company uploads — used by
    Perception (02) for closed-set visual matching.
    """

    product_id: str
    name: str
    brand: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    ref_image: bytes | None = None


# ---------------------------------------------------------------------------
# Perception (HLD 02)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ProblemSignals:
    """What the camera sees is wrong — drives the retrieval query.

    Extracted by Perception (02) in the same vision pass that identifies the
    product. ``to_query()`` concatenates the signals into a retrieval-ready
    string.
    """

    error_codes: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    damage: list[str] = field(default_factory=list)
    summary: str = ""

    def to_query(self) -> str:
        """Build a retrieval query string from all non-empty signals."""
        fragments: list[str] = []
        if self.error_codes:
            fragments.append(f"error codes: {', '.join(self.error_codes)}")
        if self.indicators:
            fragments.append(f"indicators: {', '.join(self.indicators)}")
        if self.parts:
            fragments.append(f"parts: {', '.join(self.parts)}")
        if self.damage:
            fragments.append(f"damage: {', '.join(self.damage)}")
        if self.summary:
            fragments.append(self.summary)
        return "; ".join(fragments) if fragments else ""


@dataclass(frozen=True, slots=True)
class Candidate:
    """A ranked candidate product from closed-set classification."""

    product_id: str
    confidence: float
    name: str = ""


@dataclass(slots=True)
class IdentifyResult:
    """Output of Perception (02) — product identification + problem signals.

    ``model_source`` describes the *signal type* (``ocr_label``,
    ``visual_match``, ``none``), never the vendor.
    """

    product_id: str | None
    confidence: float
    model_source: str  # ocr_label | visual_match | none
    brand: str = ""
    model: str = ""
    raw_text: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    needs_confirmation: bool = False
    problem: ProblemSignals = field(default_factory=ProblemSignals)

    @property
    def resolved(self) -> bool:
        """True only when the product is confidently identified."""
        return self.product_id is not None and not self.needs_confirmation


# ---------------------------------------------------------------------------
# Retrieval & Grounding (HLD 03)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrieved document chunk from the Moss index.

    Metadata is **string-valued only** (Moss constraint). The ``source`` and
    ``metadata`` fields carry provenance for citation.
    """

    id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)
    score: float | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """Agent (04) → Retrieval (03): what to look up.

    ``product_id`` scopes retrieval within the company index.
    ``text`` is the natural-language query (user message and/or
    ``ProblemSignals.to_query()``).
    """

    company_id: str
    text: str
    product_id: str | None = None
    need: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Answer:
    """Retrieval/Agent → Widget: the grounded response.

    Every claim is backed by an entry in ``citations``. If retrieval returned
    nothing, ``text`` is empty and the agent says "not in docs" / escalates.
    """

    text: str
    citations: list[dict[str, str | float]] = field(default_factory=list)
    # Each citation: {"doc": str, "section": str, "score": float}


# ---------------------------------------------------------------------------
# Multi-tenancy (HLD 06)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Company:
    """A tenant — the company whose support page embeds the Clutch widget.

    The ``api_key`` is carried by the embed snippet (``data-clutch-key``).
    The ``index_name`` is the Moss index holding this company's doc chunks.
    """

    company_id: str
    name: str
    index_name: str  # "clutch-<company_id>"
    catalog: list[CatalogEntry] = field(default_factory=list)
    api_key: str = ""


# ---------------------------------------------------------------------------
# Session (HLD 04 / 05)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SupportSession:
    """One customer support session — owned by the Agent (04).

    ``product_id`` starts ``None`` and is set when Perception resolves
    the device (or the customer picks it). ``phase`` tracks the FSM state.
    """

    session_id: str
    company_id: str
    modality: Modality
    product_id: str | None = None
    phase: str = "greeting"  # greeting | assisting | confirming | escalating | resolved
    history: list[dict[str, str]] = field(default_factory=list)
