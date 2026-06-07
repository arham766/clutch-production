"""
Clutch API — dependency injection for FastAPI routes.

Provides request-scoped dependencies: config, auth context, background task runner.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Request

from src.auth import AuthContext, get_current_user
from src.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config singleton (set at startup via lifespan)
# ---------------------------------------------------------------------------

_config: Config | None = None


def set_config(cfg: Config) -> None:
    """Set the global config (called once at startup)."""
    global _config
    _config = cfg


def get_config() -> Config:
    """FastAPI dependency: return the loaded config."""
    if _config is None:
        raise RuntimeError("Config not initialised. Check app lifespan.")
    return _config


# ---------------------------------------------------------------------------
# Auth dependency (re-export for convenience)
# ---------------------------------------------------------------------------

async def get_auth(request: Request) -> AuthContext:
    """Alias for get_current_user — every protected route uses this."""
    return await get_current_user(request)
