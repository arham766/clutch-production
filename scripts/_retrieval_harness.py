"""Mini stand-ins for the retrieval e2e harness (LLD 03 §12.5).

These are *mini layers* (real-shaped logic over the seam), not mocks:
  - mini_agent: builds a RetrievalQuery, calls retrieve_for, then compose, prints the Answer.
  - mini_gateway: a fake OpenAI-compatible client that drives the REAL gateway_compose path.
  - canned_identify: a fixed IdentifyResult for the See path.
  - the clutch-demo fixture corpus served by LocalCosineRetriever.

Harness-only: lives in scripts/, never in src/retrieval/. Imports only the Limb + Spine.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Put src/ on the path (matches pyproject pythonpath=["src"]); import only Spine + Limb.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from contracts import Chunk, IdentifyResult, ProblemSignals, RetrievalQuery  # noqa: E402


# ── clutch-demo fixture corpus (printer manual snippets, product-tagged) ─────
def fixture_corpus() -> list[Chunk]:
    rows = [
        (
            "lj-m404-manual-p012-0001",
            "To clear a paper jam, power off the printer, open the rear access door, and gently "
            "pull the jammed sheet straight out. Do not tear the paper. Close the door and power on.",
            {"source": "lj-m404-manual.pdf", "section": "Clearing Paper Jams", "page": "12",
             "product_id": "lj-m404"},
        ),
        (
            "lj-m404-manual-p045-0002",
            "Error 13 indicates a paper jam in the fuser area. Turn off the device, wait ten "
            "minutes for the fuser to cool, then remove any caught paper from the rear of the unit.",
            {"source": "lj-m404-manual.pdf", "section": "Error Codes", "page": "45",
             "product_id": "lj-m404"},
        ),
        (
            "lj-m404-manual-p008-0003",
            "Load paper into the main tray with the print side down. Adjust the guides to the paper "
            "width. Overfilling the tray above the max line can cause misfeeds and jams.",
            {"source": "lj-m404-manual.pdf", "section": "Loading Paper", "page": "8",
             "product_id": "lj-m404"},
        ),
        (
            "lj-m404-manual-p050-0005",
            "Replace the toner cartridge when the low toner indicator appears. Open the front cover, "
            "remove the spent cartridge, and slide the new cartridge in until it clicks.",
            {"source": "lj-m404-manual.pdf", "section": "Replacing Toner", "page": "50",
             "product_id": "lj-m404"},
        ),
        (
            "warranty-p002-0004",
            "All devices carry a one year limited warranty covering manufacturing defects. The "
            "warranty does not cover damage from paper jams caused by non-approved media.",
            {"source": "warranty.pdf", "section": "Warranty Terms", "page": "2",
             "product_id": ""},
        ),
    ]
    return [
        Chunk(id=cid, text=text, metadata=meta, source=meta.get("source", ""))
        for cid, text, meta in rows
    ]


# ── canned_identify (the See path stand-in, S3/S8) ──────────────────────────
def canned_identify() -> IdentifyResult:
    return IdentifyResult(
        product_id="lj-m404",
        confidence=0.95,
        model_source="visual_match",
        brand="Acme",
        model="LaserJet M404",
        problem=ProblemSignals(
            error_codes=[],
            indicators=["jammed paper visible"],
            parts=["rear paper tray"],
            summary="paper jam",
        ),
    )


# ── mini_gateway (a fake OpenAI-compatible client, S5) ──────────────────────
class _Msg:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, scripted):
        self._scripted = scripted

    async def create(self, *, model, messages, **kw):
        # The scripted responder may inspect the built prompt to decide what to return.
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content", "")
        return _Completion(self._scripted(user))


class _Chat:
    def __init__(self, scripted):
        self.completions = _Completions(scripted)


class MiniGateway:
    """Fake OpenAI-compatible client. `mode` scripts the completion gateway_compose receives."""

    def __init__(self, mode: str = "cited"):
        self.mode = mode
        self.chat = _Chat(self._respond)

    def _respond(self, user_prompt: str) -> str:
        if self.mode == "not_in_docs":
            return "NOT_IN_DOCS"
        if self.mode == "uncited":
            # a sentence with no [n] marker → must be dropped by gateway_compose
            return "This printer is great and you should feel great about it."
        # default "cited": cite source [1] (and [2] if present)
        has_two = "[2]" in user_prompt
        if has_two:
            return ("Power off the printer and open the rear door [1]. "
                    "Then gently pull the jammed sheet straight out [2].")
        return "Power off the printer and clear the jam from the rear door [1]."


def mini_gateway(mode: str = "cited") -> MiniGateway:
    return MiniGateway(mode)


# ── a breakable retriever wrapper (Scenario 4: Moss down) ───────────────────
class BreakingRetriever:
    """A Retriever whose search() always raises — exercises the public retrieve() fallback."""

    async def load_index(self, company_id: str) -> str:
        raise RuntimeError("moss unreachable (simulated)")

    async def search(self, company_id, text, product_id, top_k):
        raise RuntimeError("moss unreachable (simulated)")


# ── a load-count wrapper (Scenario 6: load-once) ────────────────────────────
class CountingRetriever:
    """Wraps a real backend, counting underlying load_index calls (for the load-once check)."""

    def __init__(self, inner):
        self._inner = inner
        self.load_calls = 0
        self._loaded: set[str] = set()
        import asyncio

        self._locks: dict[str, asyncio.Lock] = {}

    async def load_index(self, company_id: str) -> str:
        import asyncio

        name = f"clutch-{company_id}"
        if name in self._loaded:
            return name
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if name not in self._loaded:
                self.load_calls += 1
                await self._inner.load_index(company_id)
                self._loaded.add(name)
        return name

    async def search(self, company_id, text, product_id, top_k):
        await self.load_index(company_id)
        return await self._inner.search(company_id, text, product_id, top_k)


# ── mini_agent (the caller, S4) ─────────────────────────────────────────────
async def mini_agent(query: RetrievalQuery, retrieve_for_fn, compose_fn, *, verbose=False):
    """The ~exact two-callable flow the real Agent runs. Returns (chunks, answer)."""
    chunks = await retrieve_for_fn(query)
    answer = compose_fn(query.text, chunks)
    # compose may be sync (template) or async (gateway-backed)
    if hasattr(answer, "__await__"):
        answer = await answer
    if verbose:
        print(f"    query: {query.text!r} product_id={query.product_id}")
        for c in chunks:
            print(f"    chunk {c.id} score={c.score:.3f} section={c.metadata.get('section')!r}")
        print(f"    answer.text: {answer.text!r}")
        print(f"    citations: {answer.citations}")
    return chunks, answer
