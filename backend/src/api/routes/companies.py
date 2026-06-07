"""
Clutch API — Company routes.

POST /api/companies/bootstrap — first-login tenant creation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from src.auth import verify_token
from src.db import create_company, get_company
from src.models import BootstrapRequest, BootstrapResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["companies"])

async def get_uid_only(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    return verify_token(authorization)

@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_company(
    body: BootstrapRequest,
    uid: str = Depends(get_uid_only),
) -> BootstrapResponse:
    """First-login: create a company, mint API key, set index name.

    Idempotent — if the user already has a company, returns the existing one.
    """
    data = create_company(uid=uid, name=body.name, email="")
    logger.info("Bootstrap company=%s for uid=%s", data["company_id"], uid)
    return BootstrapResponse(company_id=data["company_id"], name=data["name"])
