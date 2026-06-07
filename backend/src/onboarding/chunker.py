"""
Clutch onboarding — chunker.

Takes raw parse results and produces self-contained, heading-prepended,
string-metadata chunks ready for Moss indexing.

Moss metadata is **string-valued only** (HLD 00 §3).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Max characters per chunk before splitting
MAX_CHUNK_SIZE = 1500
# Overlap between split chunks (characters)
CHUNK_OVERLAP = 200


def chunk_result(
    result: dict[str, Any],
    *,
    source: str = "",
    extra_metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Convert a parse result into indexable chunks.

    Args:
        result: The parsed document (from ``parse_doc``). Expected shape:
            ``{"chunks": [{"content": str, "heading": str, "page_number": int, ...}]}``
        source: The source filename / identifier.
        extra_metadata: Additional string-valued metadata to attach to every
            chunk (e.g. ``{"product_id": "p1", "doc_id": "d1", "doc_type": "user_guide"}``).

    Returns:
        List of chunk dicts ready for Moss indexing::

            {
                "id": str,           # deterministic hash
                "text": str,         # heading-prepended content
                "metadata": {str: str},  # all string-valued
            }
    """
    segments = result.get("chunks") or result.get("segments") or []
    if not segments:
        logger.warning("Empty parse result for source=%s", source)
        return []

    base_meta = {"source": source}
    if extra_metadata:
        base_meta.update(extra_metadata)

    chunks: list[dict[str, Any]] = []
    current_heading = ""

    for seg in segments:
        content = seg.get("embed") or seg.get("content") or seg.get("markdown") or seg.get("text") or ""
        if not content.strip():
            logger.warning("Empty content in segment. Keys: %s", list(seg.keys()))
            continue

        heading = seg.get("heading") or current_heading
        if heading:
            current_heading = heading

        page = str(seg.get("page_number", ""))
        section = heading or "untitled"

        # Build heading-prepended text
        text = f"## {heading}\n\n{content}" if heading else content

        # Split long segments
        text_chunks = _split_text(text, MAX_CHUNK_SIZE, CHUNK_OVERLAP)

        for i, chunk_text in enumerate(text_chunks):
            chunk_id = _make_id(source, section, page, i)

            metadata: dict[str, str] = {
                **base_meta,
                "section": section,
                "page": page,
            }

            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": metadata,
            })

    logger.info(
        "Chunked source=%s into %d chunks (from %d segments)",
        source, len(chunks), len(segments),
    )
    return chunks


def _split_text(text: str, max_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks if it exceeds max_size."""
    if len(text) <= max_size:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_size

        # Try to break at a paragraph or sentence boundary
        if end < len(text):
            # Prefer paragraph break
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + max_size // 2:
                end = para_break + 2
            else:
                # Try sentence break
                sent_break = text.rfind(". ", start, end)
                if sent_break > start + max_size // 2:
                    end = sent_break + 2

        parts.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)

    return [p for p in parts if p]


def _make_id(source: str, section: str, page: str, index: int) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{source}:{section}:{page}:{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
