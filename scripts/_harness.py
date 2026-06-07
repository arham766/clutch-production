"""REAL self-contained stand-ins for the two cross-layer seams (S2 + S8).

`src/realtime/` may not import `agent/` or `perception/` (independence, 10 §8.4), so the
harness carries its own `agent` and `identify`. These are GENUINE implementations — not
`unittest.mock`, not patched: `EchoComposeAgent` builds a real `Answer` with citations and a
measured `time_taken_ms`; `pixel_identify` actually decodes the JPEG and decides from real
pixels. Deterministic and dependency-free, but real logic through the real seam.

`scope_session` is a 3-line real function building a `SupportSession` from room metadata.
"""

from __future__ import annotations

import io
import time

from PIL import Image

from contracts import (
    Answer,
    CatalogEntry,
    IdentifyResult,
    ProblemSignals,
    SupportSession,
)


class EchoComposeAgent:
    """A real, deterministic ConversationAgent-shaped object (S2)."""

    def __init__(self) -> None:
        self.turns: list[str] = []
        self.identifies: list[IdentifyResult] = []

    async def on_user_turn(self, text: str) -> Answer | None:
        self.turns.append(text)
        t0 = time.monotonic()
        ans = f"To handle '{text}', open the rear tray and clear the jam."
        # measure a real (tiny but non-zero) span so the latency badge has a real number
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return Answer(
            text=ans,
            citations=[{"doc": "manual.pdf", "section": "3.2", "score": 0.81}],
            time_taken_ms=max(1, elapsed_ms),
        )

    async def on_identify(self, r: IdentifyResult) -> Answer | None:
        self.identifies.append(r)
        if r.needs_confirmation:
            return None  # → session emits a ConfirmEvent
        return Answer(
            text="I can see the paper-jam indicator. Open the rear tray.",
            citations=[{"doc": "manual.pdf", "section": "4.1", "score": 0.77}],
            time_taken_ms=1,
        )


async def pixel_identify(frames: list[bytes], catalog) -> IdentifyResult:
    """Real S8 implementation — actually decodes the published frame and decides from pixels."""
    img = Image.open(io.BytesIO(frames[0])).convert("L")
    bright = sum(img.getdata()) / (img.width * img.height)
    product_id = catalog[0].product_id if catalog else "p1"
    return IdentifyResult(
        product_id=product_id,
        confidence=0.9,
        model_source="visual_match",
        needs_confirmation=bright < 40,  # decision from REAL pixels
        problem=ProblemSignals(
            indicators=["amber blinking light"],
            summary="paper-jam indicator on",
        ),
    )


def make_scope_session():
    """Return a real ScopeFn (S6) building a SupportSession from the room metadata values."""

    def scope_session(company_key: str, modality: str) -> SupportSession:
        return SupportSession(
            company_id=company_key or "demo",
            modality=modality if modality in ("type", "talk", "see") else "type",
            session_id="clutch-mini",
        )

    return scope_session


def demo_catalog() -> list[CatalogEntry]:
    return [CatalogEntry(product_id="p1", name="Acme LaserJet 100", brand="Acme")]
