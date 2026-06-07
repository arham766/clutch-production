"""
Clutch onboarding — the orchestrator pipeline.

``onboard(company_id, product_id)`` is the single function triggered by
``POST /api/products/{id}/process``. It runs in the background:

    for each registered doc:
        download from Storage → parse → chunk (with product/doc metadata)
        → index into Moss

    build catalog entry from product + photo
    update Firestore status at each stage (parsing → indexing → ready | error)

Per-doc failure marks that doc ``error`` but keeps processing the rest —
never blocks the product.

Reference: HLD 01 §5 (sequence), HLD 12 §2e (pipeline).
"""

from __future__ import annotations

import logging
from typing import Any

from src.db import (
    get_product,
    list_docs,
    get_company,
    update_doc_parse_status,
    update_product_status,
)
from src.onboarding.catalog import build_catalog_entry
from src.onboarding.chunker import chunk_result
from src.onboarding.parser import ParseProvider

logger = logging.getLogger(__name__)


def onboard(
    company_id: str,
    product_id: str,
    *,
    parser: ParseProvider,
    indexer: Any,  # MossIndexer or MockMossIndexer
    download_fn: Any = None,  # storage.download_file or mock
) -> None:
    """Run the full onboarding pipeline for one product.

    This is designed to run as a background task.

    Args:
        company_id: The tenant.
        product_id: The product being processed.
        parser: The document parser (Unsiloed or PreParsedJson).
        indexer: The Moss indexer (real or mock).
        download_fn: Function to download files from Storage.
            Signature: ``(storage_path: str) -> bytes``.
    """
    try:
        # 1. Load product + company metadata
        product = get_product(company_id, product_id)
        if not product:
            logger.error("Product %s/%s not found", company_id, product_id)
            return

        company = get_company(company_id)
        if not company:
            logger.error("Company %s not found", company_id)
            return

        index_name = company.get("index_name", f"clutch-{company_id}")

        # 2. Mark as parsing
        update_product_status(company_id, product_id, "parsing")

        # 3. Get all registered docs
        docs = list_docs(company_id, product_id)
        if not docs:
            logger.warning("No docs registered for %s/%s", company_id, product_id)
            update_product_status(company_id, product_id, "ready", "No docs to process")
            return

        # 4. Process each doc
        all_chunks: list[dict[str, Any]] = []
        doc_errors = 0

        for doc in docs:
            doc_id = doc["doc_id"]
            storage_path = doc["storage_path"]
            doc_type = doc.get("doc_type", "other")

            try:
                update_doc_parse_status(company_id, product_id, doc_id, "parsing")

                # Download
                if download_fn:
                    source_bytes = download_fn(storage_path)
                else:
                    logger.warning("No download_fn — using empty bytes for %s", doc_id)
                    source_bytes = b""

                # Parse
                parse_result = parser.parse(source_bytes, filename=storage_path)

                # Chunk with product/doc metadata
                extra_metadata = {
                    "product_id": product_id,
                    "doc_id": doc_id,
                    "doc_type": doc_type,
                }
                chunks = chunk_result(
                    parse_result,
                    source=storage_path,
                    extra_metadata=extra_metadata,
                )
                all_chunks.extend(chunks)

                # Save raw Unsiloed output to R2 for debug modal
                if download_fn:
                    import json
                    from src.storage import upload_file, build_storage_path
                    debug_path = build_storage_path(company_id, product_id, f"debug_unsiloed_{doc_id}.json")
                    upload_file(debug_path, json.dumps(parse_result).encode('utf-8'), "application/json")

                update_doc_parse_status(company_id, product_id, doc_id, "succeeded")
                logger.info(
                    "Parsed doc %s → %d chunks", doc_id, len(chunks)
                )

            except Exception:
                logger.exception("Failed to process doc %s", doc_id)
                update_doc_parse_status(company_id, product_id, doc_id, "failed")
                doc_errors += 1
                # Continue with other docs — don't block the product

        # 5. Index all chunks into Moss
        if all_chunks:
            update_product_status(company_id, product_id, "indexing")
            
            # Save raw Moss chunks payload to R2 for debug modal
            if download_fn:
                import json
                from src.storage import upload_file, build_storage_path
                debug_moss_path = build_storage_path(company_id, product_id, "debug_moss.json")
                upload_file(debug_moss_path, json.dumps(all_chunks).encode('utf-8'), "application/json")

            indexed = indexer.inject(all_chunks, index_name=index_name)
            logger.info(
                "Indexed %d chunks into %s for product %s",
                indexed, index_name, product_id,
            )
        else:
            logger.warning("No chunks produced for %s/%s", company_id, product_id)

        # 6. Build catalog entry (for reference — stored on the Company)
        photo_bytes = None
        if product.get("photo_path") and download_fn:
            try:
                photo_bytes = download_fn(product["photo_path"])
            except Exception:
                logger.warning("Could not download reference photo for %s", product_id)

        catalog_entry = build_catalog_entry(product, photo_bytes)
        logger.info("Built catalog entry: %s (%s)", catalog_entry.name, catalog_entry.product_id)

        # 7. Mark as ready (or error if all docs failed)
        if doc_errors == len(docs):
            update_product_status(
                company_id, product_id, "error",
                f"All {doc_errors} docs failed to process",
            )
        else:
            msg = f"Indexed {len(all_chunks)} chunks"
            if doc_errors:
                msg += f" ({doc_errors} doc(s) had errors)"
            update_product_status(company_id, product_id, "ready", msg)

    except Exception:
        logger.exception("Onboarding pipeline failed for %s/%s", company_id, product_id)
        try:
            update_product_status(company_id, product_id, "error", "Pipeline failed")
        except Exception:
            logger.exception("Could not update status to error")
