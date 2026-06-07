"""Frame triage for the live camera feed (Pillow + imagehash pHash).

Two jobs, both keep the expensive vision call (identify → Qwen) cheap:

- `select_best_frames` — *within a burst*, keep the sharpest, mutually-distinct frame(s)
  (pHash dedup). This is what `identify` sends to the VLM.
- `FrameGate` — *across the continuous feed* (the hot-path loop), decide whether a new frame is a
  **meaningful change** vs the last one we processed, so the VLM only fires when the view actually
  changes. Holds a rolling reference + a hard rate cap. Customer holds still → zero VLM calls; moves
  to a new part → one call. Stateful, one per `SupportSession`. The realtime loop (05) drives it
  before calling `identify`.
"""

from __future__ import annotations

import io
import time

import imagehash
from PIL import Image, ImageFilter, ImageStat


def _load(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def sharpness(img: Image.Image) -> float:
    """Variance of an edge-filtered grayscale image — higher = sharper, less motion blur."""
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).var[0])


def phash(img: Image.Image) -> imagehash.ImageHash:
    """64-bit perceptual (DCT) hash. `a - b` gives the Hamming distance (0 = identical)."""
    return imagehash.phash(img)


def select_best_frames(frames: list[bytes], k: int = 2, dedup_distance: int = 8) -> list[bytes]:
    """Return up to `k` sharpest, mutually-distinct frames (sharpest first)."""
    if not frames:
        return []
    scored: list[tuple[float, imagehash.ImageHash, bytes]] = []
    for b in frames:
        try:
            img = _load(b)
        except Exception:
            continue                       # skip unreadable frames
        scored.append((sharpness(img), phash(img), b))
    scored.sort(key=lambda t: t[0], reverse=True)   # sharpest first

    picked: list[tuple[imagehash.ImageHash, bytes]] = []
    for _, h, b in scored:
        if any((h - ph) < dedup_distance for ph, _ in picked):
            continue                       # near-duplicate of an already-picked frame
        picked.append((h, b))
        if len(picked) >= k:
            break
    return [b for _, b in picked]


class FrameGate:
    """Across-time change gate for the continuous camera feed.

    `should_process(frame)` returns True only when the frame is a **meaningful change** vs the last
    accepted frame (pHash Hamming ≥ `change_distance`) **and** at least `min_interval_s` has passed
    since the last accept (rate cap). This stops redundant VLM calls while the customer holds the
    camera roughly still, and bounds call frequency when they move a lot.
    """

    def __init__(self, change_distance: int = 10, min_interval_s: float = 0.5):
        self.change_distance = change_distance
        self.min_interval_s = min_interval_s
        self._ref: imagehash.ImageHash | None = None
        self._last_accept: float = float("-inf")   # so the first frame always passes the rate cap

    def should_process(self, frame: bytes, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now - self._last_accept < self.min_interval_s:
            return False                           # rate cap
        try:
            h = phash(_load(frame))
        except Exception:
            return False                           # unreadable → skip
        if self._ref is not None and (h - self._ref) < self.change_distance:
            return False                           # view hasn't meaningfully changed
        self._ref = h
        self._last_accept = now
        return True

    def reset(self) -> None:
        self._ref = None
        self._last_accept = float("-inf")
