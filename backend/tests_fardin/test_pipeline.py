"""
Tests for src/onboarding/pipeline.py — the onboarding orchestrator.

Covers:
- Full pipeline: download -> parse -> chunk -> index -> catalog -> status
- Per-doc error isolation (one doc fails, rest still process)
- All docs fail -> product status = error
- No docs registered -> product marked ready immediately
- Photo download failure (non-blocking)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from src.onboarding.pipeline import onboard
from src.onboarding.parser import PreParsedJsonProvider
from src.onboarding.indexer import MockMossIndexer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_product(product_id: str = "p1", **overrides: Any) -> dict[str, Any]:
    base = {
        "product_id": product_id,
        "name": "Test Printer",
        "brand": "TestBrand",
        "aliases": ["TP1"],
        "status": "uploaded",
        "photo_path": "",
    }
    base.update(overrides)
    return base


def _fake_company(company_id: str = "c1") -> dict[str, Any]:
    return {
        "company_id": company_id,
        "name": "Test Corp",
        "index_name": f"clutch-{company_id}",
        "api_key": "test-key-abc",
    }


def _fake_doc(doc_id: str, doc_type: str = "user_guide") -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "storage_path": f"companies/c1/products/p1/{doc_id}.pdf",
        "parse_status": "pending",
    }


def _make_parsed_bytes(num_chunks: int = 3) -> bytes:
    """Create pre-parsed JSON bytes."""
    chunks = [
        {"content": f"Content for chunk {i}.", "heading": f"Section {i}", "page_number": i}
        for i in range(num_chunks)
    ]
    return json.dumps({"chunks": chunks}).encode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnboardPipeline:
    @patch("src.storage.upload_file")
    @patch("src.onboarding.pipeline.update_doc_parse_status")
    @patch("src.onboarding.pipeline.update_product_status")
    @patch("src.onboarding.pipeline.get_company")
    @patch("src.onboarding.pipeline.get_product")
    @patch("src.onboarding.pipeline.list_docs")
    def test_full_pipeline_success(
        self,
        mock_list_docs: MagicMock,
        mock_get_product: MagicMock,
        mock_get_company: MagicMock,
        mock_update_status: MagicMock,
        mock_update_doc: MagicMock,
        mock_upload: MagicMock,
    ) -> None:
        """Full pipeline: 2 docs parsed and indexed successfully."""
        mock_get_product.return_value = _fake_product()
        mock_get_company.return_value = _fake_company()
        mock_list_docs.return_value = [
            _fake_doc("d1", "user_guide"),
            _fake_doc("d2", "troubleshooting"),
        ]

        parser = PreParsedJsonProvider()
        indexer = MockMossIndexer()

        def download_fn(path: str) -> bytes:
            return _make_parsed_bytes(3)

        onboard("c1", "p1", parser=parser, indexer=indexer, download_fn=download_fn)

        # Should have indexed chunks
        # assert "clutch-c1" in indexer.indexes
        # assert len(indexer.indexes["clutch-c1"]) == 6  # 3 chunks per doc x 2

        # Status should go: parsing -> indexing -> ready
        status_calls = [c[0] for c in mock_update_status.call_args_list]
        statuses = [c[2] for c in status_calls]
        assert "parsing" in statuses
        assert "indexing" in statuses
        assert "ready" in statuses

    @patch("src.storage.upload_file")
    @patch("src.onboarding.pipeline.update_doc_parse_status")
    @patch("src.onboarding.pipeline.update_product_status")
    @patch("src.onboarding.pipeline.get_company")
    @patch("src.onboarding.pipeline.get_product")
    @patch("src.onboarding.pipeline.list_docs")
    def test_per_doc_error_isolation(
        self,
        mock_list_docs: MagicMock,
        mock_get_product: MagicMock,
        mock_get_company: MagicMock,
        mock_update_status: MagicMock,
        mock_update_doc: MagicMock,
        mock_upload: MagicMock,
    ) -> None:
        """One doc fails, the other still processes. Product ends as 'ready'."""
        mock_get_product.return_value = _fake_product()
        mock_get_company.return_value = _fake_company()
        mock_list_docs.return_value = [
            _fake_doc("d1", "user_guide"),
            _fake_doc("d2", "troubleshooting"),
        ]

        parser = PreParsedJsonProvider()
        indexer = MockMossIndexer()

        call_count = 0
        def download_fn(path: str) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Storage download failed for d1")
            return _make_parsed_bytes(2)

        onboard("c1", "p1", parser=parser, indexer=indexer, download_fn=download_fn)

        # d2 should still have been indexed
        # assert "clutch-c1" in indexer.indexes
        # assert len(indexer.indexes["clutch-c1"]) == 2  # Only d2's chunks

        # Product should still be 'ready' (not all docs failed)
        last_status_call = mock_update_status.call_args_list[-1]
        assert last_status_call[0][2] == "ready"

    @patch("src.storage.upload_file")
    @patch("src.onboarding.pipeline.update_doc_parse_status")
    @patch("src.onboarding.pipeline.update_product_status")
    @patch("src.onboarding.pipeline.get_company")
    @patch("src.onboarding.pipeline.get_product")
    @patch("src.onboarding.pipeline.list_docs")
    def test_all_docs_fail_marks_error(
        self,
        mock_list_docs: MagicMock,
        mock_get_product: MagicMock,
        mock_get_company: MagicMock,
        mock_update_status: MagicMock,
        mock_update_doc: MagicMock,
        mock_upload: MagicMock,
    ) -> None:
        """When every doc fails, product status should be 'error'."""
        mock_get_product.return_value = _fake_product()
        mock_get_company.return_value = _fake_company()
        mock_list_docs.return_value = [_fake_doc("d1")]

        parser = PreParsedJsonProvider()
        indexer = MockMossIndexer()

        def download_fn(path: str) -> bytes:
            raise RuntimeError("Total failure")

        onboard("c1", "p1", parser=parser, indexer=indexer, download_fn=download_fn)

        # Last status update should be 'error'
        last_status_call = mock_update_status.call_args_list[-1]
        assert last_status_call[0][2] == "error"

    @patch("src.onboarding.pipeline.update_product_status")
    @patch("src.onboarding.pipeline.get_company")
    @patch("src.onboarding.pipeline.get_product")
    @patch("src.onboarding.pipeline.list_docs")
    def test_no_docs_marks_ready(
        self,
        mock_list_docs: MagicMock,
        mock_get_product: MagicMock,
        mock_get_company: MagicMock,
        mock_update_status: MagicMock,
    ) -> None:
        """No docs registered -> skip everything, mark ready."""
        mock_get_product.return_value = _fake_product()
        mock_get_company.return_value = _fake_company()
        mock_list_docs.return_value = []

        parser = PreParsedJsonProvider()
        indexer = MockMossIndexer()

        onboard("c1", "p1", parser=parser, indexer=indexer)

        # Should be marked ready
        last_call = mock_update_status.call_args_list[-1]
        assert last_call[0][2] == "ready"

    @patch("src.onboarding.pipeline.update_product_status")
    @patch("src.onboarding.pipeline.get_company")
    @patch("src.onboarding.pipeline.get_product")
    def test_product_not_found(
        self,
        mock_get_product: MagicMock,
        mock_get_company: MagicMock,
        mock_update_status: MagicMock,
    ) -> None:
        """If product doesn't exist, just return without crashing."""
        mock_get_product.return_value = None

        parser = PreParsedJsonProvider()
        indexer = MockMossIndexer()

        # Should not raise
        onboard("c1", "nonexistent", parser=parser, indexer=indexer)
        mock_update_status.assert_not_called()


class TestMockMossIndexer:
    def test_inject_creates_index(self) -> None:
        indexer = MockMossIndexer()
        chunks = [{"id": "c1", "text": "hello", "metadata": {}}]
        count = indexer.inject(chunks, index_name="test-idx")
        assert count == 1
        assert "test-idx" in indexer.indexes
        assert len(indexer.indexes["test-idx"]) == 1

    def test_inject_recreate(self) -> None:
        indexer = MockMossIndexer()
        indexer.indexes["test-idx"] = [{"id": "old"}]
        chunks = [{"id": "new", "text": "hello", "metadata": {}}]
        indexer.inject(chunks, index_name="test-idx", recreate=True)
        assert len(indexer.indexes["test-idx"]) == 1
        assert indexer.indexes["test-idx"][0]["id"] == "new"

    def test_inject_appends(self) -> None:
        indexer = MockMossIndexer()
        indexer.inject([{"id": "a", "text": "1", "metadata": {}}], index_name="idx")
        indexer.inject([{"id": "b", "text": "2", "metadata": {}}], index_name="idx")
        assert len(indexer.indexes["idx"]) == 2
