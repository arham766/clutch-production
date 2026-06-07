"""One-off: seed the `clutch-demo` Moss index directly from the harness fixture corpus.

The project has no Unsiloed key (so the full ingest pipeline can't run), but the retrieval
Limb only needs a populated, queryable Moss index named `clutch-<company>`. This pushes the
same fixture chunks the mock path uses straight into Moss via the SDK, giving the --live path
a real index to read. Idempotent: pass --recreate to rebuild.

    .venv/Scripts/python.exe -m scripts.seed_clutch_demo            # create if absent
    .venv/Scripts/python.exe -m scripts.seed_clutch_demo --recreate # rebuild
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

load_dotenv(_ROOT / ".env.local")
load_dotenv(_ROOT / ".env")

from scripts._retrieval_harness import fixture_corpus  # noqa: E402

INDEX = os.getenv("CLUTCH_INDEX", "clutch-demo")
MODEL_ID = os.getenv("MOSS_MODEL_ID", "moss-minilm")


async def main(recreate: bool) -> int:
    from moss import DocumentInfo, MossClient

    pid = os.environ["MOSS_PROJECT_ID"]
    pkey = os.environ["MOSS_PROJECT_KEY"]
    c = MossClient(pid, pkey)

    existing = {ix.name for ix in await c.list_indexes()}
    if INDEX in existing and recreate:
        print(f"deleting existing index {INDEX!r} ...")
        await c.delete_index(INDEX)
        existing.discard(INDEX)
    if INDEX in existing:
        print(f"index {INDEX!r} already exists; nothing to do (use --recreate to rebuild)")
        return 0

    docs = [
        DocumentInfo(id=ch.id, text=ch.text, metadata=ch.metadata)
        for ch in fixture_corpus()
    ]
    print(f"creating index {INDEX!r} with {len(docs)} docs (model {MODEL_ID}) ...")
    res = await c.create_index(INDEX, docs, MODEL_ID)
    print(f"  created: job={getattr(res,'job_id',None)} docs={getattr(res,'doc_count',None)}")

    # wait until queryable
    print("loading/warming index ...")
    name = await c.load_index(INDEX, auto_refresh=False)
    print(f"  loaded: {name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--recreate", action="store_true")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.recreate)))
