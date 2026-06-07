"""FileMemoryStore — a tiny per-key memory KV (one small JSON file per key).

Cross-call recall without a new dependency: the agent's compact rolling summary is saved per
company/user and reloaded on the next call. File IO runs in a thread so the async wrappers never
block the event loop, and writes are atomic (temp + rename). Swap this for Moss/Firebase later
behind the same load/save shape — callers don't change.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path


def _safe(key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", key) or "default"


class FileMemoryStore:
    def __init__(self, root: str = "data/memory"):
        self.root = Path(root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _path(self, key: str) -> Path:
        return self.root / f"{_safe(key)}.json"

    async def load(self, key: str) -> str:
        """Return the saved summary for this key ("" if none). Fast local read; never raises."""
        def _read() -> str:
            p = self._path(key)
            if not p.exists():
                return ""
            try:
                return str(json.loads(p.read_text("utf-8")).get("summary", "") or "")
            except Exception:
                return ""
        try:
            return await asyncio.to_thread(_read)
        except Exception:
            return ""

    async def save(self, key: str, summary: str) -> None:
        """Persist the summary for this key (atomic). No-op on empty; never raises into the caller."""
        if not (summary or "").strip():
            return

        def _write() -> None:
            p = self._path(key)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"summary": summary}), "utf-8")
            os.replace(tmp, p)
        try:
            await asyncio.to_thread(_write)
        except Exception:
            pass
