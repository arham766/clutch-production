"""Definitive STT test: publish a CLEAN synthesized-speech WAV as the mic, check Cartesia transcribes.

If STT transcribes this → the worker audio->STT path is perfect and live failures are the mic level.
If not → it's a worker-side bug. Worker must be running. Run: uv run python -m scripts.wav_stt_test
"""

from __future__ import annotations

import asyncio
import json
import sys
import wave
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit import rtc  # noqa: E402
from scripts.realtime_mini import Caller, _build_cfg, _ensure_room  # noqa: E402

WAV = Path(__file__).resolve().parent.parent / "say.wav"


async def _publish_wav(room, path, rate=16000, loops=2):
    wf = wave.open(str(path), "rb")
    pcm = wf.readframes(wf.getnframes()); wf.close()
    samples = np.frombuffer(pcm, dtype=np.int16)
    src = rtc.AudioSource(rate, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", src)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    spf = int(rate * 0.02)  # 20ms
    for _ in range(loops):
        for off in range(0, len(samples) - spf, spf):
            chunk = samples[off:off + spf]
            await src.capture_frame(rtc.AudioFrame(data=chunk.tobytes(), sample_rate=rate,
                                                   num_channels=1, samples_per_channel=spf))
        await asyncio.sleep(0.6)


async def main():
    cfg = _build_cfg()
    await _ensure_room(cfg, json.dumps({"company_key": "demo", "modality": "talk"}))
    c = Caller(cfg)
    await c.connect()
    await c.wait_event("voice_state", timeout=15)
    print("publishing clean speech WAV...")
    await _publish_wav(c.room, WAV)
    await asyncio.sleep(8)
    print("events:", [e.get("type") for e in c.events])
    ans = next((e for e in c.events if e.get("type") == "answer"), None)
    print("ANSWER:", ans["text"][:160] if ans else "(none)")
    await c.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60))
