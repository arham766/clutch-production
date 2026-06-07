"""
Clutch wiring file — ``build_app(cfg)``.

**This file contains ZERO business logic.** It is the single place where
every subfolder's real or mock functions are imported, constructed with the
right config, and passed to the modules that need them.

The rule (HLD 10 §7): no sibling imports. If A needs B, ``app.py``
imports both and passes B's function to A at construction time.

Reference: HLD 10 §7 (the wiring file), HLD 12 §2f.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime container — holds all wired components
# ---------------------------------------------------------------------------

@dataclass
class ClutchRuntime:
    """The fully wired runtime — everything the app needs to run.

    Each field is a callable or client that was constructed with the right
    config and dependencies. Routes, the agent, and the voice loop pull
    from here.
    """

    # Config
    config: Config

    # B: Platform / Tenancy
    resolve_company: Callable[..., Any] = field(default=lambda k: None)
    scope_session: Callable[..., Any] = field(default=lambda k, m="text": None)

    # B: Gateway
    gateway: Any = None  # GatewayClient or MockGatewayClient

    # B: Onboarding
    onboard: Callable[..., None] | None = None

    # C: Perception (Tonmoy)
    identify: Callable[..., Any] | None = None

    # D: Retrieval + Compose (Fardin)
    retrieve: Callable[..., Any] | None = None

    # D: LiveKit minter
    livekit_minter: Callable[..., Any] | None = None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_app(
    cfg: Config,
    *,
    use_mocks: bool = False,
) -> ClutchRuntime:
    """Construct the full Clutch runtime.

    Args:
        cfg: Loaded configuration.
        use_mocks: If True, use all mock implementations (dev/testing).

    Returns:
        A ``ClutchRuntime`` with everything wired up.
    """
    runtime = ClutchRuntime(config=cfg)

    # ------------------------------------------------------------------
    # B: Tenancy
    # ------------------------------------------------------------------
    if use_mocks:
        from src.mocks import stub_resolve_company, stub_scope_session
        runtime.resolve_company = stub_resolve_company
        runtime.scope_session = stub_scope_session
        logger.info("Wired: stub tenancy")
    else:
        from src.tenancy import resolve_company, scope_session
        runtime.resolve_company = resolve_company
        runtime.scope_session = scope_session
        logger.info("Wired: real tenancy")

    # ------------------------------------------------------------------
    # B: Gateway
    # ------------------------------------------------------------------
    if use_mocks:
        from src.mocks import MockGatewayClient
        runtime.gateway = MockGatewayClient()
        logger.info("Wired: mock gateway")
    else:
        from src.gateway import init_gateway
        runtime.gateway = init_gateway(cfg)
        logger.info("Wired: real gateway → %s", cfg.tf_base_url)

    # ------------------------------------------------------------------
    # B: Onboarding pipeline
    # ------------------------------------------------------------------
    from src.onboarding.parser import get_parser
    from src.onboarding.indexer import MossIndexer, MockMossIndexer
    from src.onboarding.pipeline import onboard as _onboard
    from src.storage import download_file

    if use_mocks:
        parser = get_parser("pre_parsed_json")
        indexer = MockMossIndexer()
        download = lambda path: b'{"chunks": []}'  # noqa: E731
        logger.info("Wired: mock onboarding (pre_parsed_json + MockMossIndexer)")
    else:
        parser = get_parser(cfg.parse_provider, api_key=cfg.unsiloed_api_key)
        indexer = MossIndexer(
            project_id=cfg.moss_project_id,
            api_key=cfg.moss_api_key,
        )
        download = download_file
        logger.info("Wired: real onboarding (%s + MossIndexer)", cfg.parse_provider)

    def _bound_onboard(company_id: str, product_id: str) -> None:
        _onboard(
            company_id,
            product_id,
            parser=parser,
            indexer=indexer,
            download_fn=download,
        )

    runtime.onboard = _bound_onboard

    # Wire the onboard function into the products route
    from src.api.routes.products import set_onboard_fn
    set_onboard_fn(_bound_onboard)

    # ------------------------------------------------------------------
    # C: Perception (Tonmoy's seam)
    # ------------------------------------------------------------------
    if use_mocks:
        from src.mocks import canned_identify
        runtime.identify = canned_identify
        logger.info("Wired: canned identify")
    else:
        # Tonmoy provides: from src.perception import identify
        # For now, fall back to canned
        try:
            from src.perception import identify  # type: ignore[import-not-found]
            runtime.identify = identify
            logger.info("Wired: real identify (Tonmoy)")
        except ImportError:
            from src.mocks import canned_identify
            runtime.identify = canned_identify
            logger.warning("Wired: canned identify (src.perception not found)")

    # ------------------------------------------------------------------
    # D: Retrieval (Fardin's seam)
    # ------------------------------------------------------------------
    if use_mocks:
        from src.mocks import canned_retrieve
        runtime.retrieve = canned_retrieve
        logger.info("Wired: canned retrieval")
    else:
        # Fardin provides: from src.retrieval import retrieve
        try:
            from src.retrieval import retrieve  # type: ignore[import-not-found]
            runtime.retrieve = retrieve
            logger.info("Wired: real retrieval (Fardin)")
        except ImportError:
            from src.mocks import canned_retrieve
            runtime.retrieve = canned_retrieve
            logger.warning("Wired: canned retrieval (src.retrieval not found)")

    # ------------------------------------------------------------------
    # D: LiveKit token minter
    # ------------------------------------------------------------------
    if use_mocks:
        import time

        def mock_minter(company_id: str, modality: str) -> dict[str, str]:
            return {
                "token": f"mock-lk-token-{company_id}-{int(time.time())}",
                "url": "wss://localhost:7880",
            }

        runtime.livekit_minter = mock_minter
        logger.info("Wired: mock LiveKit minter")
    else:
        try:
            from livekit.api import AccessToken, VideoGrants  # type: ignore[import-untyped]

            def real_minter(company_id: str, modality: str) -> dict[str, str]:
                room_name = f"clutch-{company_id}-{modality}"
                token = AccessToken(
                    cfg.livekit_api_key,
                    cfg.livekit_api_secret,
                )
                token.with_identity(f"customer-{company_id}")
                token.with_grants(
                    VideoGrants(
                        room_join=True,
                        room=room_name,
                    )
                )
                return {
                    "token": token.to_jwt(),
                    "url": cfg.livekit_url,
                }

            runtime.livekit_minter = real_minter
            logger.info("Wired: real LiveKit minter")
        except ImportError:
            logger.warning("livekit-api not installed — using mock minter")
            import time

            def fallback_minter(company_id: str, modality: str) -> dict[str, str]:
                return {
                    "token": f"fallback-lk-{company_id}-{int(time.time())}",
                    "url": cfg.livekit_url,
                }

            runtime.livekit_minter = fallback_minter

    # Wire LiveKit minter into widget route
    from src.api.routes.widget import set_livekit_minter
    set_livekit_minter(runtime.livekit_minter)

    logger.info("Clutch runtime built successfully (use_mocks=%s)", use_mocks)
    return runtime
