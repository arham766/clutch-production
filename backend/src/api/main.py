"""
Clutch API — FastAPI application.

The main app with lifespan, CORS, routers, and health check.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.deps import set_config
from src.api.routes import companies, embed, products, widget
from src.models import HealthResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: init config + Firebase on startup."""
    logger.info("Clutch API starting up...")

    # Config is loaded and set by the entry point (run_api or app.py wiring).
    # If not set yet, try loading from env:
    from src.api.deps import get_config
    try:
        get_config()
        logger.info("Config already loaded.")
    except RuntimeError:
        try:
            from src.config import load_config
            cfg = load_config()
            set_config(cfg)

            from src.auth import init_firebase
            init_firebase(cfg.firebase_project_id, cfg.firebase_credentials)
            
            # Wire up the full runtime so background processing (onboarding) works
            from src.app import build_app
            build_app(cfg)
            
            logger.info("Config + Firebase + Runtime initialised from env.")
        except Exception as e:
            logger.exception("Could not auto-load config: %s", e)
            logger.warning(
                "Could not auto-load config — running in test/dev mode. "
                "Set config via set_config() before making requests."
            )

    yield

    logger.info("Clutch API shutting down.")


# ---------------------------------------------------------------------------
# App construction
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Clutch API",
        description="Platform API for Clutch — live video support for hardware.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow console and widget origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: lock down in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(companies.router)
    app.include_router(products.router)
    app.include_router(embed.router)
    app.include_router(widget.router)

    # Health check
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse()

    return app


# Module-level app for `uvicorn src.api.main:app`
app = create_app()
