"""FrameSampler (S8) — video track -> raw JPEG list[bytes] at ~1-2 fps.

Pulls frames off the subscribed video track at `fps`, throttled by `min_interval_ms`,
batches `batch` of them, and hands **raw JPEG `list[bytes]`** to `identify`. It does NOT rank
frames — best-frame selection lives in perception (Tonmoy's side of S8). This layer stays on
its side of the seam: decode/throttle/batch, then hand bytes over.

Verified against livekit-rtc: `rtc.VideoStream(track)` is async-iterable, yielding events whose
`.frame` is an `rtc.VideoFrame`; `aclose()` releases decoder buffers. `VideoFrame.convert(type)`
re-encodes the buffer to RGBA which Pillow can wrap.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Awaitable, Callable

import imagehash
from livekit import rtc
from PIL import Image

logger = logging.getLogger("clutch.realtime.frames")


def _frame_to_img(frame: rtc.VideoFrame) -> Image.Image:
    rgba = frame.convert(rtc.VideoBufferType.RGBA)
    return Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).convert("RGB")


def _jpeg_of(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class FrameSampler:
    def __init__(
        self,
        identify: Callable[..., Awaitable],
        on_result: Callable[..., Awaitable],
        *,
        fps: float,
        batch: int,
        min_interval_ms: int,
        change_distance: int = 10,         # pHash Hamming threshold: skip looks that didn't change
        clock: Callable[[], float] = time.monotonic,
    ):
        self._identify = identify          # S8 fn: (frames, catalog) -> IdentifyResult
        self._on_result = on_result        # -> ClutchAgent.handle_identify (S2)
        self._fps = fps
        self._batch = batch
        self._min_ms = min_interval_ms
        self._change_distance = change_distance
        self._clock = clock                # injected monotonic clock (testable)
        self._buf: list[bytes] = []
        self._ref = None                   # rolling pHash of the last processed view
        self._last = 0.0
        self._last_call = 0.0
        self.latest_frame: bytes | None = None   # freshest decoded JPEG, for on-demand VQA (look)

    async def run(self, track: rtc.VideoTrack, catalog) -> None:
        stream = rtc.VideoStream(track)
        try:
            async for ev in stream:
                now = self._clock()
                if now - self._last < 1.0 / self._fps:   # sample-rate gate (~1-2 fps)
                    continue
                self._last = now
                try:
                    img = _frame_to_img(ev.frame)
                    jpeg = _jpeg_of(img)
                    self.latest_frame = jpeg        # always keep the freshest frame (on-demand VQA)
                    h = imagehash.phash(img)
                    # change-gate: hold still → identical pHash → skip the VLM entirely (cost/latency).
                    if self._ref is not None and (h - self._ref) < self._change_distance:
                        continue
                    self._ref = h
                    self._buf.append(jpeg)
                except Exception as exc:  # bad frame — skip, don't kill the loop
                    logger.warning("frame decode failed: %r", exc)
                    continue
                if len(self._buf) >= self._batch:
                    frames, self._buf = self._buf, []
                    if now - self._last_call >= self._min_ms / 1000:  # cost floor
                        self._last_call = now
                        try:
                            result = await self._identify(frames, catalog)  # S8
                            await self._on_result(result)                   # → handle_identify (S2)
                        except Exception as exc:
                            logger.warning("identify/on_result failed: %r", exc)
        except asyncio.CancelledError:
            raise
        finally:
            await stream.aclose()  # release decoder buffers

