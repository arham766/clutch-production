"""
Clutch API — Product routes.

CRUD for products within the authenticated company, plus doc registration
and the onboarding pipeline trigger.
"""

from __future__ import annotations

import logging
import uuid
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, UploadFile, File

from src.api.deps import get_auth
from src.auth import AuthContext
from src.db import (
    create_product,
    delete_product,
    get_product,
    list_docs,
    list_products,
    register_docs,
    update_product_details,
    update_product_status,
)
from src.models import (
    CreateProductRequest,
    CreateProductResponse,
    DocUploadItem,
    ProcessResponse,
    ProductListItem,
    ProductListResponse,
    RegisterDocsRequest,
    RegisterDocsResponse,
    StatusResponse,
    UpdateProductRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/products", tags=["products"])


# ---------------------------------------------------------------------------
# Onboarding runner registry (set by app.py wiring)
# ---------------------------------------------------------------------------

_onboard_fn = None


def set_onboard_fn(fn: object) -> None:
    """Register the onboarding pipeline function (called by app.py wiring)."""
    global _onboard_fn
    _onboard_fn = fn


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
_PRODUCTS_CACHE: dict[str, dict[str, Any]] = {}
CACHE_TTL = 30  # seconds

def invalidate_products_cache(company_id: str) -> None:
    """Invalidate the products cache for a company."""
    if company_id in _PRODUCTS_CACHE:
        del _PRODUCTS_CACHE[company_id]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

from fastapi import Response

@router.get("", response_model=ProductListResponse)
async def list_company_products(
    response: Response,
    auth: AuthContext = Depends(get_auth),
) -> ProductListResponse:
    """List all products for the authenticated company."""
    # We deliberately don't set Cache-Control here so the frontend can hit the fast in-memory cache instantly on upload
    
    # Check cache
    cached = _PRODUCTS_CACHE.get(auth.company_id)
    if cached and (time.time() - cached["time"] < CACHE_TTL):
        return cached["data"]

    from src.storage import get_download_url
    products = list_products(auth.company_id)
    items = []
    for p in products:
        docs = list_docs(auth.company_id, p["product_id"])
        
        photo_url = None
        if p.get("photo_path"):
            try:
                photo_url = get_download_url(p["photo_path"])
            except Exception:
                pass
                
        doc_list = []
        for d in docs:
            storage_path = d.get("storage_path", "")
            name = storage_path.split("/")[-1] if storage_path else "Document"
            doc_list.append({"id": d.get("doc_id", ""), "name": name, "size": "0"})
            
        items.append(
            ProductListItem(
                product_id=p["product_id"],
                name=p["name"],
                brand=p.get("brand", ""),
                status=p.get("status", "draft"),
                doc_count=len(docs),
                photo_url=photo_url,
                docs=doc_list
            )
        )
    
    response = ProductListResponse(products=items)
    _PRODUCTS_CACHE[auth.company_id] = {"data": response, "time": time.time()}
    return response


@router.post("", response_model=CreateProductResponse)
async def create_new_product(
    body: CreateProductRequest,
    auth: AuthContext = Depends(get_auth),
) -> CreateProductResponse:
    """Create a new product folder in the authenticated company."""
    data = create_product(
        company_id=auth.company_id,
        name=body.name,
        brand=body.brand,
        aliases=body.aliases,
    )
    logger.info(
        "Created product %s in company %s", data["product_id"], auth.company_id
    )
    invalidate_products_cache(auth.company_id)
    return CreateProductResponse(product_id=data["product_id"])


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    body: UpdateProductRequest,
    auth: AuthContext = Depends(get_auth),
) -> dict[str, str]:
    """Update a product's details."""
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    update_product_details(auth.company_id, product_id, body.name)
    invalidate_products_cache(auth.company_id)
    return {"status": "ok"}


