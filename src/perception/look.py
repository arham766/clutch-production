"""look(frames, question) -> str — free-form visual question answering (VQA).

The "See" counterpart to identify(): instead of closed-set classifying the product, this answers
the customer's spoken question from what's ACTUALLY visible in the live frame ("is this cracked?",
"what's wrong with the lens?", "does this look ok?"). Returns a short spoken-style observation.

Pure seam discipline (mirrors identify.py): the vision call goes through an injected provider; this
module names no vendor and imports no SDK. The Agent (HLD 04) decides WHEN to look and how to ground
repair steps on top of the observation.
"""

from __future__ import annotations

from perception.frames import select_best_frames
from perception.providers.base import VisionProvider


async def look(
    frames: list[bytes],
    question: str,
    *,
    provider: VisionProvider,
    k: int = 1,
) -> str:
    """Pick the best frame(s) and ask the vision provider what it sees relevant to `question`."""
    best = select_best_frames(frames, k=k)
    if not best:
        return ""
    try:
        return (await provider.look(best, question) or "").strip()
    except Exception:
        return ""


def make_look(gateway_client, *, model: str, k: int = 1):
    """Bind the real Qwen-via-gateway provider into a ready `look(frames, question)` callable.

    Wired by src/app.py for the real path (same gateway client + vision model as identify)."""
    from perception.providers.qwen_gateway import QwenGatewayProvider

    provider = QwenGatewayProvider(gateway_client, model=model)

    async def _look(frames: list[bytes], question: str) -> str:
        return await look(frames, question, provider=provider, k=k)

    return _look
