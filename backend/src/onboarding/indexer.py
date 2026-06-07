"""
Clutch onboarding — Moss indexer.

Creates/upserts chunks into a per-company Moss index (``clutch-<company>``).
Batched for efficiency.

Reference: HLD 01 §3 (chunk + index), HLD 03 (retrieval reads these).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Batch size for Moss upserts
BATCH_SIZE = 50


class MossIndexer:
    """Index chunks into a Moss collection.

    Wraps the Moss SDK to provide a clean interface for the onboarding pipeline.
    The actual Moss client is injected — no direct SDK import in callers.
    """

    def __init__(self, project_id: str, api_key: str) -> None:
        self.project_id = project_id
        self.api_key = api_key

    def inject(
        self,
        chunks: list[dict[str, Any]],
        *,
        index_name: str,
        recreate: bool = False,
    ) -> int:
        """Inject chunks into a Moss index."""
        if not chunks:
            logger.warning("No chunks to index into %s", index_name)
            return 0

        import asyncio
        from moss import MossClient, DocumentInfo

        async def _run():
            try:
                client = MossClient(self.project_id, self.api_key)

                # Check if the index already exists
                index_exists = False
                try:
                    existing = await client.list_indexes()
                    for idx in existing:
                        if idx.name == index_name:
                            index_exists = True
                            break
                except Exception:
                    logger.warning("Could not list indexes — will try create_index")

                # Handle recreate
                if recreate and index_exists:
                    try:
                        await client.delete_index(index_name)
                        logger.info("Deleted index %s for recreation", index_name)
                        index_exists = False
                    except Exception:
                        logger.warning("Could not delete index %s", index_name)

                documents = [
                    DocumentInfo(
                        id=chunk["id"],
                        text=chunk["text"],
                        metadata=chunk.get("metadata", {})
                    )
                    for chunk in chunks
                ]

                total = 0
                if index_exists:
                    # Index already exists — use add_docs (upsert)
                    for i in range(0, len(documents), BATCH_SIZE):
                        batch = documents[i : i + BATCH_SIZE]
                        await client.add_docs(index_name, batch)
                        total += len(batch)
                else:
                    # New index — create with first batch, add_docs for rest
                    first_batch = documents[:BATCH_SIZE]
                    await client.create_index(index_name, first_batch, "moss-minilm")
                    total += len(first_batch)
                    for i in range(BATCH_SIZE, len(documents), BATCH_SIZE):
                        batch = documents[i : i + BATCH_SIZE]
                        await client.add_docs(index_name, batch)
                        total += len(batch)

                logger.info(
                    "Indexed %d chunks into %s (recreate=%s, existed=%s)",
                    total, index_name, recreate, index_exists,
                )
                return total
            except Exception:
                logger.exception("Failed to index chunks into %s", index_name)
                raise

        return asyncio.run(_run())


class MockMossIndexer:
    """In-memory indexer for testing — no network calls."""

    def __init__(self) -> None:
        self.indexes: dict[str, list[dict[str, Any]]] = {}

    def inject(
        self,
        chunks: list[dict[str, Any]],
        *,
        index_name: str,
        recreate: bool = False,
    ) -> int:
        if recreate:
            self.indexes.pop(index_name, None)

        if index_name not in self.indexes:
            self.indexes[index_name] = []

        self.indexes[index_name].extend(chunks)
        return len(chunks)
