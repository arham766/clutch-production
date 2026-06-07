"""Scripted end-to-end test of the conversational See flow (deterministic, no live mic).

Publishes a saved iPhone snapshot as the camera so Qwen identifies it, then drives the dialogue with
text replies: confirm -> 'yes' -> describe problem -> grounded answer. Worker must be running.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import cv2
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit import rtc  # noqa: E402
from scripts.realtime_mini import Caller, _build_cfg, _ensure_room  # noqa: E402

IMG = Path(__file__).resolve().parent.parent / "iphone_snap.jpg"


async def _publish_image(room, path, stop):
    bgr = cv2.imread(str(path))
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
    h, w = rgba.shape[:2]
    src = rtc.VideoSource(w, h)
    track = rtc.LocalVideoTrack.create_video_track("cam", src)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    data = rgba.tobytes()
    while not stop.is_set():
        src.capture_frame(rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, data))
        await asyncio.sleep(0.1)


async def _next_answer(c, since, timeout=25):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in c.events[since:]:
            if e.get("type") == "answer":
                return e, len(c.events)
        await asyncio.sleep(0.1)
    return None, len(c.events)


async def main():
    cfg = _build_cfg()
    await _ensure_room(cfg, json.dumps({"company_key": "demo", "modality": "see"}))
    c = Caller(cfg)
    await c.connect()
    stop = asyncio.Event()
    asyncio.ensure_future(_publish_image(c.room, IMG, stop))

    n = 0
    # 1) Qwen identifies → confirm
    ev, n = await _next_answer(c, n)
    print("AGENT:", ev["text"] if ev else "(no confirm)")
    # 2) user: yes → "what problem?"
    print("USER : yes")
    await c.send_text("yes")
    ev, n = await _next_answer(c, n)
    print("AGENT:", ev["text"] if ev else "(no problem prompt)")
    # 3) user describes problem → grounded answer
    print("USER : my screen is cracked")
    await c.send_text("my screen is cracked")
    ev, n = await _next_answer(c, n)
    print("AGENT:", ev["text"] if ev else "(no answer)")
    if ev:
        print("CITES:", [x.get("doc") for x in ev.get("citations", [])])
    stop.set()
    await c.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=90))
