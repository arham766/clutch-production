"""
Clutch Firestore database layer — typed CRUD for companies, products, docs.

This is the single source of truth for tenant data. Every operation is scoped
to a ``company_id`` — cross-company access is structurally impossible.

Firestore schema (HLD 12 §2b):
    users/{uid}                                 = { email, company_id }
    companies/{company_id}                      = { name, owner_uid, api_key, index_name, created_at }
    companies/{cid}/products/{pid}              = { name, brand, aliases, photo_path, status, created_at }
    companies/{cid}/products/{pid}/docs/{did}   = { doc_type, storage_path, parse_status }
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from firebase_admin import firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot

from src.contracts import CatalogEntry, Company, ProductStatus, DocType, ParseStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a short, URL-safe unique ID."""
    return uuid.uuid4().hex[:16]


def _mint_api_key() -> str:
    """Generate a cryptographically secure API key (32-byte hex = 64 chars)."""
    return secrets.token_hex(32)


def _get_db() -> Any:
    """Get the Firestore client (requires firebase_admin to be initialised)."""
    return firestore.client()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user_mapping(uid: str, email: str, company_id: str) -> dict[str, str]:
    """Create the ``users/{uid}`` → ``company_id`` mapping."""
    db = _get_db()
    data = {"email": email, "company_id": company_id, "created_at": _utcnow()}
    db.collection("users").document(uid).set(data)
    logger.info("Created user mapping uid=%s → company_id=%s", uid, company_id)
    return data


def get_user_mapping(uid: str) -> dict[str, str] | None:
    """Look up the user mapping. Returns ``None`` if not found."""
    db = _get_db()
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        return None
    return doc.to_dict()


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

def create_company(uid: str, name: str, email: str = "") -> dict[str, Any]:
    """Bootstrap a new company + link the owner user.

    Idempotent: if the user already has a company, returns the existing one.

    Returns:
        Dict with ``company_id``, ``name``, ``api_key``, ``index_name``.
    """
    # Check if user already has a company
    existing = get_user_mapping(uid)
    if existing and existing.get("company_id"):
        company = get_company(existing["company_id"])
        if company:
            return company

    db = _get_db()
    company_id = _new_id()
    api_key = _mint_api_key()
    index_name = f"clutch-{company_id}"

    data: dict[str, Any] = {
        "company_id": company_id,
        "name": name,
        "owner_uid": uid,
        "api_key": api_key,
        "index_name": index_name,
        "created_at": _utcnow(),
    }

    db.collection("companies").document(company_id).set(data)
    create_user_mapping(uid, email, company_id)

    logger.info("Created company %s (%s) for uid=%s", company_id, name, uid)
    return data


def get_company(company_id: str) -> dict[str, Any] | None:
    """Fetch a company record by ID."""
    db = _get_db()
    doc = db.collection("companies").document(company_id).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def get_company_by_api_key(api_key: str) -> dict[str, Any] | None:
    """Look up a company by its embed-snippet API key.

    Used by the widget bootstrap (``/connection-details``).
    """
    from google.cloud.firestore_v1.base_query import FieldFilter
    db = _get_db()
    results = (
        db.collection("companies")
        .where(filter=FieldFilter("api_key", "==", api_key))
        .limit(1)
        .get()
    )
    for doc in results:
        return doc.to_dict()
    return None


def get_company_as_contract(company_id: str) -> Company | None:
    """Fetch a company and return it as a ``Company`` contract."""
    data = get_company(company_id)
    if not data:
        return None

    # Build catalog from products
    catalog = build_catalog(company_id)

    return Company(
        company_id=data["company_id"],
        name=data["name"],
        index_name=data["index_name"],
        catalog=catalog,
        api_key=data["api_key"],
    )


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def create_product(
    company_id: str,
    name: str,
    brand: str = "",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new product folder within a company."""
    db = _get_db()
    product_id = _new_id()
    data: dict[str, Any] = {
        "product_id": product_id,
        "name": name,
        "brand": brand,
        "aliases": aliases or [],
        "photo_path": "",
        "status": "draft",
        "created_at": _utcnow(),
    }

    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .set(data)
    )
    logger.info("Created product %s (%s) in company %s", product_id, name, company_id)
    return data


def list_products(company_id: str) -> list[dict[str, Any]]:
    """List all products for a company."""
    db = _get_db()
    docs = (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .order_by("created_at")
        .get()
    )
    return [doc.to_dict() for doc in docs]


def get_product(company_id: str, product_id: str) -> dict[str, Any] | None:
    """Fetch a single product. Returns ``None`` if not found."""
    db = _get_db()
    doc = (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .get()
    )
    if not doc.exists:
        return None
    return doc.to_dict()


def update_product_details(company_id: str, product_id: str, name: str) -> None:
    """Update a product's name."""
    db = _get_db()
    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .update({"name": name, "updated_at": _utcnow()})
    )


