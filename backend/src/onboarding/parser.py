"""
Clutch onboarding — document parser (adapter pattern).

Supports pluggable parse providers (HLD 00 §7). Default is Unsiloed;
fallback is pre-parsed JSON for dev/testing/offline.

The parser takes raw document bytes and returns a standardised result dict
with segments (text chunks with headings, page numbers, etc.).

Failure modes (HLD 01 §6, HLD 12 §6):
- Unsiloed slow/erroring: retry with exponential backoff, then fall back
  to pre-parsed JSON (commit per-doc pre-parsed JSON alongside the doc so
  ingestion never blocks on a live parser)
- Empty parse result: returns ``{"chunks": []}``; the pipeline skips empty docs
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parser protocol (the swap point)
# ---------------------------------------------------------------------------

class ParseProvider(Protocol):
    """Provider-agnostic document parser interface."""

    def parse(self, source: bytes, filename: str = "") -> dict[str, Any]:
        """Parse a document and return a structured result.

        Returns:
            Dict with at minimum a ``chunks`` key containing a list of
            segment dicts, each with ``content``, ``segment_type``,
            ``page_number``, and optionally ``heading``.
        """
        ...


# ---------------------------------------------------------------------------
# Pre-parsed JSON provider (dev/testing/offline fallback)
# ---------------------------------------------------------------------------

class PreParsedJsonProvider:
    """Load a pre-parsed JSON file instead of calling a live parser.

    This is the offline fallback (HLD 01 §6): per-doc pre-parsed JSON
    committed alongside the doc, so ingestion never blocks on a live parser.
    """

    def parse(self, source: bytes, filename: str = "") -> dict[str, Any]:
        """Interpret source as JSON (pre-parsed result)."""
        try:
            return json.loads(source)
        except json.JSONDecodeError:
            logger.warning("PreParsedJsonProvider: source is not valid JSON")
            return {"chunks": []}


# ---------------------------------------------------------------------------
# Unsiloed provider (the default live parser) — with retry + fallback
# ---------------------------------------------------------------------------

class UnsiloedProvider:
    """Parse documents via the Unsiloed API.

    Submits an async job and polls until Succeeded.
    Requires ``UNSILOED_API_KEY`` in config.

    Retry strategy (HLD 01 §6):
    - Submit: up to ``max_retries`` attempts with exponential backoff
    - Poll: exponential backoff with cap at 10 seconds, up to 60 attempts
    - On total failure: raises RuntimeError (pipeline marks doc as ``error``
      and continues with other docs)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://prod.visionapi.unsiloed.ai",
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries

    def parse(self, source: bytes, filename: str = "") -> dict[str, Any]:
        """Submit doc to Unsiloed, poll for result, return structured chunks.

        Retries submission on transient errors. Polls with exponential backoff.
        """
        import httpx

        headers = {
            "accept": "application/json",
            "api-key": self.api_key,
        }

        # Submit with retries
        job: dict[str, Any] | None = None
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    submit_resp = client.post(
                        f"{self.base_url}/parse",
                        files={"file": (filename or "document.pdf", source, "application/pdf")},
                        headers=headers,
                    )
                    submit_resp.raise_for_status()
                    job = submit_resp.json()
                    break
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                last_error = e
                wait = min(2.0 * (2 ** attempt), 30.0)
                logger.warning(
                    "Unsiloed submit attempt %d/%d failed: %s. Retrying in %.1fs",
                    attempt + 1, self.max_retries, e, wait,
                )
                if attempt < self.max_retries - 1:
                    time.sleep(wait)

        if job is None:
            raise RuntimeError(
                f"Unsiloed: all {self.max_retries} submit attempts failed"
            ) from last_error

        job_id = job.get("job_id") or job.get("id")

        if not job_id:
            # Some APIs return the result directly (synchronous response)
            if "chunks" in job:
                return job
            raise ValueError(f"No job_id in Unsiloed response: {job}")

        # Poll for completion with exponential backoff
        with httpx.Client(timeout=30.0) as client:
            for attempt in range(60):
                wait = min(2.0 * (1.2 ** attempt), 10.0)
                time.sleep(wait)

                try:
                    status_resp = client.get(
                        f"{self.base_url}/parse/{job_id}",
                        headers={"api-key": self.api_key, "accept": "application/json"},
                    )
                    status_resp.raise_for_status()
                    result = status_resp.json()
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    logger.warning(
                        "Unsiloed poll attempt %d failed: %s", attempt + 1, e,
                    )
                    continue

                state = result.get("status", "").lower()
                if state == "succeeded":
                    logger.info("Unsiloed parse succeeded for job %s", job_id)
                    return result
                elif state in ("failed", "error"):
                    raise RuntimeError(
                        f"Unsiloed parse failed for job {job_id}: "
                        f"{result.get('error', 'unknown')}"
                    )

        raise TimeoutError(f"Unsiloed parse timed out for job {job_id}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_parser(
    provider: str = "unsiloed",
    api_key: str | None = None,
    max_retries: int = 3,
) -> ParseProvider:
    """Get a parser by provider name.

    Args:
        provider: ``"unsiloed"`` or ``"pre_parsed_json"``.
        api_key: Required for ``"unsiloed"``.
        max_retries: Number of submit retries for Unsiloed.

    Returns:
        A ``ParseProvider`` instance.
    """
    if provider == "pre_parsed_json":
        return PreParsedJsonProvider()
    elif provider == "unsiloed":
        if not api_key:
            logger.warning("No Unsiloed API key -- falling back to PreParsedJsonProvider")
            return PreParsedJsonProvider()
        return UnsiloedProvider(api_key=api_key, max_retries=max_retries)
    else:
        raise ValueError(f"Unknown parse provider: {provider}")
