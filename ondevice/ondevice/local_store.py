"""LocalIndexStore — the durable on-disk artifact + boot (LLD 03-LOCAL §3-A, §4.3).

The corpus-on-disk is the proprietary asset; the search index is reproducible from it. The artifact
is `chunks.jsonl` + `catalog.json` + `manifest.json`, all written atomically (temp + rename) so a
crash mid-onboard can't corrupt it. `build_session()` rebuilds the in-memory session at boot by
re-embedding on-device — never `push_index`. `upsert()` is incremental: a per-source content hash in
the manifest means an unchanged doc re-embeds zero chunks.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .contracts import Chunk
from .embed import Session, build_embedder


def _hash(texts: list[str]) -> str:
    h = hashlib.sha256()
    for t in texts:
        h.update(t.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)              # atomic on the same filesystem


class LocalIndexStore:
    def __init__(self, path, *, embed_model: str = "moss-minilm"):
        self.dir = Path(path)
        self.embed_model = embed_model
        self.dir.mkdir(parents=True, exist_ok=True)
        self._chunks_f = self.dir / "chunks.jsonl"
        self._catalog_f = self.dir / "catalog.json"
        self._manifest_f = self.dir / "manifest.json"

    # ---- durable artifact -------------------------------------------------
    def _read_chunks(self) -> list[Chunk]:
        if not self._chunks_f.exists():
            return []
        out: list[Chunk] = []
        for line in self._chunks_f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(Chunk(id=d["id"], text=d["text"], metadata=d.get("metadata", {}),
                             source=d.get("source", "")))
        return out

    def _read_manifest(self) -> dict:
        if not self._manifest_f.exists():
            return {"version": 0, "embed_model": self.embed_model, "doc_hashes": {}}
        return json.loads(self._manifest_f.read_text(encoding="utf-8"))

    def _write_all(self, chunks: list[Chunk], manifest: dict, catalog: dict) -> None:
        _atomic_write(self._chunks_f,
                      "\n".join(json.dumps({"id": c.id, "text": c.text, "metadata": c.metadata,
                                            "source": c.source}) for c in chunks))
        _atomic_write(self._manifest_f, json.dumps(manifest, indent=2))
        _atomic_write(self._catalog_f, json.dumps(catalog, indent=2))

    # ---- boot (derived index) --------------------------------------------
    def build_session(self) -> Session:
        """Rebuild the in-memory session from the disk artifact — the boot path. NEVER push_index."""
        session = Session(build_embedder(self.embed_model))
        session.add_docs(self._read_chunks())
        return session

    def catalog(self) -> dict:
        return json.loads(self._catalog_f.read_text(encoding="utf-8")) if self._catalog_f.exists() else {}

    # ---- admin onboarding (incremental) ----------------------------------
    def upsert(self, chunks: list[Chunk], catalog_delta: dict | None = None) -> int:
        """Append/replace by source; re-embed only changed docs. Returns the number of *sources*
        (re)embedded — 0 when nothing changed (LLD 03-LOCAL §6 incremental)."""
        manifest = self._read_manifest()
        hashes: dict = dict(manifest.get("doc_hashes", {}))
        existing = {c.id: c for c in self._read_chunks()}

        # group the incoming chunks by source doc
        by_source: dict[str, list[Chunk]] = {}
        for c in chunks:
            by_source.setdefault(c.source or c.metadata.get("source", ""), []).append(c)

        changed = 0
        for source, group in by_source.items():
            new_hash = _hash([c.text for c in group])
            if hashes.get(source) == new_hash:
                continue                                   # unchanged doc → skip (incremental)
            changed += 1
            hashes[source] = new_hash
            # drop any prior chunks from this source, then add the new ones
            existing = {cid: c for cid, c in existing.items()
                        if (c.source or c.metadata.get("source", "")) != source}
            for c in group:
                existing[c.id] = c

        if changed == 0:
            return 0

        catalog = self.catalog()
        catalog.update(catalog_delta or {})
        manifest = {"version": manifest.get("version", 0) + 1,
                    "embed_model": self.embed_model, "doc_hashes": hashes}
        self._write_all(list(existing.values()), manifest, catalog)
        return changed
