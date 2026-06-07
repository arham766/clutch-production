"""Deterministic vision provider for tests/dev — no API keys needed."""

from __future__ import annotations

from typing import Callable, Optional

from src.contracts import Candidate, CatalogEntry, IdentifyResult, ProblemSignals


class MockVisionProvider:
    """Returns a preset raw dict (or computes one via `responder`).

    Default behavior: confidently match the first catalog product, with a sample problem — enough
    to exercise the happy path. Pass `response=` for a fixed dict or `responder=` for logic.
    """

    def __init__(
        self,
        response: Optional[dict] = None,
        responder: Optional[Callable[[list[bytes], list[CatalogEntry]], dict]] = None,
    ):
        self._response = response
        self._responder = responder
        self.calls: list[tuple[list[bytes], list[CatalogEntry]]] = []

    async def identify(self, images: list[bytes], catalog: list[CatalogEntry]) -> dict:
        self.calls.append((images, catalog))
        if self._responder is not None:
            return self._responder(images, catalog)
        if self._response is not None:
            return self._response
        first = catalog[0] if catalog else None
        pid = first.product_id if first else None
        return {
            "product_id": pid,
            "candidates": [{"product_id": pid, "confidence": 0.92}] if pid else [],
            "brand": first.brand if first else "",
            "model": first.name if first else "",
            "model_source": "visual_match",
            "raw_text": "",
            "confidence": 0.92,
            "problem": {
                "error_codes": [],
                "indicators": ["amber blinking light"],
                "parts": ["rear paper tray"],
                "damage": [],
                "summary": "paper-jam indicator is on",
            },
        }

    async def look(self, images: list[bytes], question: str) -> str:
        self.calls.append((images, []))
        return "I see an amber blinking light on the device."


def canned_identify(frames, catalog, *, product_id=None, confidence=0.92) -> IdentifyResult:
    """A fixed `IdentifyResult` (skips vision entirely) — what the Agent / realtime loop build
    against (work-division §5). Resolves to the first catalog product by default."""
    entry = next((c for c in catalog if c.product_id == product_id), None) or (catalog[0] if catalog else None)
    pid = entry.product_id if entry else None
    return IdentifyResult(
        product_id=pid,
        confidence=confidence,
        model_source="visual_match",
        brand=entry.brand if entry else "",
        model=entry.name if entry else "",
        candidates=[Candidate(pid, confidence)] if pid else [],
        needs_confirmation=pid is None,
        problem=ProblemSignals(indicators=["amber blinking light"], summary="(canned) problem signal"),
    )
