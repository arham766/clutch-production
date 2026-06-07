"""Deterministic hot-path latency probe — sends a text turn, measures send->first spoken audio.

Isolates the controllable layers (retrieve + LLM TTFT race + TTS) without flaky live STT. Run with
the worker already up. The worker logs [latency] retrieve/llm_first_token for the same turn.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scripts.realtime_mini import Caller, _build_cfg, _ensure_room  # noqa: E402


async def main() -> None:
    cfg = _build_cfg()
    await _ensure_room(cfg, json.dumps({"company_key": "demo", "modality": "talk"}))
    c = Caller(cfg)
    await c.connect()
    if not await c.wait_event("voice_state", timeout=15):
        print("agent never joined"); await c.close(); return

    for q in ["how do I force restart my iphone", "my iphone wont charge"]:
        base_audio = c.agent_audio_frames
        n0 = len(c.events)
        t0 = time.monotonic()
        await c.send_text(q)
        t_audio = None
        while time.monotonic() - t0 < 15:
            if c.agent_audio_frames > base_audio + 2:
                t_audio = (time.monotonic() - t0) * 1000
                break
            await asyncio.sleep(0.02)
        ans = next((e for e in c.events[n0:] if e.get("type") == "answer"), None)
        print(f"\nQ: {q!r}")
        print(f"  send -> first spoken audio = {t_audio:.0f} ms" if t_audio else "  (no audio)")
        print(f"  answer: {ans['text'][:90] if ans else 'none'}")
        await asyncio.sleep(3)   # let it finish before next turn
    await c.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60))
