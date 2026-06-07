"""End-to-end pipeline integration tests.

Unsiloed HTTP is mocked with `responses`; Moss is the in-memory `fake_moss`
fixture. No network, no API keys, no credits — but exercises the real
unsiloed_client + chunker + ingest wiring together.
"""

from __future__ import annotations

import moss
import pytest
import responses

import ingest
from unsiloed_client import PARSE_URL, UnsiloedClient


def _chunks(n: int) -> list[dict]:
    return [
        {"id": f"d-{i:04d}", "text": f"chunk text {i}", "metadata": {"source": "x.pdf"}}
        for i in range(n)
    ]


# ----------------------------------------------------------------- inject paths
async def test_inject_creates_index_when_absent(fake_moss):
    await ingest.inject_into_moss(
        _chunks(3), project_id="p", project_key="k",
        index_name="kb", model_id="moss-minilm", recreate=False,
    )
    assert len(fake_moss.created) == 1
    name, docs, model = fake_moss.created[0]
    assert name == "kb"
    assert model == "moss-minilm"
    assert len(docs) == 3
    assert all(isinstance(d, moss.DocumentInfo) for d in docs)
    assert fake_moss.added == []  # everything fit in the create call


async def test_inject_upserts_when_index_exists(fake_moss):
    fake_moss.existing_indexes.add("kb")
    await ingest.inject_into_moss(
        _chunks(3), project_id="p", project_key="k",
        index_name="kb", model_id="moss-minilm", recreate=False,
    )
    assert fake_moss.created == []
    assert len(fake_moss.added) == 1
    name, docs, options = fake_moss.added[0]
    assert name == "kb" and len(docs) == 3
    # upsert flag passed through MutationOptions
    assert isinstance(options, moss.MutationOptions)


async def test_recreate_deletes_then_creates(fake_moss):
    fake_moss.existing_indexes.add("kb")
    await ingest.inject_into_moss(
        _chunks(2), project_id="p", project_key="k",
        index_name="kb", model_id="moss-minilm", recreate=True,
    )
    assert fake_moss.deleted == ["kb"]
    assert len(fake_moss.created) == 1


async def test_batching_create_path(fake_moss):
    total = ingest.BATCH_SIZE * 2 + 37  # 1037 with default 500
    await ingest.inject_into_moss(
        _chunks(total), project_id="p", project_key="k",
        index_name="kb", model_id="moss-minilm", recreate=False,
    )
    created_docs = len(fake_moss.created[0][1])
    added_docs = sum(len(d) for _, d, _ in fake_moss.added)
    assert created_docs == ingest.BATCH_SIZE
    assert created_docs + added_docs == total
    assert len(fake_moss.added) == 2  # remaining two batches


async def test_batching_upsert_path(fake_moss):
    fake_moss.existing_indexes.add("kb")
    total = ingest.BATCH_SIZE + 10
    await ingest.inject_into_moss(
        _chunks(total), project_id="p", project_key="k",
        index_name="kb", model_id="moss-minilm", recreate=False,
    )
    assert fake_moss.created == []
    assert sum(len(d) for _, d, _ in fake_moss.added) == total
    assert len(fake_moss.added) == 2


# ----------------------------------------------------------- full parse->inject
@responses.activate
async def test_full_pipeline_parse_chunk_inject(fake_moss, sample_parse_result):
    responses.add(responses.POST, PARSE_URL,
                  json={"job_id": "j1", "status": "Starting"}, status=200)
    responses.add(responses.GET, f"{PARSE_URL}/j1", json=sample_parse_result, status=200)

    unsiloed = UnsiloedClient("test-key")
    chunks = ingest.parse_and_chunk(unsiloed, ["https://example.com/acme.pdf"])
    assert len(chunks) == 2  # Pricing + Security

    await ingest.inject_into_moss(
        chunks, project_id="p", project_key="k",
        index_name="acme-kb", model_id="moss-minilm", recreate=False,
    )
    name, docs, _ = fake_moss.created[0]
    assert name == "acme-kb"
    assert len(docs) == 2
    texts = [d.text for d in docs]
    assert any("SOC 2" in t for t in texts)


def test_resolve_sources_expands_folder(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.docx").write_bytes(b"x")
    (tmp_path / "ignore.txt").write_text("nope")
    resolved = ingest._resolve_sources([str(tmp_path)])
    assert len(resolved) == 2
    assert all(s.endswith((".pdf", ".docx")) for s in resolved)


def test_resolve_sources_passes_urls():
    urls = ["https://example.com/a.pdf", "http://example.com/b.pdf"]
    assert ingest._resolve_sources(urls) == urls
