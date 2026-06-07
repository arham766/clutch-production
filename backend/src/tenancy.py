"""
Clutch tenancy — company resolution + session scoping.

The embed snippet carries the company key (API key). This module resolves it
to a ``Company`` contract and stamps ``company_id`` on every session.
Strict isolation: unknown key → reject, never default to another tenant.

Reference: HLD 06 §3c (multi-tenancy), §4 (tenancy surface).
"""

from __future__ import annotations

import logging
import uuid

from src.contracts import Company, Modality, SupportSession
from src.db import build_catalog, get_company, get_company_by_api_key

logger = logging.getLogger(__name__)


def resolve_company(company_key: str) -> Company:
    """Resolve a company API key to a ``Company`` contract.

    Args:
        company_key: The ``data-clutch-key`` from the embed snippet.

    Returns:
        A ``Company`` contract with ``index_name``, ``catalog``, etc.

    Raises:
        ValueError: If the key is unknown or invalid.
    """
    data = get_company_by_api_key(company_key)
    if not data:
        raise ValueError(f"Unknown company key: {company_key!r}")

    company_id = data["company_id"]
    catalog = build_catalog(company_id)

    return Company(
        company_id=company_id,
        name=data["name"],
        index_name=data.get("index_name", f"clutch-{company_id}"),
        catalog=catalog,
        api_key=data["api_key"],
    )


def scope_session(
    company_key: str,
    modality: Modality = "text",
) -> SupportSession:
    """Create a new support session scoped to a company.

    Args:
        company_key: The embed snippet's API key.
        modality: Starting modality (``text``, ``voice``, ``video``).

    Returns:
        A ``SupportSession`` stamped with the company's ``company_id``.

    Raises:
        ValueError: If the key is unknown.
    """
    company = resolve_company(company_key)
    session_id = f"sess-{uuid.uuid4().hex[:12]}"

    session = SupportSession(
        session_id=session_id,
        company_id=company.company_id,
        modality=modality,
    )

    logger.info(
        "Scoped session %s to company %s (%s), modality=%s",
        session_id, company.company_id, company.name, modality,
    )
    return session
