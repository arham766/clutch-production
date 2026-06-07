"""
Clutch API — Embed snippet route.

GET /api/embed — returns the embed snippet + API key once ≥1 product is ready.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, Response
import time

from src.api.deps import get_auth
from src.auth import AuthContext
from src.db import get_embed_info
from src.models import EmbedResponse

router = APIRouter(prefix="/api", tags=["embed"])

_EMBED_CACHE = {}
CACHE_TTL = 300  # 5 minutes

@router.get("/embed", response_model=EmbedResponse)
async def get_embed_snippet(
    response: Response,
    auth: AuthContext = Depends(get_auth),
) -> EmbedResponse:
    """Return the embed snippet for the authenticated company.

    Requires at least one product with status ``ready``.
    """
    response.headers["Cache-Control"] = f"public, max-age={CACHE_TTL}"
    
    cached = _EMBED_CACHE.get(auth.company_id)
    if cached and (time.time() - cached["time"] < CACHE_TTL):
        return cached["data"]
        
    info = get_embed_info(auth.company_id)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No products are ready yet. Process at least one product first.",
        )
    
    result = EmbedResponse(api_key=info["api_key"], snippet=info["snippet"])
    _EMBED_CACHE[auth.company_id] = {"data": result, "time": time.time()}
    return result
