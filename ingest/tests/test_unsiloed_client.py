"""Integration tests for the Unsiloed client (HTTP mocked with `responses`)."""

from __future__ import annotations

import pytest
import responses
from responses import matchers

from unsiloed_client import PARSE_URL, UnsiloedClient, UnsiloedError


@responses.activate
def test_parse_url_submit_and_poll(sample_parse_result):
    # Submit -> job_id.
    responses.add(responses.POST, PARSE_URL,
                  json={"job_id": "job-123", "status": "Starting"}, status=200)
    # Poll: first still running, then succeeded.
    status_url = f"{PARSE_URL}/job-123"
    responses.add(responses.GET, status_url, json={"status": "Processing"}, status=200)
    responses.add(responses.GET, status_url, json=sample_parse_result, status=200)

    client = UnsiloedClient("test-key")
    result = client.parse("https://example.com/doc.pdf", poll_interval=0, max_wait=10)

    assert result["status"] == "Succeeded"
    assert result["total_chunks"] == 2
    # One POST + two GETs.
    assert len(responses.calls) == 3


@responses.activate
def test_api_key_header_sent():
    responses.add(
        responses.POST, PARSE_URL,
        json={"job_id": "j", "status": "Starting"}, status=200,
        match=[matchers.header_matcher({"api-key": "secret-key"})],
    )
    responses.add(responses.GET, f"{PARSE_URL}/j",
                  json={"status": "Succeeded", "chunks": []}, status=200)

    client = UnsiloedClient("secret-key")
    client.parse("https://example.com/doc.pdf", poll_interval=0)
    # If the header didn't match, responses would have raised ConnectionError.


@responses.activate
def test_file_upload_multipart(tmp_path, sample_parse_result):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    responses.add(responses.POST, PARSE_URL,
                  json={"job_id": "jf", "status": "Starting"}, status=200)
    responses.add(responses.GET, f"{PARSE_URL}/jf", json=sample_parse_result, status=200)

    client = UnsiloedClient("test-key")
    result = client.parse(str(f), poll_interval=0)
    assert result["status"] == "Succeeded"


@responses.activate
def test_submit_http_error_raises():
    responses.add(responses.POST, PARSE_URL, json={"detail": "bad"}, status=400)
    client = UnsiloedClient("test-key")
    with pytest.raises(UnsiloedError, match="Parse submit failed"):
        client.parse("https://example.com/doc.pdf", poll_interval=0)


@responses.activate
def test_missing_job_id_raises():
    responses.add(responses.POST, PARSE_URL, json={"status": "Starting"}, status=200)
    client = UnsiloedClient("test-key")
    with pytest.raises(UnsiloedError, match="No job_id"):
        client.parse("https://example.com/doc.pdf", poll_interval=0)


@responses.activate
def test_job_failure_state_raises():
    responses.add(responses.POST, PARSE_URL,
                  json={"job_id": "jx", "status": "Starting"}, status=200)
    responses.add(responses.GET, f"{PARSE_URL}/jx",
                  json={"status": "Failed", "message": "corrupt pdf"}, status=200)
    client = UnsiloedClient("test-key")
    with pytest.raises(UnsiloedError, match="Failed"):
        client.parse("https://example.com/doc.pdf", poll_interval=0)


@responses.activate
def test_poll_timeout_raises():
    responses.add(responses.POST, PARSE_URL,
                  json={"job_id": "jt", "status": "Starting"}, status=200)
    responses.add(responses.GET, f"{PARSE_URL}/jt",
                  json={"status": "Processing"}, status=200)
    client = UnsiloedClient("test-key")
    with pytest.raises(UnsiloedError, match="did not finish"):
        client.parse("https://example.com/doc.pdf", poll_interval=0, max_wait=0)


def test_missing_file_raises():
    client = UnsiloedClient("test-key")
    with pytest.raises(FileNotFoundError):
        client.parse("does/not/exist.pdf")


def test_empty_api_key_rejected():
    with pytest.raises(ValueError):
        UnsiloedClient("")
