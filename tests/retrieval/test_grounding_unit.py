"""The bare-minimum pure unit suite for retrieval (LLD 03 §11) — three invariants.

Pure, dependency-free: imports only retrieval + contracts, touches no Moss/keys/network.
Everything behavioral lives in scripts/retrieval_mini.py --check (§12).
"""

from __future__ import annotations

from contracts import Chunk, ProblemSignals, RetrievalQuery
from retrieval import template_compose, to_citation
from retrieval.query import build_query_text


def _chunk(cid, text, source, section, score):
    return Chunk(
        id=cid,
        text=text,
        metadata={"source": source, "section": section},
        score=score,
        source=source,
    )


def test_refusal_gate():
    """Empty chunks AND all-below-min_score → refusal (empty text), no LLM call spent."""
    # empty
    a = template_compose("q", [], min_score=0.35)
    assert a.is_refusal
    assert a.text == "" and a.citations == []

    # all below floor
    weak = [_chunk("c1", "some text", "doc.pdf", "S", 0.10)]
    a2 = template_compose("q", weak, min_score=0.35)
    assert a2.is_refusal
    assert a2.citations == []


def test_grounding_invariant():
    """template_compose text ⊆ chunk text (nothing invented); to_citation maps correctly."""
    c = _chunk("c1", "Open the rear door to clear the jam.", "manual.pdf", "Jams", 0.9)
    a = template_compose("how to clear a jam", [c], min_score=0.35)

    # the composed text literally is the chunk text — nothing invented
    assert c.text.strip() in a.text
    assert not a.is_refusal

    # citation extraction maps metadata{source,section}+score → {doc,section,score}
    cit = to_citation(c)
    assert cit == {"doc": "manual.pdf", "section": "Jams", "score": 0.9}
    assert a.citations[0] == cit


def test_query_build():
    """build_query_text folds text + ProblemSignals.to_query() + need[] hints."""
    q = RetrievalQuery(company_id="demo", text="printer wont print", need=["steps", "warning"])
    prob = ProblemSignals(error_codes=["E13"], parts=["rear tray"], summary="paper jam")

    text = build_query_text(q, prob)
    assert "printer wont print" in text
    assert "paper jam" in text and "E13" in text and "rear tray" in text
    assert "steps" in text and "warning" in text

    # no problem, no need → just the text
    q2 = RetrievalQuery(company_id="demo", text="hello")
    assert build_query_text(q2) == "hello"
