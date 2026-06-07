"""
Tests for src/onboarding/parser.py — parse providers.

Covers:
- PreParsedJsonProvider: valid JSON, invalid JSON
- UnsiloedProvider: synchronous result, poll success, poll failure, retry
- Factory: get_parser with/without key, unknown provider
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.onboarding.parser import (
    PreParsedJsonProvider,
    UnsiloedProvider,
    get_parser,
)


class TestPreParsedJsonProvider:
    def test_valid_json(self) -> None:
        provider = PreParsedJsonProvider()
        data = {"chunks": [{"content": "Hello", "heading": "Intro", "page_number": 1}]}
        result = provider.parse(json.dumps(data).encode())
        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["content"] == "Hello"

    def test_invalid_json(self) -> None:
        provider = PreParsedJsonProvider()
        result = provider.parse(b"this is not json at all")
        assert result == {"chunks": []}

    def test_empty_bytes(self) -> None:
        provider = PreParsedJsonProvider()
        result = provider.parse(b"")
        assert result == {"chunks": []}


class TestGetParserFactory:
    def test_pre_parsed_json(self) -> None:
        parser = get_parser("pre_parsed_json")
        assert isinstance(parser, PreParsedJsonProvider)

    def test_unsiloed_with_key(self) -> None:
        parser = get_parser("unsiloed", api_key="test-key")
        assert isinstance(parser, UnsiloedProvider)
        assert parser.api_key == "test-key"

    def test_unsiloed_without_key_degrades(self) -> None:
        """Without a key, Unsiloed should degrade to PreParsedJson."""
        parser = get_parser("unsiloed", api_key=None)
        assert isinstance(parser, PreParsedJsonProvider)

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown parse provider"):
            get_parser("teleport_parser")

    def test_custom_retries(self) -> None:
        parser = get_parser("unsiloed", api_key="k", max_retries=5)
        assert isinstance(parser, UnsiloedProvider)
        assert parser.max_retries == 5


class TestUnsiloedProvider:
    @patch("httpx.Client")
    def test_synchronous_result(self, MockClient: MagicMock) -> None:
        """If the API returns chunks directly (no job_id), use them."""
        client = MagicMock()
        MockClient.return_value.__enter__ = MagicMock(return_value=client)
        MockClient.return_value.__exit__ = MagicMock(return_value=False)

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "chunks": [{"content": "parsed text", "heading": "Overview"}]
        }
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        provider = UnsiloedProvider(api_key="test-key")
        result = provider.parse(b"fake pdf bytes", filename="manual.pdf")

        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["content"] == "parsed text"
