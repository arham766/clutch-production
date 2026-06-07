"""
Clutch mocks — stub implementations for teammate integration.

These let Tonmoy (C) and Fardin (D) build against Section B without
needing live Firebase, Moss, or TrueFoundry credentials.

Also used for unit testing throughout.

Reference: HLD 10 §7 (mocks for each seam).
"""

from __future__ import annotations

import uuid
from typing import Any

from src.contracts import (
    CatalogEntry,
    Chunk,
    Company,
    IdentifyResult,
    Modality,
    ProblemSignals,
    SupportSession,
)


# ---------------------------------------------------------------------------
# Stub tenancy — one fake company, always resolves
# ---------------------------------------------------------------------------

DEMO_COMPANY = Company(
    company_id="demo-company",
    name="Acme Hardware Demo",
    index_name="clutch-demo",
    catalog=[
        CatalogEntry(
            product_id="demo-printer",
            name="Acme Printer 3000",
            brand="Acme",
            aliases=["AP3000", "Printer 3000"],
            description="All-in-one laser printer",
        ),
        CatalogEntry(
            product_id="demo-router",
            name="Acme Router X1",
            brand="Acme",
            aliases=["ARX1"],
            description="Dual-band WiFi router",
        ),
    ],
    api_key="demo-key-12345",
)


def stub_resolve_company(company_key: str) -> Company:
    """Always returns the demo company."""
    return DEMO_COMPANY


def stub_scope_session(company_key: str, modality: Modality = "text") -> SupportSession:
    """Create a demo session."""
    return SupportSession(
        session_id=f"demo-sess-{uuid.uuid4().hex[:8]}",
        company_id=DEMO_COMPANY.company_id,
        modality=modality,
    )


# ---------------------------------------------------------------------------
# Mock gateway — canned completions
# ---------------------------------------------------------------------------

class MockGatewayClient:
    """Returns canned responses instead of calling TrueFoundry."""

    def chat_completion(self, *, task: str, messages: list[dict], **kw: Any) -> dict:
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": (
                        f"[MOCK] I'd help you with '{user_msg[:80]}' "
                        f"but I'm a mock gateway (task={task})."
                    ),
                },
                "finish_reason": "stop",
            }],
            "model": f"mock-{task}",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    def embeddings(self, *, texts: list[str], **kw: Any) -> list[list[float]]:
        """Return deterministic fake embeddings."""
        return [[0.1 * (i + 1)] * 128 for i in range(len(texts))]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Canned identify (for Tonmoy to build against)
# ---------------------------------------------------------------------------

def canned_identify(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult:
    """Always returns the first catalog entry with high confidence."""
    if not catalog:
        return IdentifyResult(
            product_id=None,
            confidence=0.0,
            model_source="none",
        )
    first = catalog[0]
    return IdentifyResult(
        product_id=first.product_id,
        confidence=0.95,
        model_source="mock",
        brand=first.brand,
        model=first.name,
        problem=ProblemSignals(
            indicators=["blinking red light"],
            summary="Mock problem: paper jam detected",
        ),
    )


# ---------------------------------------------------------------------------
# Canned retrieval (for Fardin to build against)
# ---------------------------------------------------------------------------

def canned_retrieve(query: str, index_name: str, product_id: str | None = None) -> list[Chunk]:
    """Return hardcoded chunks."""
    return [
        Chunk(
            id="mock-chunk-1",
            text=(
                "## Troubleshooting Paper Jams\n\n"
                "1. Open the rear access panel.\n"
                "2. Gently pull out any jammed paper.\n"
                "3. Close the panel and press Resume."
            ),
            metadata={
                "source": "user_guide_v2.pdf",
                "section": "Troubleshooting",
                "product_id": product_id or "unknown",
                "doc_type": "user_guide",
            },
            score=0.94,
            source="user_guide_v2.pdf",
        ),
        Chunk(
            id="mock-chunk-2",
            text=(
                "## Error Code E-01\n\n"
                "This error indicates a paper jam in the rear tray.\n"
                "Follow the paper jam troubleshooting steps."
            ),
            metadata={
                "source": "service_manual.pdf",
                "section": "Error Codes",
                "product_id": product_id or "unknown",
                "doc_type": "service_manual",
            },
            score=0.89,
            source="service_manual.pdf",
        ),
    ]
