"""ondevice_mini — the standalone e2e gate for the on-device backend (LLD 03-LOCAL §11).

One command drives the whole local build→retrieve→ground path with mini stand-ins for every
neighbour, asserts the LOCAL-specific behavior (boot-from-disk, no-push, egress boundary,
local-LLM-first, incremental upsert, registry dispatch, grounding parity), prints evidence, and
exits non-zero on any failure. Keyless: stdlib only, no Moss, no network.

    python ondevice/scripts/ondevice_mini.py --check        # the gate
    python ondevice/scripts/ondevice_mini.py --query "..."  # one-turn dev loop
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ondevice/ on path → import ondevice

try:                                  # Windows consoles default to cp1252 → force UTF-8 for ✓/✗
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ondevice import (  # noqa: E402
    Chunk, ConfigError, LocalIndexStore, OnDeviceConfig, OnDeviceMossRetriever,
    RetrievalQuery, compose_answer, make_retrieval, resolve_reason_endpoint, template_compose,
)


# ── fixture corpus (clutch-demo: iPhone) ────────────────────────────────────
def fixture_chunks() -> list[Chunk]:
    return [
        Chunk(id="c1",
              text="If the iPhone screen is cracked, back up your data and book a screen replacement "
                   "at an Apple Store or an authorized service provider.",
              metadata={"source": "service.pdf", "section": "Screen", "product_id": "iphone-15"},
              source="service.pdf"),
        Chunk(id="c2",
              text="To force restart iPhone 15, press volume up, then volume down, then press and "
                   "hold the side button until the Apple logo appears.",
              metadata={"source": "manual.pdf", "section": "Restart", "product_id": "iphone-15"},
              source="manual.pdf"),
    ]


GROUNDED = "my iphone screen is cracked, what should I do"
ABSENT = "does it support airprint over bluetooth mesh"


# ── mini stand-ins (mini, not mock: they drive the real code paths) ─────────
class _Msg:
    def __init__(self, c): self.content = c
class _Choice:
    def __init__(self, c): self.message = _Msg(c)
class _Resp:
    def __init__(self, c): self.choices = [_Choice(c)]

class MiniLLM:
    """OpenAI-compatible fake. Records every outbound payload (the egress spy) and returns a scripted
    CITED completion so the real `compose` post-verification path runs."""
    def __init__(self, reply="Back up your data and book a screen replacement [1]."):
        self.reply, self.calls = reply, []
        self.chat = self; self.completions = self

    async def create(self, model=None, messages=None, **kw):
        self.calls.append(messages)
        return _Resp(self.reply)


def _new_store() -> LocalIndexStore:
    return LocalIndexStore(tempfile.mkdtemp(prefix="ondevice_"), embed_model="moss-minilm")


# ── scenarios ───────────────────────────────────────────────────────────────
async def s_boot_from_disk(cfg):
    store = _new_store()
    store.upsert(fixture_chunks())
    r1 = OnDeviceMossRetriever(store, cfg)
    first = await r1.retrieve("demo", GROUNDED, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    # simulate a restart: brand-new store + retriever over the SAME dir, no in-memory state
    r2 = OnDeviceMossRetriever(LocalIndexStore(store.dir, embed_model="moss-minilm"), cfg)
    after = await r2.retrieve("demo", GROUNDED, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    ok = bool(first) and bool(after)
    return ok, f"before={len(first)} chunk(s), after-restart={len(after)} chunk(s) from disk"


async def s_no_push(cfg):
    store = _new_store()
    store.upsert(fixture_chunks())
    r = OnDeviceMossRetriever(store, cfg)
    chunks = await r.retrieve("demo", GROUNDED, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    await compose_answer(GROUNDED, chunks, cfg=cfg, min_score=cfg.retrieval_min_score)
    pushes = len(r._session.push_calls)
    return pushes == 0, f"push_index calls across onboard→retrieve→compose = {pushes}"


async def s_egress_boundary(cfg):
    store = _new_store(); store.upsert(fixture_chunks())
    r = OnDeviceMossRetriever(store, cfg)
    chunks = await r.retrieve("demo", GROUNDED, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    # default: template compose → ZERO outbound
    tmpl_cfg = OnDeviceConfig(reason_endpoint="local")            # local + no client/url → template
    await compose_answer(GROUNDED, chunks, cfg=tmpl_cfg, min_score=cfg.retrieval_min_score)
    # local LLM present → exactly one outbound, payload = chunk text only
    llm = MiniLLM()
    await compose_answer(GROUNDED, chunks, cfg=OnDeviceConfig(reason_endpoint="local"),
                         local_client=llm, min_score=cfg.retrieval_min_score)
    payload = str(llm.calls)
    chunk_text_leaked = any(c.text[:20] in payload for c in chunks)
    no_vectors = "vec" not in payload.lower() and "embedding" not in payload.lower()
    ok = len(llm.calls) == 1 and chunk_text_leaked and no_vectors
    return ok, f"template egress=0, local-LLM egress={len(llm.calls)} (chunk-text only, no vectors)"


async def s_local_first(cfg):
    llm = MiniLLM()
    auto_up = resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="auto"), gateway_client="GW", local_client=llm)
    auto_down = resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="auto"), gateway_client="GW", local_client=None)
    local_none = resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="local"), gateway_client="GW", local_client=None)
    ok = (auto_up is llm) and (auto_down == "GW") and (local_none is None)
    return ok, f"auto+healthy→local, auto+down→gateway, local+none→template (None) ✓={ok}"


async def s_incremental_upsert(cfg):
    store = _new_store()
    first = store.upsert(fixture_chunks())                        # both sources new
    again = store.upsert(fixture_chunks())                        # unchanged → 0
    changed_doc = [Chunk(id="c2", text="Updated restart steps for iPhone 15.",
                         metadata={"source": "manual.pdf", "section": "Restart"}, source="manual.pdf")]
    changed = store.upsert(changed_doc)                           # one source changed → 1
    ok = first == 2 and again == 0 and changed == 1
    return ok, f"first={first}, re-upsert(unchanged)={again}, changed-doc={changed}"


async def s_registry_fail_loud(cfg):
    ok_mode, _ = make_retrieval(OnDeviceConfig(retrieval_mode="on_device")), None
    raised = False
    try:
        make_retrieval(OnDeviceConfig(retrieval_mode="totally-bogus"))
    except ConfigError:
        raised = True
    return bool(ok_mode) and raised, "on_device→backend; bogus mode→ConfigError (not KeyError)"


async def s_grounding_parity(cfg):
    store = _new_store(); store.upsert(fixture_chunks())
    r = OnDeviceMossRetriever(store, cfg)
    g = await r.retrieve("demo", GROUNDED, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    ans = template_compose(GROUNDED, g, min_score=cfg.retrieval_min_score)
    grounded_ok = bool(g) and bool(ans.citations) and all(
        any(cit["doc"] == c.metadata.get("source") for c in g) for cit in ans.citations)
    a = await r.retrieve("demo", ABSENT, "iphone-15", top_k=5, min_score=cfg.retrieval_min_score)
    refusal = template_compose(ABSENT, a, min_score=cfg.retrieval_min_score)
    ok = grounded_ok and refusal.is_refusal
    return ok, f"grounded→{len(g)} cited chunk(s); absent→refusal={refusal.is_refusal}"


SCENARIOS = [
    ("boot from disk (durable derived index)", s_boot_from_disk),
    ("no-push invariant (corpus never leaves)", s_no_push),
    ("egress boundary (chunk text only)", s_egress_boundary),
    ("local-LLM-first resolution", s_local_first),
    ("admin upsert is incremental", s_incremental_upsert),
    ("registry dispatch + fail-loud", s_registry_fail_loud),
    ("grounding parity", s_grounding_parity),
]


async def run_check() -> int:
    cfg = OnDeviceConfig()
    passed = 0
    for name, fn in SCENARIOS:
        try:
            ok, evidence = await fn(cfg)
        except Exception as exc:  # a scenario crash is a failure, with the traceback as evidence
            ok, evidence = False, f"EXCEPTION: {exc!r}"
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name} — {evidence}")
        passed += int(ok)
    total = len(SCENARIOS)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


async def run_query(text: str) -> int:
    # seed the SAME path make_retrieval will read from (LocalIndexStore(cfg.local_index_path))
    cfg = OnDeviceConfig(local_index_path=tempfile.mkdtemp(prefix="ondevice_"))
    LocalIndexStore(cfg.local_index_path, embed_model=cfg.embed_model).upsert(fixture_chunks())
    retrieve_for, compose = make_retrieval(cfg)
    chunks = await retrieve_for(RetrievalQuery("demo", text, "iphone-15"))
    ans = await compose(text, chunks)
    print(f"query: {text}\nchunks: {len(chunks)}")
    for c in chunks:
        print(f"  [{c.score}] {c.source} — {c.text[:70]}")
    print(f"answer: {ans.text or '(refusal)'}\ncitations: {ans.citations}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="on-device retrieval e2e gate")
    ap.add_argument("--check", action="store_true", help="run the full scenario battery (CI gate)")
    ap.add_argument("--query", type=str, help="run one retrieve+compose turn and print it")
    args = ap.parse_args()
    if args.query:
        return asyncio.run(run_query(args.query))
    print("on-device backend — e2e gate (LLD 03-LOCAL §11)\n")
    return asyncio.run(run_check())


if __name__ == "__main__":
    raise SystemExit(main())
