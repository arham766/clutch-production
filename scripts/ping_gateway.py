"""Verify the TrueFoundry AI Gateway: access token + base_url + model are wired correctly.

    uv run python scripts/ping_gateway.py

Reads .env: TRUEFOUNDRY_BASE_URL, TRUEFOUNDRY_API_KEY, VISION_MODEL (or QWEN_VISION_MODEL).
Prints the base_url + model (non-secret) and a one-line OK/error — never the token.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    base = os.environ.get("TRUEFOUNDRY_BASE_URL")
    key = os.environ.get("TRUEFOUNDRY_API_KEY")
    model = (os.environ.get("VISION_MODEL")
             or os.environ.get("QWEN_VISION_MODEL")
             or "qwen2.5-vl-72b-instruct")

    missing = [k for k, v in {"TRUEFOUNDRY_BASE_URL": base, "TRUEFOUNDRY_API_KEY": key}.items() if not v]
    if missing:
        print("MISSING env:", ", ".join(missing))
        print("Add them to .env  (base_url + token; token is the part you already have).")
        return

    print(f"base_url = {base}")
    print(f"model    = {model}")
    print("token    = (set, hidden)")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base, api_key=key)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=5,
                temperature=0,
            ),
            timeout=60,
        )
        print("GATEWAY OK ->", repr(resp.choices[0].message.content))
    except Exception as e:
        print("GATEWAY ERROR:", type(e).__name__, "-", str(e)[:600])


if __name__ == "__main__":
    asyncio.run(main())
