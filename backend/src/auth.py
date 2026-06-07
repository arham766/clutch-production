"""
Clutch authentication — Firebase ID token verification + company scoping.

Every ``/api/*`` request carries ``Authorization: Bearer <firebase_id_token>``.
This module verifies it, resolves the ``uid`` → ``company_id`` mapping in
Firestore, and exposes a FastAPI dependency that every protected route uses.

Reference: HLD 06 §3e (Auth), HLD 12 §2a (server-side verify).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials, firestore
from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase initialisation (called once at startup)
# ---------------------------------------------------------------------------

_firebase_app: firebase_admin.App | None = None


def init_firebase(
    project_id: str,
    credentials_path: str,
) -> firebase_admin.App:
    """Initialise the Firebase Admin SDK (idempotent).

    Args:
        project_id: Firebase project ID.
        credentials_path: Path to the service-account JSON file.

    Returns:
        The initialised ``firebase_admin.App``.
    """
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    cred = credentials.Certificate(credentials_path)
    _firebase_app = firebase_admin.initialize_app(cred, {
        "projectId": project_id,
        "storageBucket": f"{project_id}.appspot.com",
    })
    logger.info("Firebase Admin SDK initialised for project %s", project_id)
    return _firebase_app


def get_firebase_app() -> firebase_admin.App:
    """Return the initialised Firebase app, or raise if not yet initialised."""
    if _firebase_app is None:
        raise RuntimeError(
            "Firebase not initialised. Call init_firebase() at startup."
        )
    return _firebase_app


def _reset_firebase() -> None:
    """Reset the Firebase app singleton (for testing only)."""
    global _firebase_app
    if _firebase_app is not None:
        firebase_admin.delete_app(_firebase_app)
        _firebase_app = None


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_token(authorization: str | None) -> str:
    """Extract and verify a Firebase ID token from the Authorization header.

    Args:
        authorization: The raw ``Authorization`` header value.

    Returns:
        The Firebase ``uid`` from the verified token.

    Raises:
        HTTPException(401): If the token is missing, malformed, or invalid.
    """
    if not authorization:
        print("MISSING AUTHORIZATION HEADER")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        print(f"INVALID HEADER FORMAT: {authorization}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'",
        )

    token = parts[1].strip()
    if not token:
        print("EMPTY TOKEN")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
        )

    try:
        decoded = firebase_auth.verify_id_token(token, clock_skew_seconds=10)
    except firebase_auth.ExpiredIdTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token expired: {str(e)}")
    except firebase_auth.RevokedIdTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token revoked: {str(e)}")
    except firebase_auth.InvalidIdTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        logger.exception("Unexpected error verifying Firebase token")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")

    uid: str = decoded.get("uid", "")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing uid",
        )

    return uid


# ---------------------------------------------------------------------------
# Company scoping (uid → company_id via Firestore)
# ---------------------------------------------------------------------------

def get_company_id_for_user(uid: str) -> str:
    """Look up the company_id for a given Firebase uid.

    The mapping lives in Firestore at ``users/{uid}``.

    Args:
        uid: Firebase user ID.

    Returns:
        The ``company_id`` the user belongs to.

    Raises:
        HTTPException(403): If the user is not mapped to any company.
    """
    db = firestore.client()
    doc = db.collection("users").document(uid).get()

    if not doc.exists:
        from src.db import create_company
        data = create_company(uid=uid, name=f"Auto-Company {uid[:6]}", email="")
        return data["company_id"]

    data: dict[str, Any] = doc.to_dict()  # type: ignore[assignment]
    company_id = data.get("company_id")
    if not company_id:
        from src.db import create_company
        data = create_company(uid=uid, name=f"Auto-Company {uid[:6]}", email="")
        return data["company_id"]

    return company_id


# ---------------------------------------------------------------------------
# AuthContext — the result of successful auth, carried per-request
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AuthContext:
    """The authenticated identity for a request.

    Injected by the ``get_current_user`` FastAPI dependency into every
    protected route.
    """

    uid: str
    company_id: str


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> AuthContext:
    """FastAPI dependency: verify token + resolve company.

    Usage::

        @router.get("/api/products")
        async def list_products(auth: AuthContext = Depends(get_current_user)):
            ...
    """
    authorization = request.headers.get("Authorization")
    uid = verify_token(authorization)
    company_id = get_company_id_for_user(uid)
    return AuthContext(uid=uid, company_id=company_id)
