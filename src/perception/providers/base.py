"""The VisionProvider protocol + the shared prompt/JSON contract every provider returns."""

from __future__ import annotations

from typing import Protocol

from contracts import CatalogEntry


class VisionProvider(Protocol):
    async def identify(self, images: list[bytes], catalog: list[CatalogEntry]) -> dict:
        """Return a raw dict matching RESPONSE_SCHEMA (validated/normalized by `identify`)."""
        ...

    async def look(self, images: list[bytes], question: str) -> str:
        """Free-form VQA: answer `question` from what's visible. Returns a short spoken sentence."""
        ...


# The structured JSON every provider must return (normalized by perception.identify).
RESPONSE_SCHEMA = """Return ONLY a JSON object with this exact shape:
{
  "product_id": <one of the catalog ids below, or null if none match>,
  "candidates": [{"product_id": <catalog id>, "confidence": <0..1>}],   // ranked, best first
  "brand": <string>,
  "model": <string>,
  "model_source": "ocr_label" | "visual_match" | "none",
  "raw_text": <verbatim text read off any model/serial label, or "">,
  "confidence": <0..1 for the chosen product_id>,
  "problem": {
    "error_codes": [<string>],
    "indicators":  [<string>],   // e.g. "amber blinking light"
    "parts":       [<string>],   // e.g. "rear paper tray"
    "damage":      [<string>],
    "summary":     <string>
  }
}"""


def build_prompt(catalog: list[CatalogEntry]) -> str:
    """Build the closed-set identify+diagnose prompt for a company's catalog.

    Reference photos (one per product, uploaded by the company) are sent as separate labeled
    images by the provider; this prompt tells the model to match against them. Products without a
    reference photo fall back to name/description + the model label.
    """
    lines = [
        "You are a hardware-support vision assistant. You are shown the CUSTOMER DEVICE photo(s),",
        "then a set of REFERENCE PHOTOS — one per product in the company's catalog, each labeled",
        "with its product_id. Two jobs:",
        "(1) Decide WHICH catalog product the customer's device is — match it against the reference",
        "    photos AND any model number you can read off a label. Choose a product_id from the",
        "    closed set below, or null if none match.",
        "(2) DESCRIBE the problem you can see (indicator lights, error codes, ports/parts, damage).",
        "Do NOT give repair advice — only identify and describe.",
        "",
        "Company product catalog (pick product_id from these, or null):",
    ]
    for c in catalog:
        alias = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        desc = f" — {c.description}" if c.description else ""
        photo = " [reference photo provided]" if c.ref_image else " [no reference photo]"
        lines.append(f"  - id={c.product_id}: {c.brand} {c.name}{alias}{desc}{photo}".rstrip())
    lines += ["", RESPONSE_SCHEMA]
    return "\n".join(lines)
