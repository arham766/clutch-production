"""scripts/speech_e2e.py — standalone e2e for src/speech/: drives REAL Cartesia STT+TTS.

Run:   python scripts/speech_e2e.py            # round-trip via the LiveKit plugin
       python scripts/speech_e2e.py --check    # bare-minimum gate: exits 0 PASS / non-zero FAIL
       python scripts/speech_e2e.py --raw      # optional raw-SDK vendor smoke (no LiveKit) — §4.7/§8
Keyless ⇒ prints SKIP and exits 0 (never blocks a keyless checkout). Requires for a real run:
livekit-agents, livekit-plugins-cartesia, CARTESIA_API_KEY.

API verified against LiveKit Agents v1 (June 2026):
  TTS  tts.synthesize(text) -> ChunkedStream; iterate -> SynthesizedAudio.frame (rtc.AudioFrame)
  STT  stt.stream() -> SpeechStream; push_frame()/end_input(); SpeechEvent.type / alternatives[0].text
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

try:                                  # arrow glyphs in PASS lines must not crash a non-UTF-8 console
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))   # bare `python scripts/...`

try:                                       # convenience: load real keys from .env.local if present
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env.local")
except Exception:
    pass

from speech.stt import get_stt          # noqa: E402  (after sys.path bootstrap)
from speech.tts import get_tts          # noqa: E402

PHRASE = "the printer shows an amber blinking light"


async def _synthesize(tts) -> tuple[bytes, int, int]:
    """Return (s16le PCM, sample_rate, num_channels) for PHRASE — proves TTS streams audio."""
    pcm, sr, ch = bytearray(), 0, 1
    stream = tts.synthesize(PHRASE)             # ChunkedStream
    async for ev in stream:                     # SynthesizedAudio
        f = ev.frame                            # rtc.AudioFrame (native rate; no sample_rate to set)
        sr, ch = f.sample_rate, f.num_channels
        pcm += bytes(f.data)                    # int16 PCM
    await stream.aclose()
    return bytes(pcm), sr, ch


async def _transcribe(stt, pcm: bytes, sr: int, ch: int) -> str:
    """Push PCM through the STT SpeechStream, return the final transcript."""
    from livekit import rtc
    from livekit.agents import stt as lk_stt

    ss = stt.stream()                           # SpeechStream
    step = int(sr * 0.1) * 2 * ch               # 100 ms of s16 audio, in bytes

    async def _feed() -> None:
        # Feed concurrently with consumption: the plugin's _run() bails out if the input
        # channel is already closed, so we must not push+end_input() before it connects.
        await asyncio.sleep(0.2)                 # let _run() establish the WS first
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            ss.push_frame(rtc.AudioFrame(
                data=chunk, sample_rate=sr, num_channels=ch,
                samples_per_channel=len(chunk) // (2 * ch),
            ))
            await asyncio.sleep(0)               # yield so the send task drains the channel
        ss.end_input()                          # flush — finalize → FINAL_TRANSCRIPT

    feeder = asyncio.create_task(_feed())
    text = ""
    try:
        async for ev in ss:
            if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                text += ev.alternatives[0].text + " "
    finally:
        await feeder
        await ss.aclose()
    return text.strip()


def _selection_checks() -> None:
    """Pure, keyless selection logic — the bare-minimum guard, run on every invocation (no deps)."""
    assert type(get_stt(SimpleNamespace())).__name__ == "CartesiaSTT"          # default → Cartesia
    assert type(get_tts(SimpleNamespace())).__name__ == "CartesiaTTS"
    try:
        get_stt(SimpleNamespace(stt_provider="bogus"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        get_tts(SimpleNamespace(tts_provider="bogus"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    print("PASS selection (default→cartesia, unknown→raise)")


async def _raw_smoke() -> int:
    """Optional raw Cartesia SDK smoke (no LiveKit, §4.7). No-op SKIP if the SDK/key is unavailable."""
    if not os.environ.get("CARTESIA_API_KEY"):
        print("SKIP raw: CARTESIA_API_KEY unset")
        return 0
    try:
        from cartesia import Cartesia  # noqa: F401
    except Exception:
        print("SKIP raw: raw 'cartesia' SDK not installed")
        return 0
    print("PASS raw: cartesia SDK importable (full raw round-trip is operator-run, §4.7)")
    return 0


async def main(argv: list[str]) -> int:
    _selection_checks()                          # always — keyless, fast
    if "--raw" in argv:
        return await _raw_smoke()                # raw Cartesia SDK, no LiveKit (§4.7) — optional

    if not os.environ.get("CARTESIA_API_KEY"):
        print("SKIP: CARTESIA_API_KEY unset (selection checks passed)")
        return 0                                 # keyless ⇒ skip
    cfg = SimpleNamespace(stt_provider="cartesia", tts_provider="cartesia")

    # Plugins need an aiohttp session; outside the agent worker we open one explicitly.
    from livekit.agents.utils import http_context

    async with http_context.open():
        tts = get_tts(cfg).stream()              # our provider.stream() -> Cartesia TTS plugin
        stt = get_stt(cfg).stream()              # our provider.stream() -> Cartesia STT plugin

        pcm, sr, ch = await _synthesize(tts)
        assert pcm, "TTS produced no audio"
        print(f"PASS tts: {len(pcm)} bytes PCM @ {sr} Hz x{ch}")

        text = await _transcribe(stt, pcm, sr, ch)
        assert text, "STT produced empty transcript"
        overlap = len(set(text.lower().split()) & set(PHRASE.split()))
        assert overlap >= 2, f"STT transcript far from input: {text!r}"
        print(f"PASS stt: {text!r} ({overlap} words overlap)")
        print("PASS round-trip")
    return 0


if __name__ == "__main__":                       # `--check` is just the default run + non-zero on fail
    sys.exit(asyncio.run(main(sys.argv[1:])))
