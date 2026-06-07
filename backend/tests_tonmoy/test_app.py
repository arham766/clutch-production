"""Composition root (app.py) — the C↔D fusing point, keyless.

Locks that build_agent wires the real ConversationAgent (04) to Fardin's real retrieval (03) with
no agent-side mocks, and that build_identify returns a working keyless vision path. Offline.
"""

from __future__ import annotations

import io

from PIL import Image

from src.app_livekit import build_agent, build_identify
from src.contracts import CatalogEntry, SupportSession

# Aligned to LocalCosineRetriever's shipped corpus (product_id "lj-m404").
CATALOG = [CatalogEntry(product_id="lj-m404", name="LaserJet Pro M404", brand="HP")]


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (90, 110, 130)).save(buf, "PNG")
    return buf.getvalue()


async def test_build_agent_grounds_over_real_retrieval():
    a = build_agent(SupportSession("s1", "demo", modality="type"), catalog=CATALOG)
    ans = await a.on_user_turn("the printer has a paper jam, how do I clear it?")
    assert ans is not None and ans.citations                 # grounded from the real corpus
    assert "jam" in ans.text.lower()
    assert a.s.phase == "assisting"


async def test_build_agent_refuses_out_of_docs():
    a = build_agent(SupportSession("s2", "demo", modality="type"), catalog=CATALOG)
    ans = await a.on_user_turn("can it print in full color duplex?")
    assert ans is None or not ans.citations or "docs" in ans.text.lower()   # never invents
    assert a.s.phase == "escalating"


async def test_build_identify_keyless_resolves():
    identify = build_identify()                               # mock vision, no creds
    r = await identify([_png()], CATALOG)
    assert r is not None and r.product_id == "lj-m404" and r.resolved


async def test_see_confirms_then_scopes_on_yes():
    """Conversational See: on_identify confirms the device; a 'yes' scopes retrieval to it."""
    a = build_agent(SupportSession("s3", "demo", modality="type"), catalog=CATALOG)
    identify = build_identify()
    res = await identify([_png()], CATALOG)
    confirm = await a.on_identify(res)
    assert a.s.modality == "see" and a.s.product_id is None      # confirm first, not scoped yet
    assert "LaserJet" in confirm.text and a._pending_product == "lj-m404"
    yes = await a.on_user_turn("yes")
    assert a.s.product_id == "lj-m404" and "problem" in yes.text.lower()


# ---------------------------------------------------------- realtime composition root (05 wiring)

def test_make_scope_session_stamps_catalog():
    from src.app_livekit import make_scope_session
    scope = make_scope_session(catalog=CATALOG)
    s = scope("acme", "see")
    assert s.company_id == "acme" and s.modality == "see"
    assert [c.product_id for c in s.catalog] == ["lj-m404"]      # catalog flows to the session (S6)


def test_scope_session_defaults_bad_modality_to_type():
    from src.app_livekit import make_scope_session
    s = make_scope_session(catalog=CATALOG)("", "bogus")
    assert s.company_id == "demo" and s.modality == "type"       # invalid modality → type


async def test_per_session_agents_are_isolated():
    """Realtime builds one agent per room — two sessions must not share FSM state."""
    rf = lambda q: []                                            # noqa: E731 (no-coverage retrieve)
    comp = lambda aq, cks: None
    scope = __import__("src.app_livekit", fromlist=["make_scope_session"]).make_scope_session(catalog=CATALOG)
    a1 = build_agent(scope("c1", "type"), catalog=CATALOG)
    a2 = build_agent(scope("c2", "type"), catalog=CATALOG)
    await a1.on_user_turn("that worked, thanks")                 # a1 → resolved
    assert a1.is_resolved() and not a2.is_resolved()             # a2 untouched
    assert a1.s.company_id == "c1" and a2.s.company_id == "c2"
    # NB: build_clutch_worker reaches speech (RuntimeError without CARTESIA_API_KEY) is verified
    # outside pytest (run_clutch.py --check) — importing livekit here would pollute the speech
    # selection test that asserts livekit stays unimported.
