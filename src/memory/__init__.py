"""Cross-call memory (HLD 04, recall extension) — persist a compact per-user summary between calls.

Public surface: FileMemoryStore.load(key) / .save(key, summary). The realtime layer (05) seeds the
agent's rolling summary from this at session start (immediate read) and persists it at session end
(background) — neither touches the per-turn hot path. The store is injected by the composition root
(app.py); nothing here imports a sibling subfolder.
"""

from memory.store import FileMemoryStore

__all__ = ["FileMemoryStore"]
