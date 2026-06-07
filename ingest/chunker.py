"""Convert an Unsiloed parse result into Moss-ready chunks.

Design follows Moss's official indexing guidance (see moss-skills / md-indexer):
  * Each chunk's ``text`` is self-contained: the nearest heading is prepended so a
    small chunk is still discoverable on its own.
  * Small adjacent segments under the same heading + page are greedily merged toward
    a target size (better recall than one-segment-per-chunk).
  * Metadata values are ALL strings (Moss requires string-valued metadata).

Input shape (Unsiloed):
  result["chunks"][i]["segments"][j] = {
      segment_type, content, markdown, page_number, segment_id, confidence, bbox, ...
  }

Output: list[dict] with keys {id, text, metadata} ready to wrap in moss.DocumentInfo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Segment types that act as section headings (used as context, not standalone chunks).
HEADING_TYPES = {"Title", "SectionHeader", "Pageheader", "PageHeader"}
# Segment types that carry no useful retrieval text on their own.
SKIP_TYPES = {"Pagefooter", "PageFooter", "Pageheader", "PageHeader", "Picture"}

# Greedy-merge target window (characters).
TARGET_CHARS = 900
MAX_CHARS = 1500
MIN_CHARS = 40


def _slug(value: str, *, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return s[:max_len] or "doc"


def _segment_text(seg: dict[str, Any]) -> str:
    """Prefer markdown (keeps tables/lists structured), fall back to plain content."""
    text = (seg.get("markdown") or seg.get("content") or "").strip()
    return text


def _iter_segments(result: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for chunk in result.get("chunks", []) or []:
        for seg in chunk.get("segments", []) or []:
            yield seg


@dataclass
class _Pending:
    source: str
    section: str
    page: str
    parts: list[str] = field(default_factory=list)
    first_seg_id: str = ""
    seg_count: int = 0

    def char_len(self) -> int:
        return sum(len(p) for p in self.parts)

    def is_empty(self) -> bool:
        return self.seg_count == 0


def chunk_result(
    result: dict[str, Any],
    *,
    source: str,
    extra_metadata: dict[str, str] | None = None,
    target_chars: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
) -> list[dict[str, Any]]:
    """Turn one Unsiloed parse result into a list of Moss chunk dicts."""
    extra_metadata = extra_metadata or {}
    source_stem = _slug(source)

    chunks: list[dict[str, Any]] = []
    current_section = ""
    pending: _Pending | None = None
    running_idx = 0

    def flush() -> None:
        nonlocal pending, running_idx
        if pending is None or pending.is_empty():
            pending = None
            return
        body = "\n\n".join(pending.parts).strip()
        if len(body) < MIN_CHARS and not chunks:
            # keep tiny standalone content only if it's the very first thing
            pass
        # Prepend section heading for self-contained context.
        if pending.section and not body.lower().startswith(pending.section.lower()):
            text = f"{pending.section}\n\n{body}"
        else:
            text = body

        metadata = {
            "source": source,
            "section": pending.section or "",
            "page": pending.page,
            "segment_count": str(pending.seg_count),
            **extra_metadata,
        }
        # Moss requires string-valued metadata; coerce defensively.
        metadata = {k: str(v) for k, v in metadata.items() if v is not None}

        chunk_id = f"{source_stem}-p{pending.page}-{running_idx:04d}"
        chunks.append({"id": chunk_id, "text": text, "metadata": metadata})
        running_idx += 1
        pending = None

    for seg in _iter_segments(result):
        seg_type = seg.get("segment_type", "")
        page = str(seg.get("page_number", "") or "")
        seg_id = str(seg.get("segment_id", "") or "")
        text = _segment_text(seg)

        if seg_type in HEADING_TYPES:
            # Heading starts a new section; flush whatever was accumulating.
            flush()
            current_section = text or current_section
            continue

        if seg_type in SKIP_TYPES or not text:
            continue

        # Start a new pending chunk on section change, page change, or size overflow.
        if (
            pending is None
            or pending.section != current_section
            or pending.page != page
            or pending.char_len() + len(text) > max_chars
        ):
            flush()
            pending = _Pending(
                source=source, section=current_section, page=page, first_seg_id=seg_id
            )

        pending.parts.append(text)
        pending.seg_count += 1
        if not pending.first_seg_id:
            pending.first_seg_id = seg_id

        # Soft target: once we pass the target, flush so chunks stay focused.
        if pending.char_len() >= target_chars:
            flush()

    flush()
    return chunks
