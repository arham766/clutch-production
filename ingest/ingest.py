"""Unsiloed -> Moss ingestion pipeline.

Parses one or more documents (PDF/DOCX/PPTX/images or URLs) with Unsiloed, chunks
them with heading-aware context, and injects them into the SAME Moss index the
Fixalong agent reads from (MOSS_INDEX_NAME).

Usage:
    python ingest.py docs/handbook.pdf docs/pricing.pdf
    python ingest.py https://example.com/whitepaper.pdf --index my-kb
    python ingest.py docs/ --recreate          # parse every file in a folder, fresh index

Env (see .env.example):
    UNSILOED_API_KEY, MOSS_PROJECT_ID, MOSS_PROJECT_KEY, MOSS_INDEX_NAME, [MOSS_MODEL_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from moss import DocumentInfo, MossClient, MutationOptions

from chunker import chunk_result
from unsiloed_client import UnsiloedClient

DEFAULT_MODEL_ID = "moss-minilm"
BATCH_SIZE = 500
SUPPORTED_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif",
    ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
}


def _resolve_sources(inputs: list[str]) -> list[str]:
    """Expand folders into their supported files; pass URLs/files through."""
    sources: list[str] = []
    for item in inputs:
        if item.startswith("http://") or item.startswith("https://"):
            sources.append(item)
            continue
        path = Path(item)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in SUPPORTED_SUFFIXES:
                    sources.append(str(child))
        elif path.is_file():
            sources.append(str(path))
        else:
            raise FileNotFoundError(f"Source not found: {item}")
    if not sources:
        raise ValueError("No valid sources resolved from inputs.")
    return sources


def parse_and_chunk(
    unsiloed: UnsiloedClient,
    sources: list[str],
    *,
    save_debug: bool = False,
) -> list[dict[str, Any]]:
    """Parse each source and return a flat list of Moss chunk dicts."""
    all_chunks: list[dict[str, Any]] = []
    for i, source in enumerate(sources, 1):
        label = Path(source).name if not source.startswith("http") else source
        print(f"[{i}/{len(sources)}] Parsing: {label} ...")
        result = unsiloed.parse(source)

        if save_debug:
            dbg = Path("debug") / f"{Path(label).stem}.json"
            dbg.parent.mkdir(exist_ok=True)
            dbg.write_text(json.dumps(result, indent=2), encoding="utf-8")

        chunks = chunk_result(
            result,
            source=label,
            extra_metadata={"doc_index": str(i)},
        )
        print(f"        -> {len(chunks)} chunks "
              f"(from {result.get('total_chunks', '?')} raw segments groups)")
        all_chunks.extend(chunks)
    return all_chunks


def _to_documents(chunks: list[dict[str, Any]]) -> list[DocumentInfo]:
    return [
        DocumentInfo(id=c["id"], text=c["text"], metadata=c.get("metadata") or {})
        for c in chunks
    ]


async def inject_into_moss(
    chunks: list[dict[str, Any]],
    *,
    project_id: str,
    project_key: str,
    index_name: str,
    model_id: str,
    recreate: bool,
) -> None:
    client = MossClient(project_id, project_key)
    documents = _to_documents(chunks)

    # Discover whether the index already exists.
    existing = {ix.name for ix in await client.list_indexes()}
    exists = index_name in existing

    if recreate and exists:
        print(f"Deleting existing index '{index_name}' (--recreate) ...")
        await client.delete_index(index_name)
        exists = False

    if not exists:
        head, *rest = _batches(documents, BATCH_SIZE)
        print(f"Creating index '{index_name}' with {len(head)} docs (model: {model_id}) ...")
        created = await client.create_index(index_name, head, model_id)
        print(f"  created (job: {created.job_id}, docs: {created.doc_count})")
        remaining_batches = rest
    else:
        print(f"Index '{index_name}' exists -> upserting {len(documents)} docs ...")
        remaining_batches = _batches(documents, BATCH_SIZE)

    for n, batch in enumerate(remaining_batches, 1):
        print(f"  upserting batch {n} ({len(batch)} docs) ...")
        result = await client.add_docs(
            index_name, batch, MutationOptions(upsert=True)
        )
        print(f"    done (job: {result.job_id}, docs: {result.doc_count})")

    print(f"\n✅ Index '{index_name}' ready with {len(documents)} total chunks.")


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def main() -> None:
    load_dotenv(".env.local")
    load_dotenv(".env")  # fallback

    parser = argparse.ArgumentParser(description="Unsiloed -> Moss ingestion pipeline")
    parser.add_argument("sources", nargs="+", help="File path(s), folder(s), or URL(s) to ingest")
    parser.add_argument("--index", default=os.getenv("MOSS_INDEX_NAME"),
                        help="Moss index name (default: $MOSS_INDEX_NAME)")
    parser.add_argument("--model", default=os.getenv("MOSS_MODEL_ID", DEFAULT_MODEL_ID),
                        help="Moss embedding model id")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete and rebuild the index from scratch")
    parser.add_argument("--save-debug", action="store_true",
                        help="Write raw Unsiloed JSON to ./debug for inspection")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse + chunk only; print chunks, do not touch Moss")
    parser.add_argument("--sign", action="store_true",
                        help="Sign each chunk as a FACT envelope (provenance + trust)")
    parser.add_argument("--kb-key", default=os.getenv("FACT_KB_KEY_PATH", ".kb_key.b64"),
                        help="Path to the KB signing key (created if absent when --sign)")
    parser.add_argument("--fresh-days", type=int, default=30,
                        help="How long signed facts stay fresh (days)")
    args = parser.parse_args()

    api_key = os.getenv("UNSILOED_API_KEY")
    project_id = os.getenv("MOSS_PROJECT_ID")
    project_key = os.getenv("MOSS_PROJECT_KEY")
    index_name = args.index

    missing = [
        name for name, val in {
            "UNSILOED_API_KEY": api_key,
            "MOSS_PROJECT_ID": project_id,
            "MOSS_PROJECT_KEY": project_key,
            "MOSS_INDEX_NAME (or --index)": index_name,
        }.items() if not val
    ]
    if missing and not args.dry_run:
        raise SystemExit("Missing env/args: " + ", ".join(missing))
    if not api_key:
        raise SystemExit("UNSILOED_API_KEY is required.")

    sources = _resolve_sources(args.sources)
    unsiloed = UnsiloedClient(api_key)
    chunks = parse_and_chunk(unsiloed, sources, save_debug=args.save_debug)

    print(f"\nTotal chunks across {len(sources)} source(s): {len(chunks)}")

    if args.sign:
        from fact_moss import load_kb_key, sign_chunks

        kb_key = load_kb_key(args.kb_key)
        print(f"Signing {len(chunks)} chunks as FACT envelopes.")
        print(f"  KB issuer (did:key): {kb_key.issuer}")
        print("  -> set this as the agent's trusted issuer for verification.")
        chunks = sign_chunks(chunks, kb_key, fresh_seconds=args.fresh_days * 86400)

    if args.dry_run:
        for c in chunks[:10]:
            preview = c["text"].replace("\n", " ")[:120]
            print(f"  - {c['id']} | {c['metadata'].get('section','')[:30]} | {preview}...")
        if len(chunks) > 10:
            print(f"  ... and {len(chunks) - 10} more")
        print("\n(dry run — nothing written to Moss)")
        return

    assert project_id and project_key and index_name
    asyncio.run(
        inject_into_moss(
            chunks,
            project_id=project_id,
            project_key=project_key,
            index_name=index_name,
            model_id=args.model,
            recreate=args.recreate,
        )
    )


if __name__ == "__main__":
    main()
