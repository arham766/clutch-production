"""Compose with a local-LLM-first endpoint resolver (LLD 03-LOCAL §4.4).

The refusal gate and citation post-verification are pure. Only endpoint resolution is new:
  - local   → the local LLM (down ⇒ template_compose, ZERO egress)
  - auto    → local if healthy, else the cloud gateway
  - gateway → cloud only
`template_compose` is fully offline grounding: the answer text *is* chunk text, so nothing leaves
the box. When an LLM is used, the only egress is the retrieved chunk text — never the corpus/vectors.
"""

from __future__ import annotations

import re

from .contracts import Answer, Chunk

_CITE = re.compile(r"\[(\d+)\]")


def _passing(chunks: list[Chunk], min_score: float) -> list[Chunk]:
    return [c for c in chunks if (c.score or 0.0) >= min_score]


def _to_citation(c: Chunk) -> dict:
    return {"doc": c.metadata.get("source", c.source),
            "section": c.metadata.get("section", ""), "score": c.score}


def template_compose(query: str, chunks: list[Chunk], *, min_score: float = 0.35) -> Answer:
    """Deterministic grounded compose — the answer literally IS chunk text, so grounding holds by
    construction and ZERO bytes leave the box."""
    passing = _passing(chunks, min_score)
    if not passing:
        return Answer(text="", citations=[])
    seen, paras, used = set(), [], []
    for c in passing:
        key = c.metadata.get("section", "") or c.id
        if key in seen:
            continue
        seen.add(key)
        paras.append(c.text.strip())
        used.append(c)
    return Answer(text="\n\n".join(paras), citations=[_to_citation(c) for c in used])


def local_llm_healthy(cfg, local_client=None) -> bool:
    """Cheap presence probe: an injected client (tests / a running local model) counts as healthy;
    otherwise require a configured base URL."""
    if local_client is not None:
        return True
    return bool(getattr(cfg, "local_reason_base_url", ""))


def openai_compat(base_url: str):
    """Lazily build a real OpenAI-compatible client for a local model. Imported only when actually
    used with a real URL, so the keyless path never needs `openai`."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=base_url, api_key="local")


def resolve_reason_endpoint(cfg, gateway_client=None, local_client=None):
    """Return the compose client per `reason_endpoint`, or None to mean template_compose (no egress)."""
    mode = cfg.reason_endpoint
    if mode == "gateway":
        return gateway_client
    if mode == "local":
        if local_client is not None:
            return local_client
        return openai_compat(cfg.local_reason_base_url) if cfg.local_reason_base_url else None
    if mode == "auto":
        if local_llm_healthy(cfg, local_client):
            return local_client or (openai_compat(cfg.local_reason_base_url)
                                    if cfg.local_reason_base_url else None) or gateway_client
        return gateway_client
    return None


_SYSTEM = (
    "Answer the question USING ONLY the numbered sources. Cite each sentence with its [n]. "
    "If the sources don't contain the answer, reply exactly NOT_IN_DOCS."
)


async def _llm_compose(query: str, passing: list[Chunk], *, client) -> Answer:
    """Grounded compose via an OpenAI-compatible client — the egress is the chunk text only."""
    src = "\n".join(f"[{i+1}] {c.text}" for i, c in enumerate(passing))
    resp = await client.chat.completions.create(
        model="reason.compose",
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": f"Question: {query}\nSources:\n{src}"}],
        temperature=0,
    )
    out = (resp.choices[0].message.content or "").strip()
    if not out or out == "NOT_IN_DOCS":
        return Answer(text="", citations=[])
    kept, used_idx = [], []
    for sent in re.split(r"(?<=[.!?])\s+", out):
        markers = [int(m) for m in _CITE.findall(sent)]
        valid = [m for m in markers if 1 <= m <= len(passing)]
        if not valid:
            continue                                       # uncited sentence dropped
        cleaned = " ".join(_CITE.sub("", sent).split())
        if not cleaned:
            continue
        kept.append(cleaned)
        for m in valid:
            if (m - 1) not in used_idx:
                used_idx.append(m - 1)
    if not kept:
        return template_compose(query, passing, min_score=0.0)
    return Answer(text=" ".join(kept), citations=[_to_citation(passing[i]) for i in used_idx])


async def compose_answer(query, chunks, *, cfg, gateway_client=None, local_client=None, min_score=0.35):
    passing = _passing(chunks, min_score)
    if not passing:
        return Answer(text="", citations=[])               # refusal — unchanged
    client = resolve_reason_endpoint(cfg, gateway_client, local_client)
    if client is None:
        return template_compose(query, chunks, min_score=min_score)  # zero egress
    return await _llm_compose(query, passing, client=client)
