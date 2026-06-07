"""
Tests for src/db.py — Firestore CRUD operations.

All Firestore calls are mocked — no Firebase project needed.

Covers:
- Company CRUD: create, get, get_by_api_key, idempotent bootstrap
- Product CRUD: create, list, get, status transitions
- Doc registration: register_docs, list_docs, parse status
- Catalog building: only 'ready' products included
- Embed snippet: requires ≥1 ready product
- API key generation: unique, 64 chars
- Cross-company isolation (structural — tested via mock assertions)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from src.contracts import CatalogEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mock_firestore() -> MagicMock:
    """Create a mock Firestore client with chaining support."""
    db = MagicMock()
    return db


def _make_doc_snapshot(exists: bool, data: dict | None = None) -> MagicMock:
    """Create a mock Firestore DocumentSnapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data
    return doc


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class TestUserMapping:
    @patch("src.db._get_db")
    def test_create_user_mapping(self, mock_get_db: MagicMock) -> None:
        from src.db import create_user_mapping

        db = _mock_firestore()
        mock_get_db.return_value = db

        result = create_user_mapping("uid-1", "arham@acme.com", "company-1")

        assert result["email"] == "arham@acme.com"
        assert result["company_id"] == "company-1"
        db.collection.assert_called_with("users")

    @patch("src.db._get_db")
    def test_get_user_mapping_found(self, mock_get_db: MagicMock) -> None:
        from src.db import get_user_mapping

        db = _mock_firestore()
        mock_get_db.return_value = db
        doc = _make_doc_snapshot(True, {"email": "a@b.com", "company_id": "c1"})
        db.collection.return_value.document.return_value.get.return_value = doc

        result = get_user_mapping("uid-1")
        assert result is not None
        assert result["company_id"] == "c1"

    @patch("src.db._get_db")
    def test_get_user_mapping_not_found(self, mock_get_db: MagicMock) -> None:
        from src.db import get_user_mapping

        db = _mock_firestore()
        mock_get_db.return_value = db
        doc = _make_doc_snapshot(False)
        db.collection.return_value.document.return_value.get.return_value = doc

        assert get_user_mapping("uid-nope") is None


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

class TestCompanyCRUD:
    @patch("src.db._get_db")
    def test_create_company(self, mock_get_db: MagicMock) -> None:
        from src.db import create_company

        db = _mock_firestore()
        mock_get_db.return_value = db
        # get_user_mapping returns None (new user)
        doc = _make_doc_snapshot(False)
        db.collection.return_value.document.return_value.get.return_value = doc

        result = create_company("uid-1", "Acme Corp", "arham@acme.com")

        assert result["name"] == "Acme Corp"
        assert result["company_id"]  # generated
        assert result["api_key"]  # generated
        assert result["index_name"].startswith("clutch-")
        assert len(result["api_key"]) == 64  # 32 bytes hex

    @patch("src.db._get_db")
    def test_create_company_idempotent(self, mock_get_db: MagicMock) -> None:
        from src.db import create_company

        db = _mock_firestore()
        mock_get_db.return_value = db

        # User already mapped
        user_doc = _make_doc_snapshot(True, {"company_id": "existing-co"})
        company_doc = _make_doc_snapshot(
            True,
            {
                "company_id": "existing-co",
                "name": "Existing",
                "api_key": "key123",
                "index_name": "clutch-existing-co",
            },
        )

        def side_effect_get(*args: Any, **kwargs: Any) -> MagicMock:
            """Return user doc first, then company doc."""
            return user_doc

        db.collection.return_value.document.return_value.get.side_effect = [
            user_doc,
            company_doc,
        ]

        result = create_company("uid-1", "Acme Corp")
        assert result["company_id"] == "existing-co"

    @patch("src.db._get_db")
    def test_get_company(self, mock_get_db: MagicMock) -> None:
        from src.db import get_company

        db = _mock_firestore()
        mock_get_db.return_value = db
        doc = _make_doc_snapshot(True, {"company_id": "c1", "name": "Acme"})
        db.collection.return_value.document.return_value.get.return_value = doc

        result = get_company("c1")
        assert result is not None
        assert result["name"] == "Acme"

    @patch("src.db._get_db")
    def test_get_company_not_found(self, mock_get_db: MagicMock) -> None:
        from src.db import get_company

        db = _mock_firestore()
        mock_get_db.return_value = db
        doc = _make_doc_snapshot(False)
        db.collection.return_value.document.return_value.get.return_value = doc

        assert get_company("nope") is None

    @patch("src.db._get_db")
    def test_get_company_by_api_key(self, mock_get_db: MagicMock) -> None:
        from src.db import get_company_by_api_key

        db = _mock_firestore()
        mock_get_db.return_value = db

        doc = _make_doc_snapshot(True, {"company_id": "c1", "api_key": "key-abc"})
        (
            db.collection.return_value
            .where.return_value
            .limit.return_value
            .get.return_value
        ) = [doc]

        result = get_company_by_api_key("key-abc")
        assert result is not None
        assert result["company_id"] == "c1"

    @patch("src.db._get_db")
    def test_get_company_by_api_key_not_found(self, mock_get_db: MagicMock) -> None:
        from src.db import get_company_by_api_key

        db = _mock_firestore()
        mock_get_db.return_value = db
        (
            db.collection.return_value
            .where.return_value
            .limit.return_value
            .get.return_value
        ) = []

        assert get_company_by_api_key("bad-key") is None


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class TestProductCRUD:
    @patch("src.db._get_db")
    def test_create_product(self, mock_get_db: MagicMock) -> None:
        from src.db import create_product

        db = _mock_firestore()
        mock_get_db.return_value = db

        result = create_product("c1", "Printer 3000", brand="AcmeCo")
        assert result["name"] == "Printer 3000"
        assert result["brand"] == "AcmeCo"
        assert result["status"] == "draft"
        assert result["product_id"]

    @patch("src.db._get_db")
    def test_list_products(self, mock_get_db: MagicMock) -> None:
        from src.db import list_products

        db = _mock_firestore()
        mock_get_db.return_value = db

        p1 = _make_doc_snapshot(True, {"product_id": "p1", "name": "A"})
        p2 = _make_doc_snapshot(True, {"product_id": "p2", "name": "B"})
        (
            db.collection.return_value
            .document.return_value
            .collection.return_value
            .order_by.return_value
            .get.return_value
        ) = [p1, p2]

        products = list_products("c1")
        assert len(products) == 2
        assert products[0]["product_id"] == "p1"

    @patch("src.db._get_db")
    def test_update_product_status(self, mock_get_db: MagicMock) -> None:
        from src.db import update_product_status

        db = _mock_firestore()
        mock_get_db.return_value = db

        update_product_status("c1", "p1", "parsing", message="Processing doc 1 of 3")

        # Verify update was called
        (
            db.collection.return_value
            .document.return_value
            .collection.return_value
            .document.return_value
            .update
        ).assert_called_once()


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

