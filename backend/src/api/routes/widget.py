"""
Clutch API — Widget bootstrap route.

POST /connection-details — exchange a company key for a LiveKit token.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, status

from src.db import get_company_by_api_key
from src.models import ConnectionDetailsRequest, ConnectionDetailsResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["widget"])

# ---------------------------------------------------------------------------
# LiveKit token minter (set by app.py wiring)
# ---------------------------------------------------------------------------

_livekit_minter = None


def set_livekit_minter(fn: object) -> None:
    """Register the LiveKit token minting function."""
    global _livekit_minter
    _livekit_minter = fn


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post("/connection-details", response_model=ConnectionDetailsResponse)
async def get_connection_details(
    body: ConnectionDetailsRequest,
) -> ConnectionDetailsResponse:
    """Widget bootstrap: exchange a company key for a scoped LiveKit token.

    This is the **only** unauthenticated route — it uses the embed snippet's
    ``data-clutch-key`` (API key) instead of a Firebase token.
    """
    company = get_company_by_api_key(body.company_key)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid company key",
        )

    company_id = company["company_id"]

    if _livekit_minter is not None:
        try:
            result = _livekit_minter(company_id, body.modality)
            return ConnectionDetailsResponse(
                token=result["token"], url=result["url"]
            )
        except Exception:
            logger.exception("LiveKit token minting failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create session",
            )

    # Dev fallback: return a placeholder token
    logger.warning("No LiveKit minter wired — returning dev placeholder")
    session_id = f"sess-{company_id}-{int(time.time())}"
    return ConnectionDetailsResponse(
        token=f"dev-token-{session_id}",
        url="wss://localhost:7880",
    )


class ChatRequest(BaseModel):
    company_key: str
    query: str

class ChatResponse(BaseModel):
    answer: str
    sources: list[str]

@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """Widget fallback: a text-based chat endpoint hitting Moss directly.
    """
    company = get_company_by_api_key(body.company_key)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid company key",
        )
    
    from src.api.deps import get_config
    from moss import MossClient, QueryOptions
    cfg = get_config()
    
    index_name = f"clutch-{company['company_id']}"
    
    try:
        client = MossClient(cfg.moss_project_id, cfg.moss_api_key)
        await client.load_index(index_name)
        
        results = await client.query(index_name, body.query, QueryOptions(top_k=3))
        
        if not results.docs:
            return ChatResponse(
                answer="I couldn't find an answer in your uploaded manuals.",
                sources=[]
            )
            
        answer = "Based on your manuals, here is the relevant information:\n\n"
        sources = []
        for doc in results.docs:
            answer += f"**Excerpt:** {doc.text}\n\n"
            source_name = doc.metadata.get("source", "Unknown Document")
            if source_name not in sources:
                sources.append(source_name)
                
        return ChatResponse(answer=answer, sources=sources)
        
    except Exception as e:
        logger.exception("Chat query failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query Moss: {e}",
        )
