"""Seed `clutch-demo` with TWO real product manuals — content pulled ONLY from the PDFs.

  - iPhone 17 Pro  → product_id `iphone-17-pro`  (data/manuals/iphone-17-pro-info.pdf)
  - DJI Mic Mini   → product_id `dji-mic-mini`   (data/manuals/dji-mic-mini.pdf, AES-encrypted)

No hand-authored text: every chunk is extracted page text from the manual, windowed, and tagged with
its `product_id` + source + page. Retrieval scoped to the identified product never mixes the two.
Recreates the index.

    uv run --group retrieval --with pypdf --with cryptography python -m scripts.seed_clutch_manuals
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
load_dotenv(_ROOT / ".env.local")
load_dotenv(_ROOT / ".env")

INDEX = os.getenv("CLUTCH_INDEX", "clutch-demo")
MODEL_ID = os.getenv("MOSS_MODEL_ID", "moss-minilm")

# (pdf path, product_id, source label)
MANUALS = [
    (_ROOT / "data" / "manuals" / "iphone-17-pro-info.pdf", "iphone-17-pro",
     "iPhone 17 Pro - Important Product Information (Apple)"),
    (_ROOT / "data" / "manuals" / "dji-mic-mini.pdf", "dji-mic-mini",
     "DJI Mic Mini User Manual"),
]

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
MIN_CHUNK = 40


def _clean(text: str) -> str:
    text = text.replace("’", "'").replace("�", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def _window(text: str):
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + CHUNK_SIZE])
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return out


def _pdf_to_docs(path: Path, pid: str, source: str, DocumentInfo):
    """Extract real page text → windowed chunks. Section metadata is the real PDF page number."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    docs = []
    for pno, page in enumerate(reader.pages, start=1):
        txt = _clean(page.extract_text() or "")
        if len(txt) < MIN_CHUNK:
            continue
        for wi, seg in enumerate(_window(txt)):
            seg = seg.strip()
            if len(seg) < MIN_CHUNK:
                continue
            cid = f"{pid}-p{pno}" + (f"-{wi}" if wi else "")
            docs.append(DocumentInfo(
                id=cid, text=seg,
                metadata={"source": source, "section": f"p.{pno}", "product_id": pid}))
    return docs


async def main() -> int:
    from moss import DocumentInfo, MossClient

    all_docs = []
    for path, pid, source in MANUALS:
        if not path.exists():
            sys.exit(f"manual not found: {path}")
        docs = _pdf_to_docs(path, pid, source, DocumentInfo)
        print(f"{pid}: {len(docs)} chunks extracted from {path.name}")
        all_docs += docs

    c = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
    existing = {ix.name for ix in await c.list_indexes()}
    if INDEX in existing:
        print(f"deleting existing index {INDEX!r} ...")
        await c.delete_index(INDEX)
    print(f"creating {INDEX!r} with {len(all_docs)} docs (model {MODEL_ID}) ...")
    res = await c.create_index(INDEX, all_docs, MODEL_ID)
    print(f"  created: docs={getattr(res, 'doc_count', None)}")
    name = await c.load_index(INDEX, auto_refresh=False)
    print(f"  loaded: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
