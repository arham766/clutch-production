"""
Tests for src/contracts.py — shared data shapes (the Spine).

Covers:
- Round-trip construction + field access for every dataclass
- ProblemSignals.to_query() concatenation logic
- IdentifyResult.resolved property
- Frozen-ness where applicable
- Default field values
"""

from __future__ import annotations

import pytest

from src.contracts import (
    Answer,
    Candidate,
    CatalogEntry,
    Chunk,
    Company,
    IdentifyResult,
    ProblemSignals,
    RetrievalQuery,
    SupportSession,
)


# ---------------------------------------------------------------------------
# CatalogEntry
# ---------------------------------------------------------------------------

class TestCatalogEntry:
    def test_minimal_construction(self) -> None:
        entry = CatalogEntry(product_id="p1", name="Widget Pro")
        assert entry.product_id == "p1"
        assert entry.name == "Widget Pro"
        assert entry.brand == ""
        assert entry.aliases == []
        assert entry.description == ""
        assert entry.ref_image is None

    def test_full_construction(self) -> None:
        img = b"\x89PNG"
        entry = CatalogEntry(
            product_id="p2",
            name="Gadget X",
            brand="AcmeCorp",
            aliases=["GX", "Gadget-X"],
            description="A fine gadget",
            ref_image=img,
        )
        assert entry.brand == "AcmeCorp"
        assert entry.aliases == ["GX", "Gadget-X"]
        assert entry.ref_image == img

    def test_frozen(self) -> None:
        entry = CatalogEntry(product_id="p1", name="Widget")
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProblemSignals
# ---------------------------------------------------------------------------

class TestProblemSignals:
    def test_empty_to_query(self) -> None:
        ps = ProblemSignals()
        assert ps.to_query() == ""

    def test_full_to_query(self) -> None:
        ps = ProblemSignals(
            error_codes=["E01", "E02"],
            indicators=["amber blinking light"],
            parts=["rear paper tray"],
            damage=["cracked cover"],
            summary="Paper jam with blinking error",
        )
        q = ps.to_query()
        assert "error codes: E01, E02" in q
        assert "indicators: amber blinking light" in q
        assert "parts: rear paper tray" in q
        assert "damage: cracked cover" in q
        assert "Paper jam with blinking error" in q

    def test_partial_to_query(self) -> None:
        ps = ProblemSignals(error_codes=["E42"], summary="Something wrong")
        q = ps.to_query()
        assert "E42" in q
        assert "Something wrong" in q
        # Fields not set should not appear
        assert "indicators" not in q
        assert "parts" not in q
        assert "damage" not in q


# ---------------------------------------------------------------------------
# IdentifyResult
# ---------------------------------------------------------------------------

class TestIdentifyResult:
    def test_resolved_true(self) -> None:
        result = IdentifyResult(
            product_id="p1",
            confidence=0.95,
            model_source="ocr_label",
            needs_confirmation=False,
        )
        assert result.resolved is True

    def test_resolved_false_needs_confirmation(self) -> None:
        result = IdentifyResult(
            product_id="p1",
            confidence=0.55,
            model_source="visual_match",
            needs_confirmation=True,
        )
        assert result.resolved is False

    def test_resolved_false_no_product(self) -> None:
        result = IdentifyResult(
            product_id=None,
            confidence=0.0,
            model_source="none",
        )
        assert result.resolved is False

    def test_candidates(self) -> None:
        result = IdentifyResult(
            product_id="p1",
            confidence=0.7,
            model_source="visual_match",
            candidates=[
                Candidate(product_id="p1", confidence=0.7, name="Widget Pro"),
                Candidate(product_id="p2", confidence=0.6, name="Widget Lite"),
            ],
        )
        assert len(result.candidates) == 2
        assert result.candidates[0].product_id == "p1"

    def test_default_problem_signals(self) -> None:
        result = IdentifyResult(
            product_id="p1", confidence=0.9, model_source="ocr_label"
        )
        assert result.problem.to_query() == ""


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------

class TestChunk:
    def test_construction(self) -> None:
        chunk = Chunk(
            id="c1",
            text="Step 1: Open the cover.",
            metadata={"product_id": "p1", "doc_type": "user_guide", "section": "§3.2"},
            score=0.92,
            source="user_guide_v2.pdf",
        )
        assert chunk.id == "c1"
        assert chunk.metadata["product_id"] == "p1"
        assert chunk.score == 0.92

    def test_string_metadata_only(self) -> None:
        """Moss metadata is string-valued only (HLD 00 §3)."""
        chunk = Chunk(
            id="c1",
            text="...",
            metadata={"product_id": "p1", "page": "5"},
        )
        for k, v in chunk.metadata.items():
            assert isinstance(k, str)
            assert isinstance(v, str)


# ---------------------------------------------------------------------------
# RetrievalQuery
# ---------------------------------------------------------------------------

class TestRetrievalQuery:
    def test_minimal(self) -> None:
        q = RetrievalQuery(company_id="acme", text="paper jam fix")
        assert q.company_id == "acme"
        assert q.product_id is None
        assert q.need == []

    def test_with_product_scope(self) -> None:
        q = RetrievalQuery(
            company_id="acme",
            text="error E01",
            product_id="p1",
            need=["troubleshooting"],
        )
        assert q.product_id == "p1"
        assert q.need == ["troubleshooting"]


# ---------------------------------------------------------------------------
# Answer
# ---------------------------------------------------------------------------

class TestAnswer:
    def test_grounded_answer(self) -> None:
        a = Answer(
            text="Open the rear tray (§3.2).",
            citations=[
                {"doc": "user_guide_v2.pdf", "section": "§3.2", "score": 0.92},
            ],
        )
        assert len(a.citations) == 1
        assert a.citations[0]["doc"] == "user_guide_v2.pdf"

    def test_empty_answer(self) -> None:
        """Empty answer means retrieval returned nothing — agent should refuse."""
        a = Answer(text="", citations=[])
        assert a.text == ""
        assert a.citations == []


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

class TestCompany:
    def test_construction(self) -> None:
        c = Company(
            company_id="acme-123",
            name="Acme Corp",
            index_name="clutch-acme-123",
            api_key="sk_test_abc123",
        )
        assert c.index_name == "clutch-acme-123"
        assert c.catalog == []

    def test_with_catalog(self) -> None:
        entry = CatalogEntry(product_id="p1", name="Printer 3000")
        c = Company(
            company_id="acme-123",
            name="Acme Corp",
            index_name="clutch-acme-123",
            catalog=[entry],
            api_key="sk_test_abc123",
        )
        assert len(c.catalog) == 1
        assert c.catalog[0].name == "Printer 3000"


# ---------------------------------------------------------------------------
# SupportSession
# ---------------------------------------------------------------------------

class TestSupportSession:
    def test_defaults(self) -> None:
        s = SupportSession(
            session_id="sess-001",
            company_id="acme-123",
            modality="text",
        )
        assert s.product_id is None
        assert s.phase == "greeting"
        assert s.history == []

    def test_phase_mutation(self) -> None:
        s = SupportSession(
            session_id="sess-001",
            company_id="acme-123",
            modality="voice",
        )
        s.phase = "assisting"
        assert s.phase == "assisting"

    def test_modality_values(self) -> None:
        """Modality is a Literal — verify all valid values construct."""
        for m in ("text", "voice", "video"):
            s = SupportSession(session_id="s", company_id="c", modality=m)  # type: ignore[arg-type]
            assert s.modality == m
