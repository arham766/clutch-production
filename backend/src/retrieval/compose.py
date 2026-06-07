"""Grounded, cited compose with hard refusal (LLD 03 §7) — the accuracy guarantee.

The answer is built ONLY from retrieved chunks, every claim cited, and it refuses rather than
inventing. The refusal gate + citation extraction are pure (no LLM). template_compose is the
deterministic mock/fallback; gateway_compose drives a real OpenAI-compatible client (injected,
never imported here — mirrors qwen_gateway.py's lazy/no-import-of-vendor discipline at the seam).
"""

from __future__ import annotations

import re
from typing import Optional

from src.contracts import Answer, Chunk


# ── pure: refusal gate + citation extraction ────────────────────────────────
def _passing(chunks: list[Chunk], min_score: float) -> list[Chunk]:
    return [c for c in chunks if c.score is not None and c.score >= min_score]


def to_citation(c: Chunk) -> dict:
    return {
        "doc": c.metadata.get("source", c.source),
        "section": c.metadata.get("section", ""),
        "score": c.score,
    }


# ── template_compose (mock / fallback, no LLM) ──────────────────────────────
def template_compose(
    answer_query: str, chunks: list[Chunk], *, min_score: float = 0.35
) -> Answer:
    """Deterministic grounded compose. Concatenates top passing chunks' text (de-duped by
    section); the text literally IS chunk text, so the grounding invariant holds by construction.
    """
    passing = _passing(chunks or [], min_score)
    if not passing:
        return Answer(text="", citations=[])

    seen_sections: set[str] = set()
    paras: list[str] = []
    used: list[Chunk] = []
    for c in passing:
        section = c.metadata.get("section", "")
        key = section or c.id
        if key in seen_sections:
            continue
        seen_sections.add(key)
        paras.append(c.text.strip())
        used.append(c)

    text = "\n\n".join(paras).strip()
    if not text:
        return Answer(text="", citations=[])
    return Answer(text=text, citations=[to_citation(c) for c in used])


# ── gateway_compose (real, LLM via injected client) ─────────────────────────
_SYSTEM = (
    "You are Clutch's support composer. Answer the user's question USING ONLY the numbered "
    "sources below. Do not add facts not present in the sources. If the sources do not contain "
    "the answer, reply exactly with: NOT_IN_DOCS. Cite each sentence with the [n] of the source "
    "it came from."
)

_CITE = re.compile(r"\[(\d+)\]")


def _build_user_prompt(answer_query: str, passing: list[Chunk]) -> str:
    lines = [f"Question: {answer_query}", "Sources:"]
    for i, c in enumerate(passing, start=1):
        src = c.metadata.get("source", c.source)
        section = c.metadata.get("section", "")
        lines.append(f"[{i}] (source={src}, section={section}) {c.text}")
    return "\n".join(lines)


def _split_sentences(text: str) -> list[str]:
    # keep the citation markers attached to their sentence
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


async def gateway_compose(
    answer_query: str,
    chunks: list[Chunk],
    *,
    client,
    model: str = "reason.compose",
    min_score: float = 0.35,
) -> Answer:
    """Strict grounded compose via an injected OpenAI-compatible client.

    Refusal gate runs first (no LLM spent on a refusal). NOT_IN_DOCS → refusal. [n] markers map
    back to passing chunks → citations; un-cited sentences are dropped; if nothing survives, fall
    back to template_compose (never invent).
    """
    passing = _passing(chunks or [], min_score)
    if not passing:
        return Answer(text="", citations=[])

    user = _build_user_prompt(answer_query, passing)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    out = (resp.choices[0].message.content or "").strip()

    if not out or out.strip() == "NOT_IN_DOCS":
        return Answer(text="", citations=[])

    kept_sentences: list[str] = []
    used_idx: list[int] = []
    for sent in _split_sentences(out):
        markers = [int(m) for m in _CITE.findall(sent)]
        valid = [m for m in markers if 1 <= m <= len(passing)]
        if not valid:
            continue  # un-cited sentence dropped (HLD 03 §3)
        # strip the [n] markers from the spoken/displayed text (keep findall above for the indices);
        # collapse the double space left where a marker was removed. Answer.text must be clean prose
        # for TTS + the card, while citations still come from `valid`.
        cleaned = " ".join(_CITE.sub("", sent).split())
        if not cleaned:
            continue  # sentence was ONLY a marker (e.g. "[1]") — no prose to keep, skip it
        kept_sentences.append(cleaned)
        for m in valid:
            if (m - 1) not in used_idx:
                used_idx.append(m - 1)

    if not kept_sentences:
        return template_compose(answer_query, chunks, min_score=min_score)

    text = " ".join(kept_sentences).strip()
    citations = [to_citation(passing[i]) for i in used_idx]
    return Answer(text=text, citations=citations)


