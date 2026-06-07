"""Shared fixtures: sample Unsiloed payloads and an in-memory fake Moss client."""

from __future__ import annotations

from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Sample Unsiloed parse results (shape: result["chunks"][i]["segments"][j])
# --------------------------------------------------------------------------- #
@pytest.fixture
def sample_parse_result() -> dict[str, Any]:
    return {
        "job_id": "job-123",
        "status": "Succeeded",
        "total_chunks": 2,
        "chunks": [
            {
                "segments": [
                    {"segment_type": "Title", "content": "Acme Pricing",
                     "page_number": 1, "segment_id": "s1"},
                    {"segment_type": "Text",
                     "markdown": "Acme offers three plans: Starter, Pro, and Enterprise.",
                     "page_number": 1, "segment_id": "s2"},
                    {"segment_type": "ListItem",
                     "markdown": "Pro: $40/mo, 5 seats, Snowflake connector.",
                     "page_number": 1, "segment_id": "s3"},
                ]
            },
            {
                "segments": [
                    {"segment_type": "SectionHeader", "content": "Security",
                     "page_number": 2, "segment_id": "s4"},
                    {"segment_type": "Text",
                     "markdown": "Acme is SOC 2 Type II certified and GDPR compliant.",
                     "page_number": 2, "segment_id": "s5"},
                    {"segment_type": "Picture", "content": "",
                     "page_number": 2, "segment_id": "s6"},
                    {"segment_type": "Pagefooter", "content": "Confidential",
                     "page_number": 2, "segment_id": "s7"},
                ]
            },
        ],
    }


def make_large_parse_result(n_sections: int) -> dict[str, Any]:
    """A parse result that yields ~n_sections chunks (one section each)."""
    chunks = []
    for i in range(n_sections):
        chunks.append({
            "segments": [
                {"segment_type": "SectionHeader", "content": f"Section {i}",
                 "page_number": i + 1, "segment_id": f"h{i}"},
                {"segment_type": "Text",
                 "markdown": f"Body text for section number {i}. " * 3,
                 "page_number": i + 1, "segment_id": f"t{i}"},
            ]
        })
    return {"status": "Succeeded", "total_chunks": n_sections, "chunks": chunks}


# --------------------------------------------------------------------------- #
# Fake Moss client — records calls, simulates index existence in memory.
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, job_id: str, index_name: str, doc_count: int):
        self.job_id = job_id
        self.index_name = index_name
        self.doc_count = doc_count


class _IndexInfo:
    def __init__(self, name: str):
        self.name = name


class FakeMossClient:
    """Async, in-memory stand-in for moss.MossClient."""

    def __init__(self, project_id: str, project_key: str):
        self.project_id = project_id
        self.project_key = project_key
        self.existing_indexes: set[str] = set()
        self.created: list[tuple[str, list, str]] = []
        self.added: list[tuple[str, list, Any]] = []
        self.deleted: list[str] = []

    async def list_indexes(self):
        return [_IndexInfo(n) for n in sorted(self.existing_indexes)]

    async def create_index(self, name, docs, model_id):
        docs = list(docs)
        self.created.append((name, docs, model_id))
        self.existing_indexes.add(name)
        return _Result("job-create", name, len(docs))

    async def add_docs(self, name, docs, options=None):
        docs = list(docs)
        self.added.append((name, docs, options))
        self.existing_indexes.add(name)
        return _Result("job-add", name, len(docs))

    async def delete_index(self, name):
        self.deleted.append(name)
        self.existing_indexes.discard(name)
        return True


@pytest.fixture
def fake_moss(monkeypatch):
    """Patch ingest.MossClient to return a single controllable FakeMossClient.

    Tests can pre-seed ``fake.existing_indexes`` to exercise the upsert path.
    """
    import ingest

    fake = FakeMossClient("test-project", "test-key")
    monkeypatch.setattr(ingest, "MossClient", lambda pid, pkey: fake)
    return fake
