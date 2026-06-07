"""ConversationAgent (HLD 04) — feature tests + integration tests.

Feature tests exercise each behavior in isolation (intent routing, grounding, escalation, safety,
speak gate, recall). Integration tests run multi-turn flows wiring the agent to realistic mock
retrieve/compose, and exercise the real Perception→Agent boundary (S3) via `canned_identify`.

The agent is async (S2: Realtime awaits on_user_turn/on_identify; S4 retrieve/compose are async).
Mock deps here stay sync — the agent's `_maybe` awaits real coroutines and passes sync values through.
Compose is called with the S4 signature `compose(answer_query: str, chunks)`.
"""

from __future__ import annotations

from agent import ConversationAgent
from contracts import (
    Answer,
    Candidate,
    CatalogEntry,
    Chunk,
    IdentifyResult,
    ProblemSignals,
    SupportSession,
)
from perception import canned_identify

CATALOG = [
    CatalogEntry(product_id="p1", name="Printer One", brand="HP"),
    CatalogEntry(product_id="p2", name="Printer Two", brand="HP"),
]
DEFAULT_CHUNK = Chunk("c1", "Open the rear tray and clear the jam.", {"product_id": "p1"}, 0.9,
                      "User Guide §3.2")


def build(*, modality="type", product_id=None, chunks=None, cited=True, compose_text=None,
          catalog=None, escalations=None, recorder=None, clock=None):
    session = SupportSession("s1", "acme", modality=modality, product_id=product_id)
    chunks = [DEFAULT_CHUNK] if chunks is None else chunks

    def retrieve(q):
        if recorder is not None:
            recorder.append(q)
        return list(chunks)

    def compose(answer_query, cks):                 # S4 signature: (answer_query: str, chunks)
        text = compose_text if compose_text is not None else (cks[0].text if cks else "")
        cites = [{"doc": c.source, "score": c.score} for c in cks] if cited else []
        return Answer(text, cites)

    esc = (lambda r: escalations.append(r)) if escalations is not None else None
    kw = {"clock": clock} if clock is not None else {}
    return ConversationAgent(session, retrieve, compose, escalate=esc, catalog=catalog or CATALOG, **kw)


# ============================================================ FEATURE TESTS

async def test_smalltalk_skips_retrieval():
    rec = []
    a = build(recorder=rec)
    ans = await a.on_user_turn("hi")
    assert ans is not None and ans.citations == []      # a greeting, no doc claim
    assert rec == []                                    # no retrieval for small talk
    assert a.s.phase == "assisting"


async def test_grounded_answer_has_citations():
    a = build()
    ans = await a.on_user_turn("how do I reset it?")
    assert ans is not None and ans.citations            # grounded, cited
    assert "See" not in ans.text                         # non-physical → no nudge
    assert [h["role"] for h in a.s.history] == ["user", "agent"]


async def test_no_coverage_escalates_and_offers_see():
    esc = []
    a = build(chunks=[], escalations=esc)               # retrieval empty
    ans = await a.on_user_turn("does it support AirPrint?")
    assert "docs" in ans.text.lower() and "See" in ans.text
    assert a.s.phase == "escalating" and esc == ["no_doc_coverage"]


async def test_ungrounded_answer_is_refused():
    esc = []
    a = build(cited=False, escalations=esc)             # compose returns text with NO citations
    ans = await a.on_user_turn("how do I reset it?")
    assert ans.citations == [] and "docs" in ans.text.lower()   # never speak an uncited claim
    assert esc == ["no_doc_coverage"]


async def test_safety_withholds_risky_step_without_warning():
    esc = []
    a = build(compose_text="Pull the wire out with pliers.", escalations=esc)
    ans = await a.on_user_turn("how do I fix the connector?")
    assert "safety" in ans.text.lower() and ans.citations == []
    assert a.s.phase == "escalating" and esc == ["unsafe_step_without_warning"]


async def test_safety_allows_risky_step_with_warning():
    a = build(compose_text="Unplug it first, then remove the rear panel.")
    ans = await a.on_user_turn("how do I open it?")
    assert "unplug" in ans.text.lower() and ans.citations    # warning present → allowed, grounded


async def test_modality_escalation_nudges_on_physical_problem_text():
    a = build(modality="type")
    ans = await a.on_user_turn("the light is blinking red")
    assert "See" in ans.text and ans.citations          # physical + not on camera → nudge, grounded


async def test_no_see_nudge_when_already_on_see():
    a = build(modality="see")
    ans = await a.on_user_turn("the light is blinking red")
    assert "See" not in ans.text                         # already showing → no nudge


