"""
Clutch API models — Pydantic request/response schemas.

Separate from ``contracts.py`` (the Spine). These are API-facing shapes:
validation, serialization, and OpenAPI docs. Internal code passes contracts;
the API layer converts at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

class BootstrapRequest(BaseModel):
    """POST /api/companies/bootstrap"""
    name: str = Field(..., min_length=1, max_length=200, description="Company display name")


class BootstrapResponse(BaseModel):
    """Response from bootstrap — confirms tenant created."""
    company_id: str
    name: str


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class CreateProductRequest(BaseModel):
    """POST /api/products"""
    name: str = Field(..., min_length=1, max_length=200)
    brand: str = Field(default="", max_length=200)
    aliases: list[str] = Field(default_factory=list)


class UpdateProductRequest(BaseModel):
    """PUT /api/products/{id}"""
    name: str = Field(..., min_length=1, max_length=200)


class CreateProductResponse(BaseModel):
    product_id: str


class ProductListItem(BaseModel):
    """One product in the list response."""
    product_id: str
    name: str
    brand: str = ""
    status: str = "draft"
    doc_count: int = 0
    photo_url: str | None = None
    docs: list[dict[str, str]] = Field(default_factory=list)


class ProductListResponse(BaseModel):
    products: list[ProductListItem]


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------

class DocUploadItem(BaseModel):
    """One document entry in a /docs registration request."""
    doc_id: str | None = None
    doc_type: str = Field(
        ...,
        pattern=r"^(user_guide|service_manual|spec_sheet|troubleshooting|other)$",
        description="Document type",
    )
    storage_path: str = Field(..., min_length=1)


class RegisterDocsRequest(BaseModel):
    """POST /api/products/{id}/docs"""
    docs: list[DocUploadItem] = Field(..., min_length=1)
    photo_path: str = Field(default="", description="Storage path to reference photo")


class RegisterDocsResponse(BaseModel):
    doc_ids: list[str]


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

class ProcessResponse(BaseModel):
    """POST /api/products/{id}/process"""
    job_id: str
    message: str = "Processing started"


class StatusResponse(BaseModel):
    """GET /api/products/{id}/status"""
    status: str
    progress: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------

class EmbedResponse(BaseModel):
    """GET /api/embed"""
    api_key: str
    snippet: str


# ---------------------------------------------------------------------------
# Widget / Connection
# ---------------------------------------------------------------------------

class ConnectionDetailsRequest(BaseModel):
    """POST /connection-details"""
    company_key: str = Field(..., min_length=1)
    modality: str = Field(default="text", pattern=r"^(text|voice|video)$")


class ConnectionDetailsResponse(BaseModel):
    token: str
    url: str


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    """Standard error shape."""
    detail: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
