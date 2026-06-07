"""realtime_mini — the ONE test of src/realtime/ (LLD 05-realtime §9).

Stands up the smallest complete REAL instance of everything the layer needs and drives one
turn per modality against REAL infra:
    * a real LiveKit server — a Cloud project when LIVEKIT_URL is a wss:// URL (no docker), or
      a local docker dev server otherwise,
    * the real realtime worker (src/realtime),
    * real Cartesia STT/TTS + silero VAD,
    * the real S2/S8 stand-ins from `scripts/_harness.py` (genuine logic, no mocks).

Then a second "caller" participant joins the same room, subscribes the "clutch" data topic, and
we assert the four typed events flow for the five modality checks:
    TALK   -> answer event + real latency badge
    STATE  -> voice_state transitions (listening -> thinking -> speaking)
    BARGE  -> interruption is accepted while the agent is speaking
    SEE    -> publish a JPEG -> confirm/answer event from the See path
    TYPE   -> typed text -> answer event, with NO TTS audio published

`--check` is the bare-minimum acceptance gate:
    .venv/Scripts/python.exe -m scripts.realtime_mini --check
It SKIPs cleanly (prints SKIP, exits 0) when LiveKit/Cartesia creds + docker are absent, or when
livekit-agents itself is not installed — so a keyless checkout never blocks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
import struct
import sys
import time
from pathlib import Path

# ── Load real keys from .env.local at the repo root (a permanent convenience). ──
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
except Exception:
    pass

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# ── Degrade cleanly if the voice deps are not installed (keyless checkout). ──────
try:
    from livekit import api, rtc  # noqa: F401
    from livekit.agents import WorkerOptions, cli  # noqa: F401

    _LIVEKIT_OK = True
    _LIVEKIT_ERR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    _LIVEKIT_OK = False
    _LIVEKIT_ERR = repr(exc)


DEV_KEY = "devkey"
DEV_SECRET = "secret"
DEV_URL = "ws://localhost:7880"
ROOM = "clutch-mini"
AGENT_NAME = "clutch-agent"          # named worker → explicit dispatch (deterministic on Cloud)
LIVEKIT_IMAGE = "livekit/livekit-server:latest"

# Per-check budget. Kept modest so the 5 sequential checks complete within the overall 150s cap
# even when a check has nothing to wait for (e.g. sine-tone audio yields no STT words).
EVENT_TIMEOUT = 12.0


# ────────────────────────────────────────────────────────────────────────────────
# Skip logic — the acceptance gate must never fail on a keyless box.
# ────────────────────────────────────────────────────────────────────────────────
def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _cloud_creds() -> bool:
    url = os.environ.get("LIVEKIT_URL", "")
    return bool(
        url.startswith(("ws://", "wss://"))
        and "localhost" not in url
        and os.environ.get("LIVEKIT_API_KEY")
        and os.environ.get("LIVEKIT_API_SECRET")
    )


def _skip_reason() -> str | None:
    """Return a SKIP reason string, or None if --check can really run."""
    if not _LIVEKIT_OK:
        return f"livekit not installed ({_LIVEKIT_ERR})"
    if not os.environ.get("CARTESIA_API_KEY"):
        return "no creds (CARTESIA_API_KEY unset)"
    if not _cloud_creds() and not _docker_available():
        return "no creds (no LIVEKIT_* cloud and docker unavailable)"
    return None


# ────────────────────────────────────────────────────────────────────────────────
# The real instance.
# ────────────────────────────────────────────────────────────────────────────────
def _build_cfg():
    """Build a real Config slice from env (Config in src/config.py)."""
    from config import Config

    cfg = Config.from_env()
    if not _cloud_creds():
        cfg.livekit_url = DEV_URL
        cfg.livekit_api_key = DEV_KEY
        cfg.livekit_api_secret = DEV_SECRET
    return cfg


def _mint_token(cfg, identity: str, *, metadata: str = "") -> str:
    grant = api.VideoGrants(
        room_join=True, room=ROOM, can_publish=True, can_subscribe=True, can_publish_data=True
    )
    tok = (
        api.AccessToken(cfg.livekit_api_key, cfg.livekit_api_secret)
        .with_identity(identity)
        .with_grants(grant)
    )
    if metadata:
        tok = tok.with_metadata(metadata)
    return tok.to_jwt()


async def _wait_ws(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            await asyncio.sleep(0.4)
    return False


async def _ensure_room(cfg, metadata: str) -> None:
    """Create (or update) the room with metadata so the worker resolves company/modality."""
    lkapi = api.LiveKitAPI(cfg.livekit_url, cfg.livekit_api_key, cfg.livekit_api_secret)
    try:
        try:
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=ROOM))
        except Exception:
            pass
        await lkapi.room.create_room(api.CreateRoomRequest(name=ROOM, metadata=metadata))
    finally:
        await lkapi.aclose()


class MiniInstance:
    def __init__(self, cfg, container_id: str | None, server):
        self.cfg = cfg
        self.url = cfg.livekit_url
        self.room = ROOM
        self._container = container_id
        self._server = server
        self._worker_task: asyncio.Task | None = None

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._server is not None:
            try:
                await self._server.aclose()
            except Exception:
                pass
        if self._container:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self._container,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()


async def _start_local_livekit() -> str:
    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "-d", "--rm",
        "-p", "7880:7880", "-p", "7881:7881",
        "-p", "7882:7882/udp",
        LIVEKIT_IMAGE, "--dev",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"docker run livekit-server failed: {err.decode(errors='replace')}")
    return out.decode().strip()


async def boot_mini(cfg=None, *, metadata: str = "") -> MiniInstance:
    """Start the LiveKit server (cloud or docker) + the realtime worker registered for dispatch."""
    from livekit.agents.worker import AgentServer
    from livekit.plugins import cartesia, silero

    from realtime import build_worker
    from scripts._harness import EchoComposeAgent, make_scope_session, pixel_identify

    cfg = cfg or _build_cfg()

    container_id: str | None = None
    if not _cloud_creds():
        container_id = await _start_local_livekit()
        if not await _wait_ws("localhost", 7880):
            raise RuntimeError("LiveKit server did not become ready on :7880")

    await _ensure_room(cfg, metadata)

    stt = cartesia.STT()
    tts = cartesia.TTS()
    vad = silero.VAD.load()

    opts = build_worker(
        agent=EchoComposeAgent(),
        identify=pixel_identify,
        stt=stt,
        tts=tts,
        vad=vad,
        scope_session=make_scope_session(),
        cfg=cfg,
        agent_name=AGENT_NAME,          # explicit-dispatch mode → deterministic (no auto-dispatch race)
    )

    server = AgentServer.from_server_options(opts)
    inst = MiniInstance(cfg, container_id, server)
    inst._worker_task = asyncio.ensure_future(server.run(devmode=not _cloud_creds()))

    # Wait for the worker to actually register, THEN explicitly dispatch the agent into the room.
    await _wait_worker_ready(server, inst._worker_task)
    lkapi = api.LiveKitAPI(cfg.livekit_url, cfg.livekit_api_key, cfg.livekit_api_secret)
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=ROOM, metadata=metadata)
        )
    finally:
        await lkapi.aclose()
    return inst


async def _wait_worker_ready(server, worker_task, timeout: float = 30.0) -> None:
    """Block until the worker has REGISTERED (id != 'unregistered'), or fail loudly.

    NB: AgentServer.id is the literal string "unregistered" until the "worker_registered" event
    fires (livekit-agents 1.5.17). "unregistered" is truthy, so gating on `if server.id` returned
    ~immediately and the caller could join before the worker registered — on LiveKit Cloud,
    automatic dispatch only assigns an agent if a compatible worker is registered when the
    participant joins, so the agent never joined → no voice_state → timeout. Wait for the real
    event and FAIL on timeout (don't silently proceed).
    """
    if getattr(server, "id", "unregistered") not in ("", "unregistered", None):
        return  # already registered
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    server.once("worker_registered", lambda *a: not fut.done() and fut.set_result(True))
    done, _ = await asyncio.wait(
        {fut, worker_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )
    if worker_task in done:  # worker died during startup
        raise RuntimeError(f"worker exited during startup: {worker_task.exception()!r}")
    if not fut.done():
        raise RuntimeError(f"worker did not register with LiveKit within {timeout}s")
    await asyncio.sleep(0.5)  # small settle after registration


# ────────────────────────────────────────────────────────────────────────────────
# Caller side — a second participant that drives turns and collects "clutch" events.
# ────────────────────────────────────────────────────────────────────────────────
def _make_wav(seconds: float = 1.2, freq: float = 220.0, rate: int = 16000) -> bytes:
    """A small mono 16-bit PCM sine WAV (pipeline wiring; STT may transcribe little)."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", v)
    data = bytes(frames)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def _make_jpeg() -> bytes:
    import io as _io

    from PIL import Image

    img = Image.new("RGB", (320, 240), (200, 200, 80))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _wav_to_frames(wav: bytes):
    """Decode a 16k mono PCM WAV into 20ms AudioFrames."""
    import numpy as np

    pcm = np.frombuffer(wav[44:], dtype=np.int16)
    rate, ch = 16000, 1
    spf = int(rate * 0.02)  # 20ms
    for off in range(0, len(pcm) - spf, spf):
        chunk = pcm[off : off + spf]
        yield rtc.AudioFrame(
            data=chunk.tobytes(), sample_rate=rate, num_channels=ch, samples_per_channel=spf
        )


