"""
Clutch API — Embed snippet route.

GET /api/embed — returns the embed snippet + API key once ≥1 product is ready.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_auth
from src.auth import AuthContext
from src.db import get_embed_info
from src.models import EmbedResponse

router = APIRouter(prefix="/api", tags=["embed"])


@router.get("/embed", response_model=EmbedResponse)
async def get_embed_snippet(
    auth: AuthContext = Depends(get_auth),
) -> EmbedResponse:
    """Return the embed snippet for the authenticated company.

    Requires at least one product with status ``ready``.
    """
    info = get_embed_info(auth.company_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No products are ready yet. Process at least one product first.",
        )
    return EmbedResponse(api_key=info["api_key"], snippet=info["snippet"])
