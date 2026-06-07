"""Closed-set identify via an injected OpenAI-compatible gateway client.

Perception holds **no provider SDK and no vendor name**: it calls the standard chat-completions
shape with a **logical model name** through the gateway client passed in (the TrueFoundry AI
Gateway, seam S5). Swapping the vision model is a gateway/config change, not an edit here (§7).

Sends the customer frame(s) + each product's **labeled reference photo** so the model does an
image-to-image closed-set match, and extracts the problem in the same call.
"""

from __future__ import annotations

import base64
import json
import re

from src.contracts import CatalogEntry
from src.perception.providers.base import build_prompt


def _data_url(b: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(b).decode("ascii")


def _extract_json(text: str) -> dict:
    """Parse a JSON object, tolerating ```json fences or surrounding prose."""
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


class QwenGatewayProvider:
    """A `VisionProvider` backed by an injected OpenAI-compatible client (the TF gateway).

    `gateway_client` exposes `.chat.completions.create(...)` (OpenAI shape); `model` is the
    **logical** gateway task name (e.g. `route("vision.identify")`) — Qwen-VL by default, but the
    caller decides. Perception never imports `openai` or names a vendor.
    """

    def __init__(self, gateway_client, *, model: str):
        self.client = gateway_client          # OpenAI-compatible; constructed by app.py (S5), not here
        self.model = model                    # logical gateway name, resolved by route()

    async def identify(self, images: list[bytes], catalog: list[CatalogEntry]) -> dict:
        content: list[dict] = [
            {"type": "text", "text": build_prompt(catalog)},
            {"type": "text", "text": "CUSTOMER DEVICE photo(s):"},
        ]
        for b in images:
            content.append({"type": "image_url", "image_url": {"url": _data_url(b)}})

        refs = [c for c in catalog if c.ref_image]
        if refs:
            content.append({"type": "text", "text": "REFERENCE PHOTOS (one per product):"})
            for c in refs:
                content.append({"type": "text", "text": f"product_id={c.product_id} ({c.brand} {c.name}):"})
                content.append({"type": "image_url", "image_url": {"url": _data_url(c.ref_image)}})

        import asyncio
        resp = await asyncio.to_thread(
            self.client.chat_completion,
            task="vision.identify",
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return _extract_json(resp["choices"][0]["message"]["content"])

    async def look(self, images: list[bytes], question: str) -> str:
        """Free-form VQA: answer the customer's spoken question from what's ACTUALLY in the frame.
        Returns one concise spoken-style sentence describing the relevant visible condition (damage,
        indicators, ports, screen state) — or that it can't see the relevant part. No JSON, no docs."""
        sys_text = (
            "You are a live video support agent looking at the customer's device through their camera. "
            "Answer ONLY from what you can actually see in the image(s). Describe the relevant visible "
            "condition — damage, cracks, indicator lights, ports, screen state — in ONE short, natural "
            "spoken sentence. If you cannot clearly see the part the question is about, say so plainly. "
            "Do not invent details or give repair steps; just report what you see."
        )
        content: list[dict] = [
            {"type": "text", "text": f"Customer asked: {question}"},
        ]
        for b in images:
            content.append({"type": "image_url", "image_url": {"url": _data_url(b)}})
        import asyncio
        resp = await asyncio.to_thread(
            self.client.chat_completion,
            task="vision.identify",
            messages=[{"role": "system", "content": sys_text},
                      {"role": "user", "content": content}],
            temperature=0,
            max_tokens=120,
        )
        return (resp["choices"][0]["message"]["content"] or "").strip()
