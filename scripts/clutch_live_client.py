"""End-to-end live client — your REAL webcam + microphone against the running Clutch worker.

Publishes camera video + mic audio into the room, plays the agent's TTS back through your speakers,
and prints the grounded answer cards + voice states. The worker (scripts/run_clutch.py start) must
already be running and registered.

    # terminal 1:
    uv run python scripts/run_clutch.py start
    # terminal 2:
    uv run python -m scripts.clutch_live_client --seconds 60

Speak a question (e.g. "how do I clear a paper jam") and point the camera at the device.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cv2                       # noqa: E402
import numpy as np              # noqa: E402
import sounddevice as sd        # noqa: E402
from livekit import rtc         # noqa: E402

from scripts.realtime_mini import _build_cfg, _ensure_room, _mint_token, ROOM  # noqa: E402

MIC_RATE = 16000               # match the STT-friendly rate (the 16k WAV transcribed; 48k didn't)
MIC_BLOCK = 320                 # 20 ms @ 16k
MIC_GAIN = 8.0                  # software boost for low-gain mics (overridable via --gain)


def _video_thread(source: rtc.VideoSource, cam: int, stop: threading.Event) -> None:
    cap = cv2.VideoCapture(cam, cv2.CAP_DSHOW)
    try:
        for _ in range(10):
            cap.read()          # warmup
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.03); continue
            rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            h, w = rgba.shape[:2]
            source.capture_frame(rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, rgba.tobytes()))
            time.sleep(0.066)   # ~15 fps
    finally:
        cap.release()


async def _pump_mic(source: rtc.AudioSource, stop: asyncio.Event, mic=None, gain: float = 8.0) -> None:
    q: queue.Queue = queue.Queue()
    def _cb(indata, frames, t, status):  # noqa: ANN001 (sounddevice thread)
        q.put(bytes(indata))
    print(f"[mic] using device={mic if mic is not None else 'default'} "
          f"({sd.query_devices(mic if mic is not None else sd.default.device[0])['name']})", flush=True)
    stream = sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16",
                               blocksize=MIC_BLOCK, callback=_cb, device=mic)
    stream.start()
    loop = asyncio.get_event_loop()
    import numpy as _np, time as _t
    _last_log = 0.0; _peak = 0
    try:
        while not stop.is_set():
            data = await loop.run_in_executor(None, q.get)
            samp = _np.frombuffer(data, dtype=_np.int16).astype(_np.float32)
            samp = _np.clip(samp * gain, -32768, 32767).astype(_np.int16)   # boost low-gain mic
            data = samp.tobytes()
            n = len(data) // 2
            _rms = int(_np.sqrt((samp.astype(float) ** 2).mean() + 1))
            _peak = max(_peak, _rms)
            if _t.monotonic() - _last_log > 2.0:
                print(f"[mic] boosted RMS={_rms} peak2s={_peak} (gain x{gain})", flush=True)
                _last_log = _t.monotonic(); _peak = 0
            await source.capture_frame(
                rtc.AudioFrame(data=data, sample_rate=MIC_RATE, num_channels=1, samples_per_channel=n)
            )
    finally:
        stream.stop(); stream.close()


async def _play_agent_audio(track: rtc.Track) -> None:
    stream = rtc.AudioStream(track)
    out = None
    try:
        async for ev in stream:
            f = ev.frame
            if out is None:
                out = sd.RawOutputStream(samplerate=f.sample_rate, channels=f.num_channels, dtype="int16")
                out.start()
                print(f"  [audio] agent speaking ({f.sample_rate}Hz)...", flush=True)
            out.write(bytes(f.data))
    except Exception:
        pass
    finally:
        if out:
            out.stop(); out.close()
        await stream.aclose()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--mic", type=int, default=None)  # input device index (see device list); None=default
    ap.add_argument("--gain", type=float, default=8.0)  # software mic boost
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--modality", default="see")     # see → audio + video both on
    ap.add_argument("--no-video", action="store_true")  # publish audio only (isolate STT)
    args = ap.parse_args()

    cfg = _build_cfg()
    meta = json.dumps({"company_key": "demo", "modality": args.modality})
    await _ensure_room(cfg, meta)

    room = rtc.Room()
    agent_ready = asyncio.Event()

    @room.on("data_received")
    def _on_data(pkt):  # noqa: ANN001
        if getattr(pkt, "topic", None) != "clutch":
            return
        try:
            ev = json.loads(bytes(pkt.data).decode("utf-8"))
        except Exception:
            return
        t = ev.get("type")
        if t in ("voice_state", "answer"):
            agent_ready.set()           # agent + AgentSession are live → safe to publish mic
        if t == "answer":
            cites = ", ".join(c.get("doc", "") for c in ev.get("citations", []))
            print(f"\n  ANSWER: {ev['text']}\n  cites: {cites or '—'}", flush=True)
        elif t == "confirm":
            print(f"\n  CONFIRM: {ev.get('prompt')}", flush=True)
        elif t == "voice_state":
            print(f"  [state] {ev.get('state')}", flush=True)
        elif t == "latency":
            print(f"  [latency] {ev.get('time_taken_ms')}ms", flush=True)

    @room.on("track_subscribed")
    def _on_track(track, pub, participant):  # noqa: ANN001
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(_play_agent_audio(track))

    await room.connect(cfg.livekit_url, _mint_token(cfg, "live-client", metadata=meta))
    print(f"connected to {ROOM}; waiting for the agent to auto-dispatch...", flush=True)

    stop_v = threading.Event()
    stop_a = asyncio.Event()
    vt = None
    if not args.no_video:
        # publish camera video right away (See path handles tracks via track_subscribed)
        v_src = rtc.VideoSource(640, 480)
        v_track = rtc.LocalVideoTrack.create_video_track("cam", v_src)
        await room.local_participant.publish_track(
            v_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
        vt = threading.Thread(target=_video_thread, args=(v_src, args.cam, stop_v), daemon=True)
        vt.start()

    # CRITICAL: publish the mic only AFTER the agent's AgentSession is live, else its STT input never
    # links to our track and nothing transcribes. (The agent greets on join → agent_ready fires.)
    try:
        await asyncio.wait_for(agent_ready.wait(), timeout=20)
    except asyncio.TimeoutError:
        print("(agent didn't signal ready; publishing mic anyway)", flush=True)
    a_src = rtc.AudioSource(MIC_RATE, 1)
    a_track = rtc.LocalAudioTrack.create_audio_track("mic", a_src)
    await room.local_participant.publish_track(
        a_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    mic_task = asyncio.ensure_future(_pump_mic(a_src, stop_a, mic=args.mic, gain=args.gain))

    print(">>> Agent is live. SPEAK your question (USE HEADPHONES to avoid the agent echoing into the "
          f"mic). Listening ~{args.seconds:.0f}s...\n", flush=True)
    try:
        await asyncio.sleep(args.seconds)
    finally:
        stop_v.set(); stop_a.set()
        mic_task.cancel()
        await room.disconnect()
        print("\ndone.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
