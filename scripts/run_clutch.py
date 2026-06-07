"""run_clutch — the REAL Clutch worker entrypoint (full stack, all of Fardin's + Cognition wired).

Composition root in action: builds the realtime worker with the REAL per-session ConversationAgent
(04) + Qwen perception (02) + Fardin's retrieval (03) + Cartesia speech (05), all routed via the
TrueFoundry gateway, and runs it against LiveKit.

    uv run --group voice --group retrieval python scripts/run_clutch.py

Needs (in .env / .env.local):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (realtime transport)
    CARTESIA_API_KEY                                   (STT/TTS)
    TRUEFOUNDRY_BASE_URL, TRUEFOUNDRY_API_KEY          (vision + compose via gateway)
    VISION_MODEL, REASON_MODEL                         (gateway model ids)

`--check` prints what's wired + whether creds are present, then exits 0 (never blocks a keyless box).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv          # noqa: E402
load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

from contracts import CatalogEntry      # noqa: E402

# Demo single-tenant catalog (Arham's tenancy/onboarding resolves this per company in prod).
# Two visually-distinct products so vision identify can differentiate them and scope retrieval.
DEMO_CATALOG = [
    CatalogEntry(product_id="iphone-17-pro", name="iPhone 17 Pro", brand="Apple",
                 aliases=["iphone", "apple iphone", "iphone 17", "iphone 17 pro"],
                 description="Apple iPhone 17 Pro smartphone: large glass-front touchscreen, a square "
                             "rear camera array with three lenses plus a LiDAR scanner, USB-C port, a "
                             "Camera Control button and Action button on the side."),
    CatalogEntry(product_id="dji-mic-mini", name="DJI Mic Mini", brand="DJI",
                 aliases=["dji mic", "mic mini", "dji mic mini", "wireless mic", "wireless microphone",
                          "lavalier mic", "clip mic"],
                 description="DJI Mic Mini wireless microphone system: very small clip-on transmitter(s) "
                             "with a fuzzy windscreen, a separate receiver, and a pill-shaped charging "
                             "case. Tiny devices with status LEDs, not a phone."),
]


def _gateway():
    base = os.environ.get("TRUEFOUNDRY_BASE_URL")
    if not base:
        return None
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=base, api_key=os.environ.get("TRUEFOUNDRY_API_KEY"))


def _missing() -> list[str]:
    need = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "CARTESIA_API_KEY"]
    return [k for k in need if not os.environ.get(k)]


def main() -> int:
    miss = _missing()
    if "--check" in sys.argv:
        gw = "yes" if os.environ.get("TRUEFOUNDRY_BASE_URL") else "no (mock vision/compose)"
        print("run_clutch wiring:")
        print("  agent(04)+retrieval(03)+perception(02)+speech(05) -> realtime worker")
        print(f"  gateway (vision+compose): {gw}")
        print(f"  vision model : {os.environ.get('VISION_MODEL', '(default)')}")
        print(f"  reason model : {os.environ.get('REASON_MODEL', '(default)')}")
        if miss:
            print(f"SKIP — missing creds to run: {', '.join(miss)}")
        else:
            print("creds present — `python scripts/run_clutch.py` (no --check) would start the worker")
        return 0

    if miss:
        sys.exit(f"missing creds: {', '.join(miss)} (set them in .env / .env.local)")

    from config import Config
    from app import build_clutch_worker
    from realtime import run_worker

    cfg = Config.from_env()
    if os.environ.get("REASON_MODEL"):
        cfg.reason_model = os.environ["REASON_MODEL"]
    if os.environ.get("REASON_FAST_MODEL"):
        cfg.reason_fast_model = os.environ["REASON_FAST_MODEL"]   # race fallback (fast instruct)
    if os.environ.get("VISION_MODEL"):
        cfg.vision_model = os.environ["VISION_MODEL"]   # read by build_identify
    # Tighter turn detection → snappier hot path (turn-end detected sooner).
    cfg.endpoint_min_ms = int(os.environ.get("ENDPOINT_MIN_MS", 200))
    cfg.endpoint_max_ms = int(os.environ.get("ENDPOINT_MAX_MS", 1000))
    # With pHash change-gating, one distinct view == one look. batch=2 would deadlock on a held-still
    # device (identical frames never fill a 2-frame batch), so identify on each changed frame.
    cfg.see_batch = int(os.environ.get("SEE_BATCH", 1))

    # Warm the Moss index BEFORE accepting calls so the first real query doesn't pay ~1.6s cold-start.
    import asyncio as _asyncio
    from app import build_retrieval
    from contracts import RetrievalQuery as _RQ
    try:
        rf, _ = build_retrieval(cfg, _gateway())
        _asyncio.run(rf(_RQ("demo", "warmup", None)))
        print("Moss index warmed.")
    except Exception as e:
        print("warmup skipped:", str(e)[:60])

    opts = build_clutch_worker(cfg, _gateway(), catalog=DEMO_CATALOG)
    # Explicit-dispatch name: a browser frontend (e.g. moss-hacker-starter) dispatches by agent_name.
    # Register under AGENT_NAME so its dispatch reaches us (fixes Cloud auto-dispatch flakiness).
    if os.environ.get("AGENT_NAME"):
        opts.agent_name = os.environ["AGENT_NAME"]
        print(f"registering as agent_name={opts.agent_name!r} (explicit dispatch)")
    print("Clutch worker built — connecting to LiveKit...")
    run_worker(opts)            # blocking: cli.run_app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
