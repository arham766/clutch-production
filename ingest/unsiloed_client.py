"""Thin client for the Unsiloed document-parsing API.

Handles the async parse → poll lifecycle:
  POST  https://prod.visionapi.unsiloed.ai/parse        -> { job_id, status: "Starting" }
  GET   https://prod.visionapi.unsiloed.ai/parse/{job}  -> poll until status == "Succeeded"

Docs: https://docs.unsiloed.ai/api-reference/parser/parse-document
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import requests

BASE_URL = "https://prod.visionapi.unsiloed.ai"
PARSE_URL = f"{BASE_URL}/parse"

# Terminal job states reported by the API.
_SUCCESS = "Succeeded"
_FAILURE_STATES = {"Failed", "Error", "Cancelled"}


class UnsiloedError(RuntimeError):
    """Raised when Unsiloed returns an error or a job fails."""


class UnsiloedClient:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL, timeout: int = 60):
        if not api_key:
            raise ValueError("UNSILOED_API_KEY is required.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"api-key": api_key})

    # ------------------------------------------------------------------ submit
    def _submit(
        self,
        *,
        file_path: Optional[Path] = None,
        url: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> str:
        if (file_path is None) == (url is None):
            raise ValueError("Provide exactly one of `file_path` or `url`.")

        data: dict[str, Any] = {
            # Sensible defaults for clean knowledge-base text.
            "use_high_resolution": "true",
            "layout_analysis": "smart_layout_detection",
            "ocr_strategy": "auto_detection",
            "merge_tables": "true",
            "segment_filter": "all",
        }
        if options:
            data.update(options)

        parse_endpoint = f"{self._base_url}/parse"
        if file_path is not None:
            with file_path.open("rb") as fh:
                files = {"file": (file_path.name, fh)}
                resp = self._session.post(
                    parse_endpoint, files=files, data=data, timeout=self._timeout
                )
        else:
            data["url"] = url
            resp = self._session.post(parse_endpoint, data=data, timeout=self._timeout)

        if resp.status_code >= 400:
            raise UnsiloedError(
                f"Parse submit failed ({resp.status_code}): {resp.text[:500]}"
            )

        payload = resp.json()
        job_id = payload.get("job_id")
        if not job_id:
            raise UnsiloedError(f"No job_id in response: {payload}")
        return job_id

    # -------------------------------------------------------------------- poll
    def _poll(
        self, job_id: str, *, interval: float = 2.0, max_wait: float = 600.0
    ) -> dict[str, Any]:
        status_url = f"{self._base_url}/parse/{job_id}"
        deadline = time.monotonic() + max_wait
        last_status = "?"

        while time.monotonic() < deadline:
            resp = self._session.get(status_url, timeout=self._timeout)
            if resp.status_code >= 400:
                raise UnsiloedError(
                    f"Poll failed ({resp.status_code}): {resp.text[:500]}"
                )
            payload = resp.json()
            last_status = payload.get("status", "?")

            if last_status == _SUCCESS:
                return payload
            if last_status in _FAILURE_STATES:
                raise UnsiloedError(
                    f"Job {job_id} ended with status '{last_status}': "
                    f"{payload.get('message', '')}"
                )

            time.sleep(interval)

        raise UnsiloedError(
            f"Job {job_id} did not finish within {max_wait:.0f}s (last status: {last_status})."
        )

    # ------------------------------------------------------------------ public
    def parse(
        self,
        source: str,
        *,
        options: Optional[dict[str, Any]] = None,
        poll_interval: float = 2.0,
        max_wait: float = 600.0,
    ) -> dict[str, Any]:
        """Parse a local file path or a URL and return the completed result payload.

        The returned payload contains ``chunks[].segments[]`` per the Unsiloed schema.
        """
        is_url = source.startswith("http://") or source.startswith("https://")
        if is_url:
            job_id = self._submit(url=source, options=options)
        else:
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            job_id = self._submit(file_path=path, options=options)

        return self._poll(job_id, interval=poll_interval, max_wait=max_wait)