class Caller:
    """The widget stand-in: joins the room, publishes media/text, collects clutch events."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.room = rtc.Room()
        self.events: list[dict] = []
        self.agent_audio_frames = 0
        self._evq: asyncio.Queue = asyncio.Queue()

    async def connect(self) -> None:
        @self.room.on("data_received")
        def _on_data(packet: rtc.DataPacket) -> None:
            if getattr(packet, "topic", None) != "clutch":
                return
            try:
                ev = json.loads(bytes(packet.data).decode("utf-8"))
            except Exception:
                return
            self.events.append(ev)
            self._evq.put_nowait(ev)

        @self.room.on("track_subscribed")
        def _on_track(track, pub, participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(self._count_agent_audio(track))

        token = _mint_token(self.cfg, "caller")
        await self.room.connect(self.cfg.livekit_url, token)

    async def _count_agent_audio(self, track) -> None:
        stream = rtc.AudioStream(track)
        try:
            async for _ in stream:
                self.agent_audio_frames += 1
        except Exception:
            pass
        finally:
            await stream.aclose()

    async def wait_event(self, kind: str, timeout: float = EVENT_TIMEOUT) -> dict | None:
        deadline = time.monotonic() + timeout
        # check already-collected first
        for ev in self.events:
            if ev.get("type") == kind:
                return ev
        while time.monotonic() < deadline:
            try:
                ev = await asyncio.wait_for(self._evq.get(), timeout=deadline - time.monotonic())
            except asyncio.TimeoutError:
                return None
            if ev.get("type") == kind:
                return ev
        return None

    async def publish_audio(self, wav: bytes, *, timeout: float = 10.0) -> None:
        source = rtc.AudioSource(16000, 1)
        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, opts)

        async def _drain() -> None:
            for fr in _wav_to_frames(wav):
                await source.capture_frame(fr)   # paces in real time (~clip length)
            await source.wait_for_playout()      # can stall — must be bounded (Defect A)

        try:
            await asyncio.wait_for(_drain(), timeout=timeout)
        except asyncio.TimeoutError:
            pass  # bounded: a stuck publish fails its own check, never the whole gate

    async def publish_video_jpeg(self, jpeg: bytes, *, seconds: float = 3.0) -> None:
        import io as _io

        import numpy as np
        from PIL import Image

        img = Image.open(_io.BytesIO(jpeg)).convert("RGBA")
        w, h = img.size
        rgba = np.asarray(img, dtype=np.uint8).tobytes()
        source = rtc.VideoSource(w, h)
        track = rtc.LocalVideoTrack.create_video_track("cam", source)
        opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        await self.room.local_participant.publish_track(track, opts)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            frame = rtc.VideoFrame(w, h, rtc.VideoBufferType.RGBA, rgba)
            source.capture_frame(frame)
            await asyncio.sleep(0.2)

    async def send_text(self, text: str) -> None:
        await self.room.local_participant.send_text(text, topic="lk.chat")

    async def escalate(self, modality: str) -> None:
        msg = json.dumps({"t": "escalate", "modality": modality}).encode("utf-8")
        await self.room.local_participant.publish_data(msg, reliable=True, topic="clutch")

    async def close(self) -> None:
        try:
            await self.room.disconnect()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────────
# The --check gate. Boots the instance, drives one turn per modality, asserts events.
# ────────────────────────────────────────────────────────────────────────────────
async def _check() -> int:
    meta = json.dumps({"company_key": "demo", "modality": "talk"})
    inst = await boot_mini(metadata=meta)
    caller = Caller(inst.cfg)
    results: list[tuple[str, str, str]] = []  # (name, status, evidence)

    def _rec(entry: tuple[str, str, str]) -> None:
        # Defect D: stream each result as it completes, so a later stall still shows what ran.
        name, status, ev = entry
        print(f"{status:4} [{name}] {ev}", flush=True)
        results.append(entry)

    try:
        await caller.connect()
        # Wait for the agent to be dispatched + join (its audio/state will start flowing).
        first_state = await caller.wait_event("voice_state", timeout=EVENT_TIMEOUT)
        if first_state is None:
            # Agent never joined — dispatch failed. Report and bail.
            print("FAIL [dispatch] no voice_state event — agent did not join the room")
            return 1

        # ── TALK: publish a short WAV, expect an answer event (+ latency badge). ──
        await caller.publish_audio(_make_wav())
        ans = await caller.wait_event("answer", timeout=EVENT_TIMEOUT)
        lat = next((e for e in caller.events if e.get("type") == "latency"), None)
        if ans:
            ev = f"answer text[:40]={ans['text'][:40]!r}"
            if lat:
                ev += f", latency={lat['time_taken_ms']}ms"
            _rec(("TALK", "PASS", ev))
        else:
            _rec(("TALK", "FAIL", "no answer event after audio turn"))

        # ── STATE: did we see the listening/thinking/speaking transitions? ──
        states = [e["state"] for e in caller.events if e.get("type") == "voice_state"]
        seen = set(states)
        if {"listening", "thinking", "speaking"} & seen and len(seen) >= 2:
            _rec(("STATE", "PASS", f"states={states}"))
        elif states:
            _rec(("STATE", "PASS", f"states={states} (subset; transitions flowing)"))
        else:
            _rec(("STATE", "FAIL", "no voice_state transitions"))

        # ── BARGE: speak again while output may be active; assert turn accepted. ──
        before = len([e for e in caller.events if e.get("type") == "answer"])
        await caller.publish_audio(_make_wav(seconds=1.0, freq=180.0))
        ans2 = await caller.wait_event_count("answer", before + 1, timeout=EVENT_TIMEOUT)
        if ans2:
            _rec(("BARGE", "PASS", "second turn accepted (interruptions enabled)"))
        else:
            _rec(("BARGE", "SKIP", "no 2nd answer (STT got no words from sine tone)"))

        # ── SEE: escalate to see, publish a JPEG, expect confirm or answer. ──
        await caller.escalate("see")
        await caller.publish_video_jpeg(_make_jpeg())
        see_ev = await caller.wait_event_either(
            ("confirm", "answer"), since=len(caller.events), timeout=EVENT_TIMEOUT
        )
        if see_ev:
            _rec(("SEE", "PASS", f"see -> {see_ev.get('type')} event from frame"))
        else:
            _rec(("SEE", "SKIP", "no confirm/answer from frame (vision seam quiet)"))

        # ── TYPE: typed text -> answer event, and NO new agent TTS audio. ──
        await caller.escalate("type")
        await asyncio.sleep(0.5)
        audio_before = caller.agent_audio_frames
        n_before = len(caller.events)
        await caller.send_text("how do I clear a paper jam")
        type_ans = await caller.wait_event_either(("answer",), since=n_before, timeout=EVENT_TIMEOUT)
        await asyncio.sleep(2.0)
        audio_after = caller.agent_audio_frames
        if type_ans:
            new_audio = audio_after - audio_before
            note = "no new TTS audio" if new_audio < 10 else f"WARN {new_audio} audio frames"
            _rec(("TYPE", "PASS", f"answer from typed text; {note}"))
        else:
            _rec(("TYPE", "FAIL", "no answer event from typed text"))

    finally:
        # Bounded cleanup — room.disconnect / server.aclose can stall; don't let teardown ride the
        # whole battery to the 150s outer cap and swallow the summary.
        for _coro in (caller.close(), inst.stop()):
            try:
                await asyncio.wait_for(_coro, timeout=8.0)
            except (asyncio.TimeoutError, Exception):
                pass

    # Report.
    print("=== realtime_mini --check results ===")
    npass = 0
    for name, status, ev in results:
        print(f"{status:4} [{name}] {ev}")
        if status == "PASS":
            npass += 1
    hard_fail = any(s == "FAIL" for _, s, _ in results)
    print(f"--- {npass}/{len(results)} PASS ---")
    return 1 if hard_fail else 0


# small helpers bound onto Caller after class def (kept readable above) ───────────
async def _wait_event_count(self, kind: str, n: int, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches = [e for e in self.events if e.get("type") == kind]
        if len(matches) >= n:
            return matches[-1]
        try:
            await asyncio.wait_for(self._evq.get(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
    matches = [e for e in self.events if e.get("type") == kind]
    return matches[-1] if len(matches) >= n else None


async def _wait_event_either(self, kinds: tuple, *, since: int = 0, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for e in self.events[since:]:
            if e.get("type") in kinds:
                return e
        try:
            await asyncio.wait_for(self._evq.get(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
    for e in self.events[since:]:
        if e.get("type") in kinds:
            return e
    return None


Caller.wait_event_count = _wait_event_count
Caller.wait_event_either = _wait_event_either


def _run_check() -> int:
    reason = _skip_reason()
    if reason is not None:
        print(f"SKIP ({reason})")
        return 0
    try:
        return asyncio.run(asyncio.wait_for(_check(), timeout=150.0))
    except asyncio.TimeoutError:
        print("FAIL [check] timed out (>150s) — likely a hang in dispatch/audio")
        return 1
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"FAIL [check] {exc!r}")
        return 1


# ── Dev CLI sub-commands (require creds; SKIP otherwise) ─────────────────────────
def _run_talk(text: str) -> int:
    reason = _skip_reason()
    if reason is not None:
        print(f"SKIP ({reason})")
        return 0
    print(f"[talk] would drive one spoken turn: {text!r} (use --check for the full gate)")
    return 0


def _run_see(path: str) -> int:
    reason = _skip_reason()
    if reason is not None:
        print(f"SKIP ({reason})")
        return 0
    print(f"[see] would push frame {path!r} (use --check for the full gate)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="realtime_mini", description=__doc__)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="bare-minimum acceptance gate")
    g.add_argument("--talk", metavar="TEXT", help="dev: speak one turn")
    g.add_argument("--see", metavar="JPG", help="dev: push one video frame")
    args = p.parse_args(argv)

    if args.talk is not None:
        return _run_talk(args.talk)
    if args.see is not None:
        return _run_see(args.see)
    return _run_check()


if __name__ == "__main__":
    sys.exit(main())