async def test_on_identify_asks_to_confirm_the_guess():
    a = build()
    res = IdentifyResult(product_id=None, confidence=0.6, model_source="visual_match",
                         candidates=[Candidate("p1", 0.6), Candidate("p2", 0.55)],
                         needs_confirmation=True, problem=ProblemSignals(summary="jam"))
    ans = await a.on_identify(res)
    assert "Printer One" in ans.text                    # confirms the top guess by name
    assert a.s.phase == "confirming" and a.s.product_id is None      # NOT scoped until user says yes
    assert a.s.modality == "see"


async def test_on_identify_confident_still_confirms_first():
    """Conversational flow: even a confident match asks 'is this X?' before answering."""
    rec = []
    a = build(recorder=rec)
    res = IdentifyResult(product_id="p1", confidence=0.92, model_source="visual_match",
                         candidates=[Candidate("p1", 0.92)], needs_confirmation=False,
                         problem=ProblemSignals(summary="paper jam"))
    ans = await a.on_identify(res)
    assert "Printer One" in ans.text and a.s.phase == "confirming"
    assert a.s.product_id is None and a._pending_product == "p1"     # pending, not scoped yet
    assert rec == []                                                 # no retrieval until problem given


async def test_see_confirm_then_problem_then_answer():
    """yes → 'what problem?' → user describes → grounded scoped answer."""
    rec = []
    a = build(recorder=rec)
    await a.on_identify(IdentifyResult(product_id="p1", confidence=0.9, model_source="visual_match",
                                       candidates=[Candidate("p1", 0.9)], needs_confirmation=False,
                                       problem=ProblemSignals()))
    yes = await a.on_user_turn("yes")
    assert "problem" in yes.text.lower() and a.s.product_id == "p1" and a.s.phase == "awaiting_problem"
    ans = await a.on_user_turn("there's a paper jam")
    assert rec[-1].product_id == "p1" and ans.citations and a.s.phase == "assisting"


async def test_see_confirm_no_reidentifies():
    a = build()
    await a.on_identify(IdentifyResult(product_id="p1", confidence=0.9, model_source="visual_match",
                                       candidates=[Candidate("p1", 0.9)], needs_confirmation=False,
                                       problem=ProblemSignals()))
    no = await a.on_user_turn("no")
    assert a.s.product_id is None and "p1" in a._rejected and a.s.phase == "identifying"
    # a fresh look at the same (rejected) product shouldn't re-ask it
    again = await a.on_identify(IdentifyResult(product_id="p1", confidence=0.9, model_source="visual_match",
                                               candidates=[Candidate("p1", 0.9)], needs_confirmation=False,
                                               problem=ProblemSignals()))
    assert again is None or "can't" in again.text.lower()


def test_speak_gate_turn_vs_ambient():
    a = build()
    assert a.should_speak("turn", "question") is True
    assert a.should_speak("turn", "ack") is True
    assert a.should_speak("vision", "fix") is True
    assert a.should_speak("vision", "confirm") is True
    assert a.should_speak("vision", "ack") is False     # ambient: stay silent unless useful


async def test_debounce_suppresses_immediate_repeat():
    clk = [0.0]
    a = build(clock=lambda: clk[0], chunks=[DEFAULT_CHUNK])
    first = await a.on_user_turn("how do I reset it?")
    second = await a.on_user_turn("how do I reset it?")   # same answer, clock not advanced
    assert first is not None and second is None
    clk[0] = 5.0
    third = await a.on_user_turn("how do I reset it?")     # past debounce → speaks again
    assert third is not None


async def test_recall_context_and_summary_folding():
    a = build()
    for i in range(20):
        await a.on_user_turn(f"question number {i}?")
    ctx = a.recall_context()
    assert ctx["phase"] and "recent" in ctx and len(ctx["recent"]) <= a._recall_n * 2
    assert a.rolling_summary != ""                       # older turns folded into the summary


def test_add_turn_dedups_consecutive():
    a = build()
    a.add_turn("user", "x")
    a.add_turn("user", "x")                              # identical consecutive → ignored
    assert sum(1 for h in a.s.history if h["text"] == "x") == 1


async def test_turn_never_crashes_on_dependency_error():
    def boom(_q):
        raise RuntimeError("retrieval down")
    a = ConversationAgent(SupportSession("s", "acme"), boom, lambda aq, c: Answer("x", []))
    assert await a.on_user_turn("how do I reset it?") is None   # swallowed → no crash


