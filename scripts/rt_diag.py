"""Focused realtime diagnostic — verify the brain-as-LLM wiring (voice_state + Type answer).

Boots the real worker (Fardin's harness) against LiveKit Cloud, joins a caller, logs every data
packet, then sends a TEXT turn and checks an answer event flows back. Run: uv run python -m scripts.rt_diag
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scripts.realtime_mini import Caller, boot_mini  # noqa: E402


async def _diag() -> None:
    meta = json.dumps({"company_key": "demo", "modality": "talk"})
    print("booting worker...")
    inst = await boot_mini(metadata=meta)
    caller = Caller(inst.cfg)

    @caller.room.on("data_received")
    def _all(pkt):  # noqa: ANN001
        try:
            body = bytes(pkt.data).decode("utf-8")[:140]
        except Exception:
            body = "<binary>"
        print(f"  >> DATA topic={getattr(pkt,'topic',None)!r}: {body}", flush=True)

    await caller.connect()
    print(f"caller connected; remote: {[p.identity for p in caller.room.remote_participants.values()]}")
    for _ in range(20):
        await asyncio.sleep(1)
        if caller.room.remote_participants:
            break
    print(f"agent present: {[p.identity for p in caller.room.remote_participants.values()]}")

    print("sending TEXT turn: 'how do I clear a paper jam'")
    await caller.send_text("how do I clear a paper jam")
    ans = await caller.wait_event("answer", timeout=30)
    await asyncio.sleep(2)

    print("--- SUMMARY ---")
    print(f"  voice_state seen : {any(e.get('type')=='voice_state' for e in caller.events)}")
    print(f"  answer event     : {ans['text'][:120] if ans else 'NONE'}")
    print(f"  all events       : {[e.get('type') for e in caller.events]}")
    await caller.close()
    await inst.stop()


def main() -> int:
    try:
        asyncio.run(asyncio.wait_for(_diag(), timeout=90.0))
        return 0
    except asyncio.TimeoutError:
        print("DIAG timed out")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
