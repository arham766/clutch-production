"""identify(frames, catalog) -> IdentifyResult.

When the customer hits "See": pick the best frame(s), ask the vision provider to closed-set
classify the product (against the company catalog + its reference photos) and extract the problem,
then normalize into an IdentifyResult. Pure: confidence gating is here; the *ask-the-customer*
decision (when needs_confirmation) belongs to the Agent (HLD 04).
"""

from __future__ import annotations

from contracts import Candidate, CatalogEntry, IdentifyResult, ProblemSignals
from perception.frames import select_best_frames
from perception.providers.base import VisionProvider

ACCEPT = 0.80              # >= this and a single clear winner → resolved (no confirmation)
AMBIGUOUS_MARGIN = 0.15   # top-2 within this → ambiguous → confirm


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


_VALID_SOURCES = {"ocr_label", "visual_match"}


def _resolve_source(reported, pid, raw_text: str) -> str:
    """How the product was identified. Don't trust the VLM's self-report blindly — it often omits the
    field, leaving a confident match labelled "none" (self-contradictory). Infer from the evidence we
    already have; honour a valid self-report only when our own signal is ambiguous.

    - no in-catalog match → "none" (regardless of what the model claimed)
    - matched + OCR text present → "ocr_label"  (it read a label/screen)
    - matched + no OCR text     → "visual_match" (it matched appearance/ref photo)
    """
    if pid is None:
        return "none"
    if raw_text.strip():
        return "ocr_label"
    reported = str(reported or "").strip().lower()
    return reported if reported in _VALID_SOURCES else "visual_match"


def _parse_problem(d) -> ProblemSignals:
    d = d or {}
    lst = lambda k: [str(x) for x in (d.get(k) or [])]
    return ProblemSignals(
        error_codes=lst("error_codes"),
        indicators=lst("indicators"),
        parts=lst("parts"),
        damage=lst("damage"),
        summary=str(d.get("summary", "")),
    )


async def identify(
    frames: list[bytes],
    catalog: list[CatalogEntry],
    *,
    provider: VisionProvider,
    k: int = 2,
    accept: float = ACCEPT,
) -> IdentifyResult:
    catalog_ids = {c.product_id for c in catalog}

    best = select_best_frames(frames, k=k)
    if not best:
        return IdentifyResult(
            product_id=None, confidence=0.0, model_source="none",
            needs_confirmation=True,
            problem=ProblemSignals(summary="no usable camera frame"),
        )

    raw = await provider.identify(best, catalog) or {}

    pid = raw.get("product_id")
    if pid not in catalog_ids:        # reject anything not in the company's closed set
        pid = None
    conf = _clamp01(raw.get("confidence", 0.0))

    candidates = [
        Candidate(c["product_id"], _clamp01(c.get("confidence", 0.0)))
        for c in (raw.get("candidates") or [])
        if isinstance(c, dict) and c.get("product_id") in catalog_ids
    ]
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    ambiguous = (
        len(candidates) >= 2
        and (candidates[0].confidence - candidates[1].confidence) < AMBIGUOUS_MARGIN
    )

    needs_confirmation = pid is None or conf < accept or ambiguous

    return IdentifyResult(
        product_id=pid,
        confidence=conf,
        model_source=_resolve_source(raw.get("model_source"), pid, str(raw.get("raw_text", ""))),
        brand=str(raw.get("brand", "")),
        model=str(raw.get("model", "")),
        raw_text=str(raw.get("raw_text", "")),
        candidates=candidates,
        needs_confirmation=needs_confirmation,
        problem=_parse_problem(raw.get("problem")),
    )


def make_identify(gateway_client, *, model: str, k: int = 2, accept: float = ACCEPT):
    """Bind the real Qwen-via-gateway provider into a ready `identify(frames, catalog)` callable.

    This is what `src/app.py` wires in for the real path (`make_identify(gateway)`); the mock path
    just calls `identify(..., provider=MockVisionProvider())`. The vision model is swapped by the
    `model` logical name — no change here (§7).
    """
    from perception.providers.qwen_gateway import QwenGatewayProvider

    provider = QwenGatewayProvider(gateway_client, model=model)

    async def _identify(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult:
        return await identify(frames, catalog, provider=provider, k=k, accept=accept)

    return _identify
