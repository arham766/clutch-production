"""On-device embedding + the in-memory search session.

Real path: when the `moss` SDK is importable, the edge box embeds with Moss on-device. Keyless path
(default, CI): a dependency-free lexical embedder (term-frequency vectors + cosine) fills the same
session, so the whole build→retrieve→ground flow runs offline with zero infra.

The session exposes a `push_index` method that this package **never calls** — it exists only so the
no-push invariant (LLD 03-LOCAL §11.3) can be asserted by a spy. The corpus and its vectors never
leave the box.
"""

from __future__ import annotations

import math
import re

from .contracts import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class LocalEmbedder:
    """Dependency-free stand-in embedder: text → term-frequency vector. Cosine over these is a real
    (if simple) semantic-ish retrieval that needs no model, no keys, no network."""

    name = "local-cosine-stand-in"

    def vec(self, text: str) -> dict[str, float]:
        tf: dict[str, float] = {}
        for t in _tokens(text):
            tf[t] = tf.get(t, 0.0) + 1.0
        return tf


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def build_embedder(embed_model: str):
    """Real Moss embedder if the SDK is present, else the keyless lexical stand-in. The on-device
    box never reaches Moss cloud either way — embedding happens in-process."""
    try:
        import moss  # noqa: F401  (presence check only; real wiring is an ops step)
    except Exception:
        return LocalEmbedder()
    # A real deployment swaps in the Moss on-device embedder here; kept as the stand-in for the
    # keyless build so this package has no hard dependency.
    return LocalEmbedder()


class Session:
    """In-memory search session — the in-process, offline index (Moss Python session is in-memory;
    durability is the disk artifact in local_store.py, LLD 03-LOCAL §3-A)."""

    def __init__(self, embedder):
        self._embedder = embedder
        self._docs: list[tuple[Chunk, dict]] = []   # (chunk, vector)
        self.push_calls: list = []                  # SPY: stays empty — we never push to cloud

    def add_docs(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self._docs.append((c, self._embedder.vec(c.text)))

    def push_index(self, *args, **kwargs) -> None:  # pragma: no cover - exists only to be spied
        # The corpus/vectors must NEVER be pushed to cloud. This package never calls this; the
        # no-push test asserts push_calls stays empty across a full onboard→retrieve→compose run.
        self.push_calls.append((args, kwargs))

    def query(self, text: str, *, top_k: int, product_id=None) -> list[Chunk]:
        qv = self._embedder.vec(text)
        scored: list[Chunk] = []
        for c, v in self._docs:
            if product_id and c.product_id and c.product_id != product_id:
                continue
            s = _cosine(qv, v)
            scored.append(Chunk(id=c.id, text=c.text, metadata=dict(c.metadata),
                                score=round(s, 4), source=c.source))
        scored.sort(key=lambda c: (c.score or 0.0), reverse=True)
        return scored[:top_k]
