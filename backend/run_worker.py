"""
Clutch — LiveKit Agent Worker entrypoint (matches Abdul's run_clutch.py pattern).

Run this SEPARATELY from the FastAPI server:

    # Terminal 1 — API
    uvicorn src.api.main:app --reload --port 8000

    # Terminal 2 — LiveKit Worker
    python run_worker.py start

It connects to LiveKit cloud, waits for rooms, and dispatches run_session for each.
"""

from __future__ import annotations

import os
import sys
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("clutch.worker")


def main() -> int:
    from src.config import load_config
    from src.gateway import init_gateway
    from src.contracts import CatalogEntry, RetrievalQuery
    from src.app_livekit import build_clutch_worker, build_retrieval
    from src.realtime import run_worker

    cfg = load_config()

    if not cfg.livekit_api_key:
        sys.exit("LIVEKIT_API_KEY is not set — cannot start worker.")
    if not os.environ.get("CARTESIA_API_KEY"):
        sys.exit("CARTESIA_API_KEY is not set — cannot start worker.")

    gw = init_gateway(cfg)

    # Warm Moss index BEFORE accepting calls (avoids ~1.6s cold-start on first query)
    try:
        rf, _ = build_retrieval(cfg, gw)
        asyncio.run(rf(RetrievalQuery("demo", "warmup", None)))
        logger.info("Moss index warmed.")
    except Exception as e:
        logger.warning("Moss warmup skipped: %s", str(e)[:60])

    demo_catalog = [CatalogEntry(product_id="lj-m404", name="LaserJet Pro M404", brand="HP")]
    worker_opts = build_clutch_worker(cfg=cfg, gateway_client=gw, catalog=demo_catalog)

    # Explicit dispatch — auto-dispatch is flaky on LiveKit Cloud (Abdul's finding).
    # The /connection-details endpoint dispatches by this name.
    worker_opts.agent_name = "clutch"
    logger.info("Agent registered as agent_name='clutch' (explicit dispatch mode)")

    logger.info(
        "Worker ready. Connecting to %s...",
        cfg.livekit_url,
    )
    run_worker(worker_opts)   # blocks until killed — cli.run_app under the hood
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
