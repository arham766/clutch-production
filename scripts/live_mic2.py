"""Isolation test: live mic through the PROVEN publish path (Caller), publishing only AFTER the
agent has joined. If this transcribes, clutch_live_client's early-publish timing is the bug."""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit import rtc  # noqa: E402
from scripts.realtime_mini import Caller, _build_cfg, _ensure_room  # noqa: E402

RATE = 16000


async def main():
    cfg = _build_cfg()
    await _ensure_room(cfg, json.dumps({"company_key": "demo", "modality": "talk"}))
    c = Caller(cfg)
    await c.connect()
    print("waiting for agent to join...", flush=True)
    await c.wait_event("voice_state", timeout=15)     # agent + session ready FIRST
    await asyncio.sleep(1.0)

    src = rtc.AudioSource(RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", src)
    await c.room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    q: queue.Queue = queue.Queue()
    sd.default.device = None
    stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=320,
                               callback=lambda d, f, t, s: q.put(bytes(d)))
    stream.start()
    loop = asyncio.get_event_loop()
    print(">>> SPEAK NOW: 'my iPhone screen is cracked' (15s)", flush=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15:
        data = await loop.run_in_executor(None, q.get)
        samp = np.clip(np.frombuffer(data, np.int16).astype(np.float32) * 10, -32768, 32767).astype(np.int16)
        await src.capture_frame(rtc.AudioFrame(data=samp.tobytes(), sample_rate=RATE,
                                               num_channels=1, samples_per_channel=320))
    stream.stop()
    await asyncio.sleep(3)
    print("events:", [e.get("type") for e in c.events])
    ans = [e for e in c.events if e.get("type") == "answer"]
    print("answers:", [a["text"][:70] for a in ans])
    await c.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=50))
