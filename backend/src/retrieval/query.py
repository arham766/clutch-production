"""Query construction (pure, no I/O) — LLD 03 §6.

Turns a RetrievalQuery (+ an optional ProblemSignals for the See path) into a single query
string. This layer never imports perception/: the Agent (04) builds the RetrievalQuery from an
IdentifyResult before calling retrieve_for. ProblemSignals is accepted only for direct/test use.
"""

from __future__ import annotations

from typing import Optional

from src.contracts import ProblemSignals, RetrievalQuery


def build_query_text(q: RetrievalQuery, problem: Optional[ProblemSignals] = None) -> str:
    """Type/Talk: q.text. See: fold in ProblemSignals.to_query(). q.need[] appended as soft keywords."""
    parts = [q.text or ""]
    if problem is not None:
        parts.append(problem.to_query())
    if q.need:
        parts.append(" ".join(str(n) for n in q.need))
    return " ".join(p for p in parts if p).strip()