def delete_product(company_id: str, product_id: str) -> None:
    """Delete a product."""
    db = _get_db()
    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .delete()
    )


def update_product_status(
    company_id: str,
    product_id: str,
    status: ProductStatus,
    message: str = "",
) -> None:
    """Update the processing status of a product."""
    db = _get_db()
    update: dict[str, Any] = {"status": status, "updated_at": _utcnow()}
    if message:
        update["status_message"] = message

    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .update(update)
    )
    logger.info("Product %s/%s status → %s", company_id, product_id, status)


def update_product_photo(
    company_id: str,
    product_id: str,
    photo_path: str,
) -> None:
    """Set the reference-photo storage path for a product."""
    db = _get_db()
    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .update({"photo_path": photo_path, "updated_at": _utcnow()})
    )


# ---------------------------------------------------------------------------
# Docs (per product)
# ---------------------------------------------------------------------------

def register_docs(
    company_id: str,
    product_id: str,
    docs: list[dict[str, str]],
    photo_path: str = "",
) -> list[str]:
    """Register uploaded doc files for a product.

    Each doc dict must have: ``doc_type``, ``storage_path``.
    Returns the list of generated ``doc_id``s.

    Also updates the product's ``photo_path`` if provided and sets
    status to ``uploaded``.
    """
    db = _get_db()
    doc_ids: list[str] = []

    for doc_entry in docs:
        doc_id = doc_entry.get("doc_id") or _new_id()
        data: dict[str, Any] = {
            "doc_id": doc_id,
            "doc_type": doc_entry["doc_type"],
            "storage_path": doc_entry["storage_path"],
            "parse_status": "pending",
            "created_at": _utcnow(),
        }
        (
            db.collection("companies")
            .document(company_id)
            .collection("products")
            .document(product_id)
            .collection("docs")
            .document(doc_id)
            .set(data)
        )
        doc_ids.append(doc_id)

    # Update photo if provided
    if photo_path:
        update_product_photo(company_id, product_id, photo_path)

    # Advance status to 'uploaded'
    update_product_status(company_id, product_id, "uploaded")

    logger.info(
        "Registered %d docs for product %s/%s", len(docs), company_id, product_id
    )
    return doc_ids


def list_docs(company_id: str, product_id: str) -> list[dict[str, Any]]:
    """List all registered docs for a product."""
    db = _get_db()
    docs = (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .collection("docs")
        .get()
    )
    return [doc.to_dict() for doc in docs]


def update_doc_parse_status(
    company_id: str,
    product_id: str,
    doc_id: str,
    parse_status: ParseStatus,
) -> None:
    """Update the parse status of a single doc."""
    db = _get_db()
    (
        db.collection("companies")
        .document(company_id)
        .collection("products")
        .document(product_id)
        .collection("docs")
        .document(doc_id)
        .update({"parse_status": parse_status, "updated_at": _utcnow()})
    )


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------

def build_catalog(company_id: str) -> list[CatalogEntry]:
    """Build the product catalog for a company from Firestore data.

    Only includes products with status ``ready``.
    """
    products = list_products(company_id)
    catalog: list[CatalogEntry] = []

    for p in products:
        if p.get("status") != "ready":
            continue
        catalog.append(
            CatalogEntry(
                product_id=p["product_id"],
                name=p["name"],
                brand=p.get("brand", ""),
                aliases=p.get("aliases", []),
                description=p.get("description", ""),
                ref_image=None,  # loaded separately from Storage when needed
            )
        )

    return catalog


# ---------------------------------------------------------------------------
# Embed snippet
# ---------------------------------------------------------------------------

def get_embed_info(company_id: str) -> dict[str, str] | None:
    """Return the embed snippet info if at least one product is ready.

    Returns:
        Dict with ``api_key`` and ``snippet``, or ``None`` if no products are ready.
    """
    company = get_company(company_id)
    if not company:
        return None

    products = list_products(company_id)
    has_ready = any(p.get("status") == "ready" for p in products)
    if not has_ready:
        return None

    api_key = company["api_key"]
    snippet = (
        f'<script src="https://cdn.clutch.ai/widget.js" '
        f'data-clutch-key="{api_key}"></script>'
    )
    return {"api_key": api_key, "snippet": snippet}
