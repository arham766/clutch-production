"""Live integration tests against the real Unsiloed + Moss APIs.

Skipped by default. Run explicitly once credentials and a sample file exist:

    # .env.local must have UNSILOED_API_KEY, MOSS_PROJECT_ID, MOSS_PROJECT_KEY
    # set FIXALONG_TEST_FILE to a small local PDF and FIXALONG_TEST_INDEX to a throwaway index
    uv run pytest -m live
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(".env.local")
load_dotenv(".env")

import ingest  # noqa: E402
from unsiloed_client import UnsiloedClient  # noqa: E402

_REQUIRED = ["UNSILOED_API_KEY", "MOSS_PROJECT_ID", "MOSS_PROJECT_KEY"]
_missing = [k for k in _REQUIRED if not os.getenv(k)]
_test_file = os.getenv("FIXALONG_TEST_FILE")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        bool(_missing) or not _test_file,
        reason=f"live test needs {_REQUIRED} + FIXALONG_TEST_FILE "
               f"(missing: {_missing or 'creds ok'}; file: {_test_file or 'unset'})",
    ),
]


def test_live_parse_returns_segments():
    client = UnsiloedClient(os.environ["UNSILOED_API_KEY"])
    result = client.parse(_test_file)
    assert result.get("status") == "Succeeded"
    assert result.get("chunks"), "expected at least one chunk from the document"


def test_live_full_ingest_into_throwaway_index():
    index_name = os.getenv("FIXALONG_TEST_INDEX", "fixalong-live-test")
    unsiloed = UnsiloedClient(os.environ["UNSILOED_API_KEY"])
    chunks = ingest.parse_and_chunk(unsiloed, [_test_file])
    assert chunks, "no chunks produced"

    asyncio.run(
        ingest.inject_into_moss(
            chunks,
            project_id=os.environ["MOSS_PROJECT_ID"],
            project_key=os.environ["MOSS_PROJECT_KEY"],
            index_name=index_name,
            model_id=os.getenv("MOSS_MODEL_ID", "moss-minilm"),
            recreate=True,  # throwaway index — rebuild each run
        )
    )

    # Verify it's queryable.
    from moss import MossClient, QueryOptions

    async def _check():
        c = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
        await c.load_index(index_name)
        res = await c.query(index_name, Path(_test_file).stem, QueryOptions(top_k=3))
        return res

    result = asyncio.run(_check())
    assert getattr(result, "docs", None), "query returned no docs"
