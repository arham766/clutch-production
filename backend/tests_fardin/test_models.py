"""
Tests for src/models.py — Pydantic API request/response schemas.

Covers:
- Validation rules (min_length, pattern, required fields)
- Default values
- Rejection of invalid inputs
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import (
    BootstrapRequest,
    ConnectionDetailsRequest,
    CreateProductRequest,
    DocUploadItem,
    RegisterDocsRequest,
    StatusResponse,
)


class TestBootstrapRequest:
    def test_valid(self) -> None:
        r = BootstrapRequest(name="Acme Corp")
        assert r.name == "Acme Corp"

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BootstrapRequest(name="")


class TestCreateProductRequest:
    def test_minimal(self) -> None:
        r = CreateProductRequest(name="Printer")
        assert r.name == "Printer"
        assert r.brand == ""
        assert r.aliases == []

    def test_full(self) -> None:
        r = CreateProductRequest(name="Printer 3000", brand="AcmeCo", aliases=["P3K"])
        assert r.brand == "AcmeCo"
        assert r.aliases == ["P3K"]


class TestDocUploadItem:
    def test_valid_doc_types(self) -> None:
        for dt in ["user_guide", "service_manual", "spec_sheet", "troubleshooting", "other"]:
            d = DocUploadItem(doc_type=dt, storage_path="companies/c/p/f.pdf")
            assert d.doc_type == dt

    def test_invalid_doc_type(self) -> None:
        with pytest.raises(ValidationError):
            DocUploadItem(doc_type="random", storage_path="x/y.pdf")

    def test_empty_storage_path(self) -> None:
        with pytest.raises(ValidationError):
            DocUploadItem(doc_type="user_guide", storage_path="")


class TestRegisterDocsRequest:
    def test_valid(self) -> None:
        r = RegisterDocsRequest(
            docs=[DocUploadItem(doc_type="user_guide", storage_path="a/b.pdf")],
            photo_path="a/photo.jpg",
        )
        assert len(r.docs) == 1

    def test_empty_docs_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RegisterDocsRequest(docs=[])


class TestConnectionDetailsRequest:
    def test_valid(self) -> None:
        r = ConnectionDetailsRequest(company_key="sk_abc123")
        assert r.modality == "text"

    def test_all_modalities(self) -> None:
        for m in ["text", "voice", "video"]:
            r = ConnectionDetailsRequest(company_key="k", modality=m)
            assert r.modality == m

    def test_invalid_modality(self) -> None:
        with pytest.raises(ValidationError):
            ConnectionDetailsRequest(company_key="k", modality="teleport")

    def test_empty_key(self) -> None:
        with pytest.raises(ValidationError):
            ConnectionDetailsRequest(company_key="")


class TestStatusResponse:
    def test_defaults(self) -> None:
        r = StatusResponse(status="parsing")
        assert r.progress == ""
        assert r.message == ""
