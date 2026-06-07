"""Smoke-test the REAL perception path against a real image (you have keys).

Runs Qwen-VL closed-set identify through the TrueFoundry AI Gateway on a device photo + an optional
product reference photo — no mocks.

    uv run python scripts/smoke_identify.py <device.jpg> [<product_ref.jpg>]

Env:
    TRUEFOUNDRY_BASE_URL   # gateway OpenAI-compatible endpoint
    TRUEFOUNDRY_API_KEY
    VISION_MODEL           # gateway logical/model name (default: qwen2.5-vl-72b-instruct)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv               # noqa: E402
load_dotenv()                                # pick up TRUEFOUNDRY_* from .env

from contracts import CatalogEntry          # noqa: E402
from perception import make_identify         # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: smoke_identify.py <device.jpg> [<product_ref.jpg>]")
    frame = Path(sys.argv[1]).read_bytes()
    ref = Path(sys.argv[2]).read_bytes() if len(sys.argv) > 2 else None

    # A tiny one-product catalog so closed-set classification has a target.
    catalog = [CatalogEntry(product_id="demo-1", name="Demo Product", brand="Demo",
                            description="the device in the reference photo", ref_image=ref)]

    from openai import AsyncOpenAI   # the gateway client lives OUTSIDE perception (S5)
    gateway = AsyncOpenAI(
        base_url=os.environ["TRUEFOUNDRY_BASE_URL"],
        api_key=os.environ["TRUEFOUNDRY_API_KEY"],
    )
    identify = make_identify(gateway, model=os.environ.get("VISION_MODEL", "qwen2.5-vl-72b-instruct"))

    result = await identify([frame], catalog)
    print("product_id        :", result.product_id)
    print("confidence        :", round(result.confidence, 3))
    print("model_source      :", result.model_source)
    print("raw_text (OCR)    :", result.raw_text)
    print("needs_confirmation:", result.needs_confirmation)
    print("problem.summary   :", result.problem.summary)
    print("problem.signals   :", result.problem.to_query())
    print("candidates        :", [(c.product_id, round(c.confidence, 3)) for c in result.candidates])


if __name__ == "__main__":
    asyncio.run(main())
