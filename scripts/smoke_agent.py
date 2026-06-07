"""S4 integration smoke — ConversationAgent (04) wired to Fardin's REAL retrieval (03).

No agent-side mocks: the agent calls Fardin's `make_retrieval()` (LocalCosineRetriever +
template_compose, keyless/deterministic) over its shipped printer-manual corpus. Proves the
reconciled S4 seam end-to-end: async `retrieve_for(RetrievalQuery)` + `compose(answer_query, chunks)`,
grounding/refusal, modality escalation, and See→scope.

If TRUEFOUNDRY creds + REASON_MODEL are set, also runs the MiniMax (gateway_compose) path.

    uv run python scripts/smoke_agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv          # noqa: E402
load_dotenv()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from contracts import (                                  # noqa: E402
    Candidate, CatalogEntry, IdentifyResult, ProblemSignals, SupportSession,
)
from agent import ConversationAgent                     # noqa: E402
from retrieval import make_retrieval                    # noqa: E402  (Fardin's S4)

# A realistic vision result (what live Qwen emits) — a confident match + real problem signals.
SEE_RESULT = IdentifyResult(
    product_id="lj-m404", confidence=0.95, model_source="visual_match",
    candidates=[Candidate("lj-m404", 0.95)], needs_confirmation=False,
    problem=ProblemSignals(summary="paper jam", indicators=["paper stuck"], parts=["rear tray"]),
)

# Catalog aligned to Fardin's LocalCosineRetriever default corpus (product_id "lj-m404").
CATALOG = [CatalogEntry(product_id="lj-m404", name="LaserJet Pro M404", brand="HP")]


async def show(label, coro, agent):
    ans = await coro
    print(f"\n### {label}")
    if ans is None:
        print("  (no spoken turn)")
    else:
        print("  reply :", " ".join(ans.text.split())[:240])
        print("  cites :", [c.get("doc") for c in ans.citations] or "— (refusal/none)")
    print("  phase :", agent.s.phase, "| product:", agent.s.product_id, "| modality:", agent.s.modality)


async def run(label, retrieve_for, compose):
    print("\n" + "=" * 70 + f"\n{label}\n" + "=" * 70)
    agent = ConversationAgent(SupportSession("s1", "demo", modality="type"),
                              retrieve_for, compose, catalog=CATALOG)

    await show("1. paper jam (grounded, physical → See nudge)",
               agent.on_user_turn("the printer has a paper jam, how do I clear it?"), agent)
    await show("2. error code lookup",
               agent.on_user_turn("what does error 13 mean?"), agent)
    await show("3. out-of-docs (should refuse, offer See/human)",
               agent.on_user_turn("can it print in full color duplex?"), agent)
    await show("4. See → identify lj-m404 → scoped grounded fix",
               agent.on_identify(SEE_RESULT), agent)
    await show("5. resolution",
               agent.on_user_turn("that fixed it, thank you!"), agent)
    print("\n  resolved:", agent.is_resolved())


async def main() -> None:
    # ---- keyless path: LocalCosineRetriever + template_compose (deterministic) ----
    retrieve_for, compose = make_retrieval()
    await run("A) KEYLESS  — Fardin's local-cosine retrieve + template compose", retrieve_for, compose)

    # ---- gateway path: same retrieve, MiniMax gateway_compose ----
    if os.environ.get("TRUEFOUNDRY_BASE_URL") and os.environ.get("REASON_MODEL"):
        from openai import AsyncOpenAI
        from config import Config
        cfg = Config.from_env()
        cfg.reason_model = os.environ["REASON_MODEL"]          # real gateway model id
        gw = AsyncOpenAI(base_url=os.environ["TRUEFOUNDRY_BASE_URL"],
                         api_key=os.environ["TRUEFOUNDRY_API_KEY"])
        retrieve_for2, compose2 = make_retrieval(cfg, gw)
        await run(f"B) GATEWAY — local-cosine retrieve + MiniMax compose ({cfg.reason_model})",
                  retrieve_for2, compose2)
    else:
        print("\n(skipping gateway path — set TRUEFOUNDRY_BASE_URL + REASON_MODEL to run MiniMax compose)")


if __name__ == "__main__":
    asyncio.run(main())
