"""
Clutch onboarding — catalog entry builder.

Converts a console product (name, brand, aliases, photo) into a
``CatalogEntry`` contract for Perception (02) to consume.
"""

from __future__ import annotations

import logging
from typing import Any

from src.contracts import CatalogEntry

logger = logging.getLogger(__name__)


def build_catalog_entry(
    product: dict[str, Any],
    photo_bytes: bytes | None = None,
) -> CatalogEntry:
    """Build a CatalogEntry from product data + uploaded photo.

    Args:
        product: Product dict from Firestore (name, brand, aliases, etc.).
        photo_bytes: Raw bytes of the uploaded reference photo, or None.

    Returns:
        A ``CatalogEntry`` contract instance.
    """
    return CatalogEntry(
        product_id=product["product_id"],
        name=product["name"],
        brand=product.get("brand", ""),
        aliases=product.get("aliases", []),
        description=product.get("description", ""),
        ref_image=photo_bytes,
    )