# ── streaming, ungrounded-but-fast (LLD 03 §7 relaxed for near-real-time voice) ──
# Raw token streaming for low-latency TTS: no citation gate, no sentence buffering. The model is
# told to compose from the sources conversationally; tokens are yielded as they arrive so TTS can
# begin almost immediately. Use a FAST (non-reasoning) model — a thinking model's TTFT dominates.
_STREAM_SYSTEM = (
    "You are Clutch's live voice support agent. Using the numbered SOURCES, answer the user's "
    "question in a natural, concise spoken style (1-2 short sentences). Do NOT say citation numbers "
    "or markers. If the sources don't cover it, say you don't have that in the documentation."
)


def _history_messages(history, summary: str) -> list[dict]:
    """Turn the agent's recall context (recent {role,text} turns + rolling summary) into chat
    messages, so the model has CONVERSATION MEMORY (follow-ups like 'and then?' resolve). Roles map
    agent→assistant, anything else→user. Empty/falsey turns are skipped."""
    msgs: list[dict] = []
    if summary:
        msgs.append({"role": "system",
                     "content": f"Earlier in this conversation: {summary}"})
    for h in (history or []):
        text = (h.get("text") or "").strip()
        if not text:
            continue
        role = "assistant" if h.get("role") == "agent" else "user"
        msgs.append({"role": role, "content": text})
    return msgs


async def gateway_compose_stream_raw(answer_query, chunks, *, client, model, history=None, summary=""):
    """Async-generator: yield raw answer token-deltas as the LLM streams (no citation gating).

    Trades the strict grounding gate for latency — tokens flow straight to TTS. Still prompts the
    model to use only the retrieved sources, so it stays substantially grounded. `history`/`summary`
    carry the prior turns so the agent stays coherent across the call (not a fresh turn each time).
    """
    passing = list(chunks or [])
    if not passing:
        yield "I don't have that in the documentation."
        return
    src = "\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(passing))
    user = f"Question: {answer_query}\nSOURCES:\n{src}"
    messages = ([{"role": "system", "content": _STREAM_SYSTEM}]
                + _history_messages(history, summary)
                + [{"role": "user", "content": user}])
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=300,
        stream=True,
    )
    async for ch in stream:
        delta = (ch.choices[0].delta.content or "") if ch.choices else ""
        if delta:
            yield delta


import asyncio as _asyncio


async def gateway_compose_race(answer_query, chunks, *, client, primary_model, fallback_model,
                               primary_deadline: float = None,  # noqa: ARG001 (kept for compat)
                               history=None, summary=""):
    """True parallel race: start BOTH models at t=0 and stream from whichever emits a token FIRST;
    the loser's stream is closed. Lowest possible first-token latency — fastest model wins each turn.
    Yields raw token-deltas. (`primary_deadline` is ignored; kept for call-site compatibility.)
    `history`/`summary` give both racers the conversation context so answers stay call-coherent.
    """
    gens = [
        gateway_compose_stream_raw(answer_query, chunks, client=client, model=primary_model,
                                   history=history, summary=summary),
        gateway_compose_stream_raw(answer_query, chunks, client=client, model=fallback_model,
                                   history=history, summary=summary),
    ]
    pending = {_asyncio.ensure_future(g.__anext__()): g for g in gens}
    winner = None
    first_tok = None
    while pending and winner is None:
        done, _ = await _asyncio.wait(pending, return_when=_asyncio.FIRST_COMPLETED)
        for task in done:
            gen = pending.pop(task)
            if not task.cancelled() and task.exception() is None:
                winner, first_tok = gen, task.result()   # first model to emit a token wins
                break
            try:                                          # this one errored on its first token
                await gen.aclose()
            except Exception:
                pass
    # close the losers
    for task, gen in pending.items():
        task.cancel()
        try:
            await gen.aclose()
        except Exception:
            pass
    if winner is None:
        return
    yield first_tok
    async for tok in winner:
        yield tok
