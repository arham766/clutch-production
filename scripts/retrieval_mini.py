"""Retrieval & Grounding e2e harness (LLD 03 §12) — Section D's acceptance gate.

One command drives the whole read+ground path the way the Agent will, with mini stand-ins for
every neighbour. Imports only src/retrieval/ (code under test) + src/contracts.py (Spine).

Modes (escalating fidelity):
  (default / mock)   uv run python -m scripts.retrieval_mini --check
  gateway            ... --check --real-compose   (mini_gateway drives real gateway_compose)
  live               ... --check --live           (real MossRetriever; keyless => SKIP, exit 0)

A bare `--query "..."` runs one turn (the dev run-loop). `--check` runs the scenario battery and
exits non-zero on any failure. `--verbose` dumps the full chunks/answer/citations.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Permanent improvement: load .env.local so --live / --real-compose work without a manual
# `source .env.local`. Real keys (Moss + OpenRouter) come from here; never printed.
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env.local")
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from contracts import RetrievalQuery  # noqa: E402

import retrieval  # noqa: E402
from retrieval import (  # noqa: E402
    LocalCosineRetriever,
    MossRetriever,
    gateway_compose,
    make_retrieval,
    template_compose,
)

from scripts._retrieval_harness import (  # noqa: E402
    BreakingRetriever,
    CountingRetriever,
    canned_identify,
    fixture_corpus,
    mini_agent,
    mini_gateway,
)

COMPANY = os.environ.get("CLUTCH_COMPANY", "demo")  # index = clutch-<COMPANY>

# Force UTF-8 on the console where possible (Windows cp1252 can't encode the marks).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

PASS = "PASS"
FAIL = "FAIL"


def _local():
    return LocalCosineRetriever(fixture_corpus())


def _real_gateway_client():
    """Build a real OpenAI-compatible client for OpenRouter, or None if env is absent.

    Used by --real-compose so gateway_compose drives the actual REASON_MODEL through the
    injected client (same seam the Agent uses via make_retrieval)."""
    base = os.environ.get("OPENROUTER_BASE_URL")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not (base and key):
        return None, None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base, api_key=key)
    model = os.environ.get("REASON_MODEL", "minimax/minimax-m2")
    return client, model


def _citations_resolve(chunks, answer) -> bool:
    ids = {(c.metadata.get("source", c.source), c.metadata.get("section", "")) for c in chunks}
    for cit in answer.citations:
        if (cit["doc"], cit["section"]) not in ids:
            return False
    return True


async def _scenario_grounded(verbose, real_compose):
    backend = _local()
    q = RetrievalQuery(company_id=COMPANY, text="printer shows error 13")

    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=backend, _fallback=backend)

    if real_compose:
        gw = mini_gateway("cited")
        comp = lambda aq, ch: gateway_compose(aq, ch, client=gw, model="reason.compose")
    else:
        comp = template_compose
    chunks, answer = await mini_agent(q, retr, comp, verbose=verbose)
    ok = bool(chunks) and any(c.score >= 0.35 for c in chunks) and bool(answer.text)
    ok = ok and _citations_resolve(chunks, answer)
    return ok, f"{len(chunks)} chunks, answer {len(answer.text)} chars, {len(answer.citations)} citations"


async def _scenario_see_filtered(verbose, real_compose):
    ident = canned_identify()
    q = RetrievalQuery(
        company_id=COMPANY,
        text=ident.problem.to_query(),
        product_id=ident.product_id,
    )
    backend = _local()

    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=backend, _fallback=backend)

    chunks, answer = await mini_agent(q, retr, template_compose, verbose=verbose)
    ok = bool(chunks) and all(c.metadata.get("product_id") == "lj-m404" for c in chunks)
    return ok, f"{len(chunks)} chunks, all product_id==lj-m404: {ok}"


async def _scenario_refusal(verbose, real_compose):
    backend = _local()
    q = RetrievalQuery(company_id=COMPANY, text="how do I file my quarterly taxes in spain")

    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=backend, _fallback=backend)

    if real_compose:
        gw = mini_gateway("not_in_docs")
        comp = lambda aq, ch: gateway_compose(aq, ch, client=gw, model="reason.compose")
    else:
        comp = template_compose
    chunks, answer = await mini_agent(q, retr, comp, verbose=verbose)
    ok = answer.is_refusal
    return ok, f"is_refusal={answer.is_refusal} (text {len(answer.text)} chars)"


async def _scenario_fallback(verbose, real_compose):
    # primary raises → public retrieve() must fall through to local-cosine fallback.
    primary = BreakingRetriever()
    fallback = _local()
    q = RetrievalQuery(company_id=COMPANY, text="printer shows error 13")

    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=primary, _fallback=fallback)

    chunks, answer = await mini_agent(q, retr, template_compose, verbose=verbose)
    ok = bool(chunks) and bool(answer.text)
    return ok, f"served by fallback: {len(chunks)} chunks despite primary error"


async def _scenario_widen(verbose, real_compose):
    # filter on a product that has no doc for this term → company-wide re-query surfaces warranty.
    backend = _local()
    q = RetrievalQuery(
        company_id=COMPANY,
        text="warranty coverage for manufacturing defects",
        product_id="lj-m404",  # warranty chunk has product_id="" → filtered out on first pass
    )

    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=backend, _fallback=backend)

    chunks, answer = await mini_agent(q, retr, template_compose, verbose=verbose)
    ok = any(c.metadata.get("source") == "warranty.pdf" for c in chunks)
    return ok, f"widened company-wide, surfaced warranty.pdf: {ok}"


async def _scenario_load_once(verbose, real_compose):
    counting = CountingRetriever(_local())
    # two concurrent first-touches of the same company
    await asyncio.gather(
        counting.load_index(COMPANY),
        counting.load_index(COMPANY),
        counting.search(COMPANY, "error 13", None, 5),
    )
    ok = counting.load_calls == 1
    return ok, f"underlying load_index called {counting.load_calls}x (expected 1)"


async def _scenario_gateway_compose(verbose, real_compose):
    backend = _local()
    q = RetrievalQuery(company_id=COMPANY, text="how do I clear a paper jam")
    chunks = await retrieval.retrieve_for(q, _primary=backend, _fallback=backend)

    # cited completion → citations populated
    gw = mini_gateway("cited")
    cited = await gateway_compose(q.text, chunks, client=gw, model="reason.compose")
    ok_cited = bool(cited.text) and len(cited.citations) >= 1

    # NOT_IN_DOCS → refusal
    gw2 = mini_gateway("not_in_docs")
    refused = await gateway_compose(q.text, chunks, client=gw2, model="reason.compose")
    ok_refuse = refused.is_refusal

    # un-cited sentence → dropped → falls back to template_compose (still grounded, never invents)
    gw3 = mini_gateway("uncited")
    dropped = await gateway_compose(q.text, chunks, client=gw3, model="reason.compose")
    ok_drop = "feel great" not in dropped.text  # the invented sentence must not survive

    ok = ok_cited and ok_refuse and ok_drop
    if verbose:
        print(f"    cited.citations={cited.citations}")
        print(f"    refused.is_refusal={refused.is_refusal}")
        print(f"    dropped.text={dropped.text!r}")
    return ok, f"cited={ok_cited} refuse={ok_refuse} drop-uncited={ok_drop}"


async def _scenario_latency(verbose, real_compose):
    # fixture path → last_latency_ms is None (badge hidden). (Live path asserts a real number.)
    backend = _local()
    last = getattr(backend, "last_latency_ms", None)
    ok = last is None
    badge = f"{last:.2f}ms" if last is not None else "(hidden)"
    return ok, f"latency badge: {badge}"


SCENARIOS = [
    ("Type/Talk grounded answer", _scenario_grounded),
    ("See product-filtered", _scenario_see_filtered),
    ("Refusal not-in-docs", _scenario_refusal),
    ("Fallback Moss-down", _scenario_fallback),
    ("Widen weak first pass", _scenario_widen),
    ("load-once concurrency", _scenario_load_once),
    ("gateway compose path", _scenario_gateway_compose),
    ("latency surfacing", _scenario_latency),
]


async def run_live(verbose: bool, real_compose: bool) -> int:
    """Live smoke against the real Moss index clutch-<COMPANY> (keyless => SKIP, exit 0).

    With --real-compose: also drives gateway_compose through a REAL OpenRouter client
    (REASON_MODEL), asserting a grounded+cited answer and a real not-in-docs refusal.
    """
    has_creds = bool(os.environ.get("MOSS_PROJECT_ID") and os.environ.get("MOSS_PROJECT_KEY"))
    if not has_creds:
        print("SKIP (no creds)")
        return 0

    retr = MossRetriever(
        os.environ["MOSS_PROJECT_ID"],
        os.environ["MOSS_PROJECT_KEY"],
        alpha=0.70,
        default_top_k=5,
    )

    # 1) live retrieve
    try:
        chunks = await retr.search(COMPANY, "printer shows error 13", None, 5)
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} live Moss query — {type(e).__name__}: {e}")
        return 1
    if not chunks:
        print(f"{FAIL} live Moss query — index clutch-{COMPANY} returned 0 chunks "
              f"(is it populated? run scripts.seed_clutch_demo)")
        return 1
    lat = getattr(retr, "last_latency_ms", None)
    lat_s = f"{lat:.2f}ms" if lat is not None else "(hidden)"
    print(f"{PASS} live Moss query — {len(chunks)} chunks, latency {lat_s}")

    if not real_compose:
        return 0

    # 2) live grounded compose via the REAL OpenRouter client (REASON_MODEL)
    client, model = _real_gateway_client()
    if client is None:
        print("SKIP real compose (no OPENROUTER_BASE_URL/OPENROUTER_API_KEY)")
        return 0

    failed = 0
    try:
        ans = await gateway_compose(
            "How do I fix error 13 on the printer?", chunks, client=client, model=model
        )
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} live compose (cited) — {type(e).__name__}: {e}")
        return 1
    cited_ok = bool(ans.text) and len(ans.citations) >= 1 and _citations_resolve(chunks, ans)
    print(f"{'PASS' if cited_ok else 'FAIL'} live compose (cited) — "
          f"text {len(ans.text)} chars, {len(ans.citations)} citations, model={model}")
    if verbose:
        print(f"    answer: {ans.text!r}")
        print(f"    citations: {ans.citations}")
    failed += 0 if cited_ok else 1

    # 3) real refusal: a question the docs cannot answer must refuse (empty text)
    try:
        ref = await gateway_compose(
            "How do I file my quarterly taxes in Spain?", chunks, client=client, model=model
        )
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} live compose (refusal) — {type(e).__name__}: {e}")
        return 1
    refuse_ok = ref.is_refusal
    print(f"{'PASS' if refuse_ok else 'FAIL'} live compose (refusal) — is_refusal={ref.is_refusal}")
    if verbose and not refuse_ok:
        print(f"    non-refusal text: {ref.text!r}")
    failed += 0 if refuse_ok else 1

    return 0 if failed == 0 else 1


async def run_check(verbose: bool, real_compose: bool, live: bool) -> int:
    print("== retrieval_mini --check ==")
    mode = "live" if live else ("gateway" if real_compose else "mock")
    print(f"mode: {mode}")

    if live:
        return await run_live(verbose, real_compose)

    passed = 0
    failed = 0
    for name, fn in SCENARIOS:
        try:
            ok, evidence = await fn(verbose, real_compose)
        except Exception as e:  # noqa: BLE001
            ok, evidence = False, f"raised {type(e).__name__}: {e}"
        mark = PASS if ok else FAIL
        print(f"{mark} {name} — {evidence}")
        if ok:
            passed += 1
        else:
            failed += 1

    total = passed + failed
    print(f"\n{passed}/{total} passed")
    return 0 if failed == 0 else 1


async def run_query(text: str, product_id, verbose: bool, real_compose: bool) -> int:
    backend = _local()
    q = RetrievalQuery(company_id=COMPANY, text=text, product_id=product_id)
    retrieve_for, compose = make_retrieval(None, mini_gateway("cited") if real_compose else None)

    # use the fixture-backed local backend for a deterministic dev run
    async def retr(query):
        return await retrieval.retrieve_for(query, _primary=backend, _fallback=backend)

    chunks, answer = await mini_agent(q, retr, compose if real_compose else template_compose, verbose=True)
    print(f"\nAnswer: {answer.text or '(refusal — not in docs)'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Retrieval & Grounding e2e harness (LLD 03 §12)")
    p.add_argument("--check", action="store_true", help="run the scenario battery (acceptance gate)")
    p.add_argument("--live", action="store_true", help="real Moss (keyless => SKIP, exit 0)")
    p.add_argument("--real-compose", action="store_true", help="drive real gateway_compose via mini_gateway")
    p.add_argument("--query", type=str, default=None, help="run one turn (dev run-loop)")
    p.add_argument("--product-id", type=str, default=None)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.check:
        return asyncio.run(run_check(args.verbose, args.real_compose, args.live))
    if args.query:
        return asyncio.run(run_query(args.query, args.product_id, args.verbose, args.real_compose))
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
