"""Live-camera smoke test for perception (HLD 02) against the REAL TrueFoundry gateway.

Captures a burst from your webcam, runs the real triage + closed-set identify path
(select_best_frames -> Qwen-VL via gateway -> normalized IdentifyResult), and prints the result.
A snapshot of the frame that was actually sent is saved so you can see what the model saw.

    uv run python scripts/camera_identify.py [--cam N] [--warmup 15] [--burst 12]

Env (.env): TRUEFOUNDRY_BASE_URL, TRUEFOUNDRY_API_KEY, VISION_MODEL.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv          # noqa: E402
load_dotenv()

import asyncio                          # noqa: E402

import cv2                              # noqa: E402

from contracts import CatalogEntry      # noqa: E402
from perception import make_identify, select_best_frames   # noqa: E402

# Closed-set catalog: the target product + a decoy, so the match is a real discrimination test.
CATALOG = [
    CatalogEntry(product_id="laptop-1", name="Laptop Computer", brand="",
                 aliases=["laptop", "notebook", "macbook", "thinkpad"],
                 description="A portable laptop computer: hinged screen above a keyboard and trackpad."),
    CatalogEntry(product_id="router-1", name="Wi-Fi Router", brand="",
                 aliases=["router", "modem", "access point"],
                 description="A home wireless router: small box with external antennas and status LEDs."),
]


def capture_burst(cam: int, warmup: int, burst: int) -> list[bytes]:
    cap = cv2.VideoCapture(cam, cv2.CAP_DSHOW)
    if not cap.isOpened():
        sys.exit(f"could not open camera index {cam}")
    try:
        for _ in range(warmup):          # let exposure/white-balance settle
            cap.read()
        for n in (3, 2, 1):
            print(f"  capturing in {n}...", flush=True)
            time.sleep(1.0)
        print("  >>> CAPTURING <<<", flush=True)
        frames: list[bytes] = []
        for _ in range(burst):
            ok, frame = cap.read()
            if ok and frame is not None:
                enc, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if enc:
                    frames.append(buf.tobytes())
            time.sleep(0.05)
        return frames
    finally:
        cap.release()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--warmup", type=int, default=15)
    ap.add_argument("--burst", type=int, default=12)
    args = ap.parse_args()

    print(f"camera #{args.cam} — point it at the device, hold steady...")
    frames = capture_burst(args.cam, args.warmup, args.burst)
    print(f"captured {len(frames)} frames")
    if not frames:
        sys.exit("no frames captured")

    best = select_best_frames(frames, k=2)
    snap = Path(__file__).resolve().parent.parent / "camera_snapshot.jpg"
    snap.write_bytes(best[0])
    print(f"sent {len(best)} frame(s) to the model; snapshot of frame[0] -> {snap}")

    from openai import AsyncOpenAI
    gw = AsyncOpenAI(base_url=os.environ["TRUEFOUNDRY_BASE_URL"],
                     api_key=os.environ["TRUEFOUNDRY_API_KEY"])
    identify = make_identify(gw, model=os.environ["VISION_MODEL"])

    r = await identify(best, CATALOG)
    print("-" * 50)
    print("product_id        :", r.product_id)
    print("confidence        :", round(r.confidence, 3))
    print("model_source      :", r.model_source)
    print("brand / model     :", r.brand, "/", r.model)
    print("raw_text (OCR)    :", r.raw_text)
    print("needs_confirmation:", r.needs_confirmation)
    print("problem.summary   :", r.problem.summary)
    print("problem.to_query  :", r.problem.to_query())
    print("candidates        :", [(c.product_id, round(c.confidence, 3)) for c in r.candidates])
    print("resolved          :", r.resolved)


if __name__ == "__main__":
    asyncio.run(main())