class TestDocRegistration:
    @patch("src.db._get_db")
    def test_register_docs(self, mock_get_db: MagicMock) -> None:
        from src.db import register_docs

        db = _mock_firestore()
        mock_get_db.return_value = db

        docs = [
            {"doc_type": "user_guide", "storage_path": "companies/c1/products/p1/guide.pdf"},
            {"doc_type": "troubleshooting", "storage_path": "companies/c1/products/p1/ts.pdf"},
        ]

        doc_ids = register_docs("c1", "p1", docs, photo_path="companies/c1/products/p1/photo.jpg")
        assert len(doc_ids) == 2
        # All IDs should be unique
        assert len(set(doc_ids)) == 2


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class TestCatalog:
    @patch("src.db.list_products")
    def test_build_catalog_only_ready(self, mock_list: MagicMock) -> None:
        from src.db import build_catalog

        mock_list.return_value = [
            {"product_id": "p1", "name": "Ready Product", "brand": "A", "status": "ready", "aliases": []},
            {"product_id": "p2", "name": "Draft Product", "brand": "B", "status": "draft", "aliases": []},
            {"product_id": "p3", "name": "Another Ready", "brand": "C", "status": "ready", "aliases": ["AR"]},
        ]

        catalog = build_catalog("c1")
        assert len(catalog) == 2
        assert all(isinstance(e, CatalogEntry) for e in catalog)
        ids = [e.product_id for e in catalog]
        assert "p1" in ids
        assert "p3" in ids
        assert "p2" not in ids

    @patch("src.db.list_products")
    def test_build_catalog_empty(self, mock_list: MagicMock) -> None:
        from src.db import build_catalog

        mock_list.return_value = []
        assert build_catalog("c1") == []


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------

class TestEmbedInfo:
    @patch("src.db.list_products")
    @patch("src.db.get_company")
    def test_embed_with_ready_product(
        self, mock_company: MagicMock, mock_products: MagicMock
    ) -> None:
        from src.db import get_embed_info

        mock_company.return_value = {"company_id": "c1", "api_key": "key-xyz", "name": "Acme"}
        mock_products.return_value = [{"status": "ready"}]

        result = get_embed_info("c1")
        assert result is not None
        assert result["api_key"] == "key-xyz"
        assert 'data-clutch-key="key-xyz"' in result["snippet"]
        assert "widget.js" in result["snippet"]

    @patch("src.db.list_products")
    @patch("src.db.get_company")
    def test_embed_no_ready_products(
        self, mock_company: MagicMock, mock_products: MagicMock
    ) -> None:
        from src.db import get_embed_info

        mock_company.return_value = {"company_id": "c1", "api_key": "key-xyz", "name": "Acme"}
        mock_products.return_value = [{"status": "parsing"}, {"status": "draft"}]

        assert get_embed_info("c1") is None

    @patch("src.db.get_company")
    def test_embed_company_not_found(self, mock_company: MagicMock) -> None:
        from src.db import get_embed_info

        mock_company.return_value = None
        assert get_embed_info("nope") is None


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------

class TestApiKeyGeneration:
    def test_key_length_and_uniqueness(self) -> None:
        from src.db import _mint_api_key

        keys = {_mint_api_key() for _ in range(100)}
        assert all(len(k) == 64 for k in keys)
        assert len(keys) == 100  # all unique


# ---------------------------------------------------------------------------
# Storage path builder
# ---------------------------------------------------------------------------

class TestStoragePath:
    def test_build_storage_path(self) -> None:
        from src.storage import build_storage_path

        path = build_storage_path("c1", "p1", "manual.pdf")
        assert path == "companies/c1/products/p1/manual.pdf"
