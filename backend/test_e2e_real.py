"""
End-to-end test with a real PDF: Unsiloed parse → chunker → Moss index.
Tests each stage independently so we can pinpoint failures.
"""
import os
import json
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

PDF_PATH = r"C:\Users\arham\Downloads\Skop — Pre-Seed Deck.pdf"

def test_unsiloed_parse():
    """Stage 1: Send real PDF to Unsiloed and get the raw response."""
    import httpx
    import time

    api_key = os.environ["UNSILOED_API_KEY"]
    
    with open(PDF_PATH, "rb") as f:
        pdf_bytes = f.read()
    
    logger.info("PDF size: %d bytes", len(pdf_bytes))
    
    headers = {"accept": "application/json", "api-key": api_key}
    
    with httpx.Client(timeout=60.0) as client:
        res = client.post(
            "https://prod.visionapi.unsiloed.ai/parse",
            files={"file": ("Skop_Deck.pdf", pdf_bytes, "application/pdf")},
            headers=headers,
        )
        res.raise_for_status()
        job = res.json()
        job_id = job.get("job_id") or job.get("id")
        logger.info("Job ID: %s", job_id)
        
        if not job_id and "chunks" in job:
            logger.info("Synchronous response!")
            return job
        
        # Poll
        for attempt in range(60):
            time.sleep(2)
            res = client.get(
                f"https://prod.visionapi.unsiloed.ai/parse/{job_id}",
                headers=headers,
            )
            res.raise_for_status()
            result = res.json()
            state = result.get("status", "").lower()
            logger.info("Poll %d: status=%s", attempt, state)
            if state == "succeeded":
                return result
            elif state in ("failed", "error"):
                raise RuntimeError(f"Parse failed: {result}")
    
    raise TimeoutError("Unsiloed timed out")


def test_chunker(parse_result):
    """Stage 2: Feed the parse result into the chunker and see what comes out."""
    from src.onboarding.chunker import chunk_result
    
    # Log the structure we got
    top_keys = list(parse_result.keys())
    logger.info("Parse result top-level keys: %s", top_keys)
    
    chunks_raw = parse_result.get("chunks") or parse_result.get("segments") or []
    logger.info("Number of raw chunks/segments: %d", len(chunks_raw))
    
    if chunks_raw:
        first = chunks_raw[0]
        logger.info("First chunk keys: %s", list(first.keys()))
        logger.info("First chunk 'embed' (first 200 chars): %s", str(first.get("embed", ""))[:200])
        logger.info("First chunk 'content' (first 200 chars): %s", str(first.get("content", ""))[:200])
        logger.info("First chunk 'segments' count: %s", len(first.get("segments", [])))
        if first.get("segments"):
            seg0 = first["segments"][0]
            logger.info("First segment keys: %s", list(seg0.keys()))
            logger.info("First segment 'content' (first 200): %s", str(seg0.get("content", ""))[:200])
    
    # Now run the chunker
    processed = chunk_result(parse_result, source="test_deck.pdf", extra_metadata={"product_id": "test", "doc_id": "test"})
    logger.info("Chunker produced %d chunks", len(processed))
    
    if processed:
        logger.info("First processed chunk text (first 200): %s", processed[0]["text"][:200])
    
    return processed


def test_moss_index(chunks):
    """Stage 3: Index chunks into Moss."""
    project_id = os.environ["MOSS_PROJECT_ID"]
    api_key = os.environ["MOSS_API_KEY"]
    
    from src.onboarding.indexer import MossIndexer
    indexer = MossIndexer(project_id=project_id, api_key=api_key)
    
    count = indexer.inject(chunks, index_name="clutch-e2e-test")
    logger.info("Indexed %d chunks into Moss", count)
    return count


def test_moss_query():
    """Stage 4: Query Moss to verify the chunks are there."""
    project_id = os.environ["MOSS_PROJECT_ID"]
    api_key = os.environ["MOSS_API_KEY"]
    
    from moss import MossClient, QueryOptions
    
    async def _query():
        client = MossClient(project_id, api_key)
        await client.load_index("clutch-e2e-test")
        results = await client.query("clutch-e2e-test", "What is Skop?", QueryOptions(top_k=3))
        for doc in results.docs:
            logger.info("  Score=%.3f ID=%s Text=%s", doc.score, doc.id, doc.text[:120])
        return results
    
    return asyncio.run(_query())


if __name__ == "__main__":
    print("=" * 60)
    print("STAGE 1: Unsiloed Parse")
    print("=" * 60)
    parse_result = test_unsiloed_parse()
    
    # Save raw result for debugging
    with open("debug_parse_result.json", "w") as f:
        json.dump(parse_result, f, indent=2, default=str)
    logger.info("Saved raw parse result to debug_parse_result.json")
    
    print("\n" + "=" * 60)
    print("STAGE 2: Chunker")
    print("=" * 60)
    chunks = test_chunker(parse_result)
    
    if not chunks:
        logger.error("NO CHUNKS PRODUCED. Check debug_parse_result.json for the raw Unsiloed output.")
        print("\nDumping first chunk from raw result for analysis...")
        raw_chunks = parse_result.get("chunks", [])
        if raw_chunks:
            print(json.dumps(raw_chunks[0], indent=2, default=str)[:2000])
        exit(1)
    
    print("\n" + "=" * 60)
    print("STAGE 3: Moss Index")
    print("=" * 60)
    test_moss_index(chunks)
    
    print("\n" + "=" * 60)
    print("STAGE 4: Moss Query")
    print("=" * 60)
    test_moss_query()
    
    print("\n" + "=" * 60)
    print("ALL STAGES PASSED!")
    print("=" * 60)