@router.delete("/{product_id}")
async def delete_existing_product(
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> dict[str, str]:
    """Delete a product."""
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
    delete_product(auth.company_id, product_id)
    invalidate_products_cache(auth.company_id)
    return {"status": "ok"}


@router.post("/{product_id}/docs", response_model=RegisterDocsResponse)
async def register_product_docs(
    product_id: str,
    body: RegisterDocsRequest,
    auth: AuthContext = Depends(get_auth),
) -> RegisterDocsResponse:
    """Register uploaded doc files for a product.

    Files are uploaded directly to Firebase Storage by the console frontend.
    This endpoint records their paths + doc_types so the backend knows what
    to process.
    """
    # Verify product exists and belongs to this company
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    doc_dicts = [d.model_dump() for d in body.docs]
    doc_ids = register_docs(
        company_id=auth.company_id,
        product_id=product_id,
        docs=doc_dicts,
        photo_path=body.photo_path,
    )
    invalidate_products_cache(auth.company_id)
    return RegisterDocsResponse(doc_ids=doc_ids)


@router.post("/{product_id}/upload")
async def upload_product_file(
    product_id: str,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_auth),
) -> dict[str, str]:
    """Upload a file to R2 via the backend.
    
    Returns:
        The storage path of the uploaded file.
    """
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )
        
    from src.storage import upload_file, build_storage_path
    
    storage_path = build_storage_path(auth.company_id, product_id, file.filename or "file")
    data = await file.read()
    
    upload_file(storage_path, data, file.content_type or "application/octet-stream")
    invalidate_products_cache(auth.company_id)
    
    return {"storage_path": storage_path}


@router.post("/{product_id}/process", response_model=ProcessResponse)
async def process_product(
    product_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(get_auth),
) -> ProcessResponse:
    """Kick off the onboarding pipeline for a product (background).

    Parses each doc → chunks → indexes into Moss → builds catalog entry.
    Poll ``GET /api/products/{id}/status`` for progress.
    """
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    if product.get("status") in ("parsing", "indexing"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product is already being processed",
        )

    job_id = uuid.uuid4().hex[:12]

    if _onboard_fn is not None:
        background_tasks.add_task(_onboard_fn, auth.company_id, product_id)
    else:
        # No pipeline wired yet — just mark as ready for dev/testing
        logger.warning("No onboard_fn wired — marking product as ready (dev mode)")
        update_product_status(auth.company_id, product_id, "ready")

    invalidate_products_cache(auth.company_id)
    return ProcessResponse(job_id=job_id, message="Processing started")


@router.get("/{product_id}/status", response_model=StatusResponse)
async def get_product_status(
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> StatusResponse:
    """Get the processing status of a product."""
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    return StatusResponse(
        status=product.get("status", "draft"),
        progress=product.get("progress", ""),
        message=product.get("status_message", ""),
    )

@router.get("/{product_id}/outputs")
async def get_product_outputs(
    product_id: str,
    auth: AuthContext = Depends(get_auth),
) -> dict[str, Any]:
    """Get signed URLs for the actual pipeline outputs and original document.
    Used by the dashboard debug modal.
    """
    product = get_product(auth.company_id, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found",
        )

    from src.storage import get_download_url, build_storage_path, file_exists, download_file
    import json
    
    # 1. Get original doc URL (for iframe, CORS not needed)
    docs = list_docs(auth.company_id, product_id)
    pdf_url = None
    if docs:
        storage_path = docs[0].get("storage_path")
        if storage_path:
            try:
                pdf_url = get_download_url(storage_path)
            except Exception:
                pass
                
    # 2. Get Unsiloed JSON directly to avoid CORS
    unsiloed_data = None
    if docs:
        doc_id = docs[0].get("doc_id")
        unsiloed_path = build_storage_path(auth.company_id, product_id, f"debug_unsiloed_{doc_id}.json")
        if file_exists(unsiloed_path):
            try:
                raw = download_file(unsiloed_path)
                unsiloed_data = json.loads(raw)
            except Exception as e:
                logger.error("Failed to load unsiloed debug data: %s", e)

    # 3. Get Moss JSON directly to avoid CORS
    moss_data = None
    moss_path = build_storage_path(auth.company_id, product_id, "debug_moss.json")
    if file_exists(moss_path):
        try:
            raw = download_file(moss_path)
            moss_data = json.loads(raw)
        except Exception as e:
            logger.error("Failed to load moss debug data: %s", e)

    return {
        "pdf_url": pdf_url,
        "unsiloed_data": unsiloed_data,
        "moss_data": moss_data,
    }
