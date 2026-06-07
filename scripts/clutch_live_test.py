"""Prove the PRODUCTION auto-dispatch loop: a caller joins a room, the already-registered
run_clutch worker auto-dispatches the brain, and a text turn returns a grounded answer.

Run the worker first (separately):  uv run python scripts/run_clutch.py start
Then:                               uv run python -m scripts.clutch_live_test
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

from scripts.realtime_mini import Caller, _build_cfg, _ensure_room  # noqa: E402


async def _run() -> None:
    cfg = _build_cfg()
    meta = json.dumps({"company_key": "demo", "modality": "talk"})
    await _ensure_room(cfg, meta)                       # fresh room → caller-join triggers auto-dispatch
    caller = Caller(cfg)

    @caller.room.on("data_received")
    def _d(pkt):  # noqa: ANN001
        try:
            print("  >>", bytes(pkt.data).decode("utf-8")[:160], flush=True)
        except Exception:
            pass

    await caller.connect()
    print("caller joined; waiting for auto-dispatched agent...")
    for _ in range(25):
        await asyncio.sleep(1)
        if caller.room.remote_participants:
            print(f"AGENT JOINED via auto-dispatch: {[p.identity for p in caller.room.remote_participants.values()]}")
            break
    else:
        print("FAIL: no agent auto-dispatched")
        await caller.close(); return

    print("sending text: 'how do I clear a paper jam'")
    await caller.send_text("how do I clear a paper jam")
    ans = await caller.wait_event("answer", timeout=30)
    await asyncio.sleep(1)
    print("--- RESULT ---")
    print("voice_state seen:", any(e.get("type") == "voice_state" for e in caller.events))
    print("ANSWER:", ans["text"] if ans else "NONE")
    print("citations:", ans.get("citations") if ans else None)
    await caller.close()


def main() -> int:
    try:
        asyncio.run(asyncio.wait_for(_run(), timeout=75.0))
        return 0
    except asyncio.TimeoutError:
        print("timed out")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
