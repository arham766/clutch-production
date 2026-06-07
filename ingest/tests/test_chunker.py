"""Unit/integration tests for the segment -> Moss chunk transform."""

from __future__ import annotations

from chunker import chunk_result


def test_basic_sectioning(sample_parse_result):
    chunks = chunk_result(sample_parse_result, source="acme.pdf")
    # Title section + Security section = 2 chunks.
    assert len(chunks) == 2
    sections = {c["metadata"]["section"] for c in chunks}
    assert sections == {"Acme Pricing", "Security"}


def test_heading_context_is_prepended(sample_parse_result):
    chunks = chunk_result(sample_parse_result, source="acme.pdf")
    security = next(c for c in chunks if c["metadata"]["section"] == "Security")
    assert security["text"].startswith("Security")
    assert "SOC 2" in security["text"]


def test_pictures_and_footers_are_skipped(sample_parse_result):
    chunks = chunk_result(sample_parse_result, source="acme.pdf")
    joined = " ".join(c["text"] for c in chunks)
    assert "Confidential" not in joined  # Pagefooter dropped


def test_metadata_values_are_all_strings(sample_parse_result):
    chunks = chunk_result(sample_parse_result, source="acme.pdf")
    for c in chunks:
        for key, value in c["metadata"].items():
            assert isinstance(value, str), f"{key} is not a string: {value!r}"


def test_expected_metadata_keys(sample_parse_result):
    chunks = chunk_result(
        sample_parse_result, source="acme.pdf", extra_metadata={"doc_index": "1"}
    )
    meta = chunks[0]["metadata"]
    assert {"source", "section", "page", "segment_count", "doc_index"} <= set(meta)
    assert meta["source"] == "acme.pdf"
    assert meta["doc_index"] == "1"


def test_ids_are_unique_and_stable(sample_parse_result):
    chunks = chunk_result(sample_parse_result, source="acme.pdf")
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids))
    # Stable across runs.
    again = [c["id"] for c in chunk_result(sample_parse_result, source="acme.pdf")]
    assert ids == again


def test_markdown_preferred_over_content():
    result = {
        "chunks": [{"segments": [
            {"segment_type": "Text", "content": "plain", "markdown": "**bold**",
             "page_number": 1, "segment_id": "x"},
        ]}]
    }
    chunks = chunk_result(result, source="d.pdf")
    assert "**bold**" in chunks[0]["text"]
    assert "plain" not in chunks[0]["text"]


def test_size_merge_splits_oversized_sections():
    # One section with many large text segments should split into multiple chunks.
    big = "word " * 300  # ~1500 chars per segment
    segments = [{"segment_type": "SectionHeader", "content": "Big", "page_number": 1,
                 "segment_id": "h"}]
    for i in range(5):
        segments.append({"segment_type": "Text", "markdown": big,
                         "page_number": 1, "segment_id": f"t{i}"})
    result = {"chunks": [{"segments": segments}]}
    chunks = chunk_result(result, source="big.pdf", target_chars=900, max_chars=1500)
    assert len(chunks) >= 3
    assert all(len(c["text"]) <= 1500 + len("Big\n\n") for c in chunks)


def test_empty_result_yields_no_chunks():
    assert chunk_result({"chunks": []}, source="empty.pdf") == []
    assert chunk_result({}, source="empty.pdf") == []
