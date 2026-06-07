"""
Tests for src/onboarding/chunker.py — heading-aware text chunking.

Covers:
- Single segment -> single chunk
- Multi-segment -> heading-prepended chunks
- Long text splitting with overlap
- Deterministic chunk IDs
- Empty result handling
- Metadata propagation
"""

from __future__ import annotations

from src.onboarding.chunker import chunk_result, _split_text, _make_id


class TestChunkResult:
    def test_single_segment(self) -> None:
        result = {
            "chunks": [
                {"content": "This is some text.", "heading": "Introduction", "page_number": 1}
            ]
        }
        chunks = chunk_result(result, source="guide.pdf")
        assert len(chunks) == 1
        assert "## Introduction" in chunks[0]["text"]
        assert "This is some text." in chunks[0]["text"]
        assert chunks[0]["metadata"]["source"] == "guide.pdf"
        assert chunks[0]["metadata"]["section"] == "Introduction"
        assert chunks[0]["metadata"]["page"] == "1"

    def test_multi_segment(self) -> None:
        result = {
            "chunks": [
                {"content": "First paragraph.", "heading": "Setup", "page_number": 1},
                {"content": "Second paragraph.", "heading": "Usage", "page_number": 2},
            ]
        }
        chunks = chunk_result(result, source="manual.pdf")
        assert len(chunks) == 2
        assert "## Setup" in chunks[0]["text"]
        assert "## Usage" in chunks[1]["text"]

    def test_extra_metadata_propagated(self) -> None:
        result = {"chunks": [{"content": "Text.", "heading": "H1", "page_number": 1}]}
        meta = {"product_id": "p1", "doc_id": "d1", "doc_type": "user_guide"}
        chunks = chunk_result(result, source="guide.pdf", extra_metadata=meta)

        assert chunks[0]["metadata"]["product_id"] == "p1"
        assert chunks[0]["metadata"]["doc_id"] == "d1"
        assert chunks[0]["metadata"]["doc_type"] == "user_guide"

    def test_empty_result(self) -> None:
        result = {"chunks": []}
        chunks = chunk_result(result, source="empty.pdf")
        assert chunks == []

    def test_no_chunks_key(self) -> None:
        result = {}
        chunks = chunk_result(result, source="missing.pdf")
        assert chunks == []

    def test_heading_carryover(self) -> None:
        """If a segment has no heading, the previous heading carries over."""
        result = {
            "chunks": [
                {"content": "First.", "heading": "Overview", "page_number": 1},
                {"content": "Second.", "heading": "", "page_number": 1},
            ]
        }
        chunks = chunk_result(result, source="doc.pdf")
        assert len(chunks) == 2
        assert chunks[1]["metadata"]["section"] == "Overview"

    def test_whitespace_only_segments_skipped(self) -> None:
        result = {
            "chunks": [
                {"content": "   ", "heading": "Empty", "page_number": 1},
                {"content": "Real content.", "heading": "Real", "page_number": 2},
            ]
        }
        chunks = chunk_result(result, source="doc.pdf")
        assert len(chunks) == 1
        assert "Real content" in chunks[0]["text"]

    def test_segments_key_alternative(self) -> None:
        """Supports 'segments' as an alternative to 'chunks'."""
        result = {
            "segments": [
                {"content": "Via segments key.", "heading": "H1", "page_number": 1}
            ]
        }
        chunks = chunk_result(result, source="doc.pdf")
        assert len(chunks) == 1

    def test_markdown_field_as_content(self) -> None:
        """Supports 'markdown' or 'text' as alternative content fields."""
        result = {
            "chunks": [
                {"markdown": "Markdown content.", "heading": "H1", "page_number": 1}
            ]
        }
        chunks = chunk_result(result, source="doc.pdf")
        assert len(chunks) == 1
        assert "Markdown content" in chunks[0]["text"]


class TestSplitText:
    def test_short_text_no_split(self) -> None:
        parts = _split_text("short text", 1500, 200)
        assert len(parts) == 1
        assert parts[0] == "short text"

    def test_long_text_splits(self) -> None:
        text = "A" * 3000
        parts = _split_text(text, 1500, 200)
        assert len(parts) >= 2
        # Each part should be <= max_size
        for part in parts:
            assert len(part) <= 1500

    def test_paragraph_break_preferred(self) -> None:
        """Should prefer splitting at paragraph boundaries."""
        text = ("word " * 200) + "\n\n" + ("word " * 200)
        parts = _split_text(text, 1200, 100)
        assert len(parts) >= 2


class TestMakeId:
    def test_deterministic(self) -> None:
        id1 = _make_id("doc.pdf", "Setup", "1", 0)
        id2 = _make_id("doc.pdf", "Setup", "1", 0)
        assert id1 == id2

    def test_different_inputs(self) -> None:
        id1 = _make_id("doc.pdf", "Setup", "1", 0)
        id2 = _make_id("doc.pdf", "Usage", "2", 0)
        assert id1 != id2

    def test_length(self) -> None:
        chunk_id = _make_id("doc.pdf", "section", "1", 0)
        assert len(chunk_id) == 16
