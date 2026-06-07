"""Shared data contracts for Clutch — the Spine. Every module imports only this.

`IdentifyResult` is what perception (02) produces when a customer hits "See" — it scopes Moss
retrieval (product_id) and drives the query (problem). `Chunk`/`RetrievalQuery`/`Answer` are the
retrieval seam (03↔04). `SupportSession` is owned + mutated by the Agent (04) and built by Realtime
(05). Reconciled across Cognition (02/04) and Inference (03/05): one set of shapes, no forks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# Product modes, widget-facing (HLD 00 §3): Type (text) / Talk (voice) / See (camera).
Modality = Literal["type", "talk", "see"]


@dataclass
class CatalogEntry:
    """One product in a company's catalog (derived at onboarding from uploaded docs).

    The closed set the vision step classifies against. `ref_image` (a representative image
    extracted from the manual by Unsiloed) makes classification image-to-image and far more
    reliable than name-only.
    """

    product_id: str
    name: str
    brand: str = ""
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    ref_image: Optional[bytes] = None


@dataclass
class ProblemSignals:
    """What the camera sees is *wrong* — drives the Moss retrieval query."""

    error_codes: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)   # e.g. "amber blinking light"
    parts: list[str] = field(default_factory=list)         # e.g. "rear paper tray"
    damage: list[str] = field(default_factory=list)
    summary: str = ""

    def to_query(self) -> str:
        """Full query (prose summary + signal terms) — for display / LLM context."""
        terms = self.error_codes + self.indicators + self.parts + self.damage
        return (self.summary + " " + " ".join(terms)).strip()

    def to_keywords(self) -> str:
        """Distilled keyword query (signal terms only, no prose) — for RETRIEVAL.

        The vision model's prose summary dilutes keyword/overlap retrieval (long query → low score);
        the structured signals (codes, indicators, parts, damage) are what actually match doc chunks.
        """
        return " ".join(self.error_codes + self.indicators + self.parts + self.damage).strip()


@dataclass
class Candidate:
    product_id: str
    confidence: float


@dataclass
class IdentifyResult:
    """Output of `identify`: which product + what's wrong, with a confirm flag for the widget."""

    product_id: Optional[str]              # matched catalog id, or None if unknown
    confidence: float
    model_source: str                       # "ocr_label" | "visual_match" | "none"
    brand: str = ""
    model: str = ""
    raw_text: str = ""                       # verbatim text read off a model/serial label
    candidates: list[Candidate] = field(default_factory=list)
    needs_confirmation: bool = False
    problem: ProblemSignals = field(default_factory=ProblemSignals)

    @property
    def resolved(self) -> bool:
        """True when we can safely scope retrieval without asking the customer."""
        return self.product_id is not None and not self.needs_confirmation


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval & grounding shapes (LLD 03 §2 / §9.1). Producer of metadata keys is the
# ingest chunker: {source, section, page, segment_count, doc_index} (+ product_id once
# onboarding stamps it). `source` mirrors metadata['source'] for convenience.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Chunk:
    """A retrieved doc chunk (03). Moss metadata is string-valued."""

    id: str
    text: str
    metadata: dict = field(default_factory=dict)
    score: Optional[float] = None
    source: str = ""               # human-readable cite, e.g. "User Guide §3.2"


@dataclass
class RetrievalQuery:
    """Agent (04) → Retrieval (03). The seam shape the Agent hands to retrieve_for()."""

    company_id: str
    text: str
    product_id: Optional[str] = None
    need: list = field(default_factory=list)         # soft keyword hints, e.g. ["steps", "warning"]


@dataclass
class Answer:
    """Retrieval/Agent → Realtime (05). Grounded, cited answer.

    `citations` = the grounding shown to the user [{doc, section?, score?}]. `time_taken_ms` is the
    client-side Moss span (LLD 03 §5.1); None → the latency badge is hidden (05-realtime §6).
    Empty `text` is the **refusal flag** the Agent reads (03 §7.1).
    """

    text: str
    citations: list[dict] = field(default_factory=list)
    time_taken_ms: Optional[int] = None

    @property
    def is_refusal(self) -> bool:
        return not self.text


# ─────────────────────────────────────────────────────────────────────────────
# Session / tenancy shapes (00 §3) — Realtime's scope_session returns a SupportSession;
# the Agent owns + mutates it (state, FSM phase, history) across modality escalation.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Company:
    """Tenant (06). The embed snippet's company key resolves to this."""

    company_id: str
    name: str = ""
    index_name: str = ""           # Moss index "clutch-<company>"
    catalog: list[CatalogEntry] = field(default_factory=list)
    api_key: str = ""


@dataclass
class SupportSession:
    """One customer session (04/05). Owned + mutated by the Agent.

    Field order keeps Cognition's positional `SupportSession(session_id, company_id)` working while
    Inference constructs it by keyword (`company_id=`, `modality=`, `session_id=`). `phase` drives
    the Agent FSM; `history`/`product_id` carry over as the modality escalates.
    """

    session_id: str = ""
    company_id: str = ""
    modality: Modality = "type"
    product_id: Optional[str] = None
    phase: str = "greeting"        # FSM: greeting→assisting→confirming→escalating→resolved
    history: list = field(default_factory=list)
    catalog: list = field(default_factory=list)   # company products (S6) — feeds vision + confirm prompts