async def test_is_resolved_on_success_signal():
    a = build()
    await a.on_user_turn("that worked, thanks")
    assert a.is_resolved() and a.s.phase == "resolved"


async def test_danger_intent_gets_grounded_safety_answer():
    a = build(compose_text="Unplug the unit first. Warning: do not touch the power supply.")
    ans = await a.on_user_turn("I smell burning")
    assert "unplug" in ans.text.lower() and ans.citations  # safety step, grounded + warned


# ============================================================ INTEGRATION TESTS

async def test_integration_text_to_see_to_resolved():
    """Full conversational flow: text Q + See nudge → show device → confirm → problem → fix → resolved."""
    rec = []
    a = build(modality="type", recorder=rec)

    # 1) Type: physical problem → grounded answer + "hit See" nudge
    t1 = await a.on_user_turn("my printer won't print and the light is blinking red")
    assert t1.citations and "See" in t1.text

    # 2) User hits See → Perception identifies → agent CONFIRMS the device (doesn't blurt a fix)
    res = canned_identify([b"frame-bytes"], [CATALOG[0]])   # guesses p1
    confirm = await a.on_identify(res)
    assert "Printer One" in confirm.text and a.s.modality == "see" and a.s.product_id is None

    # 3) "yes" → agent asks for the problem; 4) user describes it → grounded scoped fix
    yes = await a.on_user_turn("yes")
    assert "problem" in yes.text.lower() and a.s.product_id == "p1"
    fix = await a.on_user_turn("the light is blinking red")
    assert rec[-1].product_id == "p1" and fix.citations

    # 5) resolved
    await a.on_user_turn("that fixed it, thanks")
    assert a.is_resolved()


async def test_integration_reject_then_confirm_other():
    """See guesses wrong → user says no → re-identify a different product → confirm → answer."""
    rec = []
    a = build(recorder=rec)

    c = await a.on_identify(IdentifyResult(product_id="p1", confidence=0.9, model_source="visual_match",
                                           candidates=[Candidate("p1", 0.9)], needs_confirmation=False,
                                           problem=ProblemSignals()))
    assert "Printer One" in c.text
    no = await a.on_user_turn("no")
    assert "p1" in a._rejected and a.s.phase == "identifying"

    c2 = await a.on_identify(IdentifyResult(product_id="p2", confidence=0.93, model_source="ocr_label",
                                            candidates=[Candidate("p2", 0.93)], needs_confirmation=False,
                                            problem=ProblemSignals()))
    assert "Printer Two" in c2.text and a._pending_product == "p2"
    await a.on_user_turn("yes")
    f = await a.on_user_turn("the toner is low")
    assert a.s.product_id == "p2" and rec[-1].product_id == "p2" and f.citations


async def test_integration_scope_persists_across_turns():
    """Once confirmed, later text turns stay scoped to the product."""
    rec = []
    a = build(recorder=rec)
    await a.on_identify(canned_identify([b"x"], [CATALOG[0]]))    # confirm p1?
    await a.on_user_turn("yes")                                  # → scope p1, awaiting problem
    await a.on_user_turn("how do I change the toner?")           # problem → answer scoped p1
    assert rec[-1].product_id == "p1"


# ============================================================ RECALL (streaming path)

async def test_stream_turn_threads_conversation_history_to_composer():
    """Regression: the streaming composer must receive PRIOR turns + summary so the agent stays
    coherent across a call (follow-ups resolve). Before this, every turn was composed cold."""
    captured: dict = {}

    async def compose_stream(answer_query, chunks, history=None, summary=""):
        captured["query"] = answer_query
        captured["history"] = history
        captured["summary"] = summary
        for tok in ("The ", "back ", "panel."):
            yield tok

    session = SupportSession("s1", "acme", modality="talk", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK],
                          lambda aq, cks: Answer("", []),
                          compose_stream=compose_stream, catalog=CATALOG)

    out1 = "".join([t async for t in a.stream_turn("my screen is cracked")])
    assert out1                                              # first turn produced an answer

    captured.clear()
    out2 = "".join([t async for t in a.stream_turn("what about the back?")])
    assert out2

    # Turn 2 saw turn 1 (user + agent) as history...
    assert captured["history"], "history was not threaded to the composer"
    roles = [h["role"] for h in captured["history"]]
    assert "user" in roles and "agent" in roles
    # ...and the CURRENT user turn is the answer_query, NOT duplicated into history.
    assert captured["query"] == "what about the back?"
    assert captured["history"][-1]["text"] != "what about the back?"


