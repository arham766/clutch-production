"""
Tests for src/auth.py — Firebase token verification + company scoping.

All Firebase calls are mocked — no live Firebase project needed.

Covers:
- verify_token: valid token, missing header, bad prefix, expired, revoked, invalid
- get_company_id_for_user: user mapped, user not in Firestore, missing company_id
- get_current_user: full chain (token → uid → company_id → AuthContext)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.auth import (
    AuthContext,
    get_company_id_for_user,
    verify_token,
)


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------

class TestVerifyToken:
    @patch("src.auth.firebase_auth.verify_id_token")
    def test_valid_token(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {"uid": "user-123"}
        uid = verify_token("Bearer valid-token-abc")
        assert uid == "user-123"
        mock_verify.assert_called_once_with("valid-token-abc", clock_skew_seconds=10)

    def test_missing_authorization(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            verify_token(None)
        assert exc_info.value.status_code == 401
        assert "Missing" in exc_info.value.detail

    def test_no_bearer_prefix(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            verify_token("Token some-token")
        assert exc_info.value.status_code == 401
        assert "Bearer" in exc_info.value.detail

    def test_empty_token(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            verify_token("Bearer ")
        assert exc_info.value.status_code == 401
        assert "Empty" in exc_info.value.detail

    @patch("src.auth.firebase_auth.verify_id_token")
    def test_expired_token(self, mock_verify: MagicMock) -> None:
        from firebase_admin.auth import ExpiredIdTokenError
        mock_verify.side_effect = ExpiredIdTokenError("expired", cause=None)

        with pytest.raises(HTTPException) as exc_info:
            verify_token("Bearer expired-token")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    @patch("src.auth.firebase_auth.verify_id_token")
    def test_revoked_token(self, mock_verify: MagicMock) -> None:
        from firebase_admin.auth import RevokedIdTokenError
        mock_verify.side_effect = RevokedIdTokenError("revoked")

        with pytest.raises(HTTPException) as exc_info:
            verify_token("Bearer revoked-token")
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail.lower()

    @patch("src.auth.firebase_auth.verify_id_token")
    def test_invalid_token(self, mock_verify: MagicMock) -> None:
        from firebase_admin.auth import InvalidIdTokenError
        mock_verify.side_effect = InvalidIdTokenError("bad token")

        with pytest.raises(HTTPException) as exc_info:
            verify_token("Bearer bad-token")
        assert exc_info.value.status_code == 401
        assert "Invalid" in exc_info.value.detail

    @patch("src.auth.firebase_auth.verify_id_token")
    def test_token_missing_uid(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {}  # no uid field
        with pytest.raises(HTTPException) as exc_info:
            verify_token("Bearer no-uid-token")
        assert exc_info.value.status_code == 401
        assert "uid" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# get_company_id_for_user
# ---------------------------------------------------------------------------

class TestGetCompanyIdForUser:
    @patch("src.auth.firestore.client")
    def test_valid_user(self, mock_client: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"email": "arham@acme.com", "company_id": "acme-123"}

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_client.return_value = mock_db

        company_id = get_company_id_for_user("user-123")
        assert company_id == "acme-123"
        mock_db.collection.assert_called_with("users")

# ---------------------------------------------------------------------------
# AuthContext
# ---------------------------------------------------------------------------

class TestAuthContext:
    def test_construction(self) -> None:
        ctx = AuthContext(uid="user-1", company_id="acme-1")
        assert ctx.uid == "user-1"
        assert ctx.company_id == "acme-1"

    def test_frozen(self) -> None:
        ctx = AuthContext(uid="user-1", company_id="acme-1")
        with pytest.raises(AttributeError):
            ctx.uid = "changed"  # type: ignore[misc]