async def test_stream_turn_falls_back_for_recall_unaware_composer():
    """A composer that only accepts (query, chunks) must still work (TypeError fallback)."""
    async def compose_stream(answer_query, chunks):         # no history/summary params
        yield "ok"

    session = SupportSession("s1", "acme", modality="talk", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK],
                          lambda aq, cks: Answer("", []),
                          compose_stream=compose_stream, catalog=CATALOG)
    out = "".join([t async for t in a.stream_turn("how do I reset it?")])
    assert out == "ok"


# ============================================================ UI hook + recall depth

async def test_on_context_fires_with_retrieved_chunks():
    """The Knowledge Matches hook must fire with the query + retrieved chunks on a grounded turn."""
    seen = []
    session = SupportSession("s1", "acme", modality="talk", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK],
                          lambda aq, cks: Answer(cks[0].text, [{"doc": "d", "score": 0.9}]),
                          catalog=CATALOG)
    a.on_context = lambda query, chunks, ms=None: seen.append((query, chunks, ms))
    await a.on_user_turn("how do I clear the jam?")
    assert seen, "on_context never fired"
    q, chunks, _ = seen[0]
    assert chunks and chunks[0] is DEFAULT_CHUNK


async def test_case_summary_keeps_device_across_folding():
    """Recall: the confirmed device stays in the prompt context even after history folds away."""
    session = SupportSession("s1", "acme", modality="talk", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK], lambda aq, cks: Answer("x", []),
                          catalog=CATALOG)
    for i in range(24):                                   # force several folds
        a.add_turn("user", f"turn {i}")
        a.add_turn("agent", f"reply {i}")
    summary = a._case_summary(a.recall_context())
    assert "Printer One" in summary                       # device survives folding
    assert "agent:" in a.rolling_summary or "reply" in a.rolling_summary  # agent turns kept too


# ============================================================ See VQA (visual answer path)

async def _simple_stream(answer_query, chunks, history=None, summary=""):
    for t in ("Take ", "it ", "in ", "for ", "service."):
        yield t


async def test_see_visual_question_looks_then_grounds():
    """In See mode, a visual question LOOKS at the frame (vision answers), THEN streams grounded steps."""
    seen = []
    captured = {}

    async def look_fn(frames, question):
        seen.append((frames, question))
        return "The rear camera lens has a visible crack"

    async def compose_stream(answer_query, chunks, history=None, summary=""):
        captured["summary"] = summary
        captured["query"] = answer_query
        for t in ("Bring ", "it ", "in ", "for ", "repair."):
            yield t

    session = SupportSession("s1", "acme", modality="see", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK], lambda aq, c: Answer("x", []),
                          compose_stream=compose_stream, look=look_fn, catalog=CATALOG)
    a.frame_provider = lambda: b"JPEGBYTES"

    out = "".join([t async for t in a.stream_turn("can you see the crack on the back?")])
    assert seen, "look() was never called"
    assert seen[0][0] == [b"JPEGBYTES"]                 # the live frame was passed to vision
    assert "crack" in out                                # vision observation spoken first
    assert "repair" in out                               # grounded steps followed
    assert "[Seen now]" in captured["summary"]           # observation handed to the composer
    assert captured["query"] == "can you see the crack on the back?"


async def test_see_non_visual_question_skips_look():
    """A non-visual question in See mode answers from docs without spending a vision call."""
    called = []

    async def look_fn(frames, question):
        called.append(question)
        return "should not be used"

    session = SupportSession("s1", "acme", modality="see", product_id="p1")
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK], lambda aq, c: Answer("x", []),
                          compose_stream=_simple_stream, look=look_fn, catalog=CATALOG)
    a.frame_provider = lambda: b"frame"
    out = "".join([t async for t in a.stream_turn("how do I reset it?")])
    assert called == []                                   # no visual cue → no look()
    assert "service" in out                               # answered from the doc stream


async def test_visual_path_inactive_when_not_in_see_mode():
    """Even a visual-sounding question stays on the doc path when the user isn't on camera."""
    called = []

    async def look_fn(frames, question):
        called.append(question)
        return "x"

    session = SupportSession("s1", "acme", modality="talk", product_id="p1")   # not 'see'
    a = ConversationAgent(session, lambda q: [DEFAULT_CHUNK], lambda aq, c: Answer("x", []),
                          compose_stream=_simple_stream, look=look_fn, catalog=CATALOG)
    a.frame_provider = lambda: b"frame"
    _ = "".join([t async for t in a.stream_turn("can you see the crack?")])
    assert called == []                                   # talk mode → never looks
