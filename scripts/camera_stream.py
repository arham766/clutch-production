"""Continuous live-camera perception (HLD 02 hot-path loop, seam S8) against the REAL gateway.

Streams frames from the webcam. A FrameGate fires only when the view *meaningfully changes*
(pHash Hamming >= change_distance) and a rate cap has elapsed -> exactly one Qwen-VL call per
change. Holds still -> zero calls. Move to a new device/part -> one call. Each gated look prints a
live line and updates camera_live.jpg (the frame the model actually saw).

    uv run python scripts/camera_stream.py [--cam 0] [--seconds 120] [--change-distance 10] [--min-interval 1.0]

Ctrl-C to stop early. Env (.env): TRUEFOUNDRY_BASE_URL, TRUEFOUNDRY_API_KEY, VISION_MODEL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv          # noqa: E402
load_dotenv()

import cv2                              # noqa: E402

from contracts import CatalogEntry      # noqa: E402
from perception import make_identify, FrameGate   # noqa: E402

CATALOG = [
    CatalogEntry(product_id="laptop-1", name="Laptop Computer", brand="",
                 aliases=["laptop", "notebook", "macbook", "thinkpad"],
                 description="A portable laptop computer: hinged screen above a keyboard and trackpad."),
    CatalogEntry(product_id="router-1", name="Wi-Fi Router", brand="",
                 aliases=["router", "modem", "access point"],
                 description="A home wireless router: small box with external antennas and status LEDs."),
    CatalogEntry(product_id="phone-1", name="Smartphone", brand="",
                 aliases=["phone", "iphone", "android", "mobile"],
                 description="A handheld smartphone with a touchscreen."),
]


def _jpg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else b""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--change-distance", type=int, default=10)
    ap.add_argument("--min-interval", type=float, default=1.0)
    args = ap.parse_args()

    gw = __import__("openai").AsyncOpenAI(
        base_url=os.environ["TRUEFOUNDRY_BASE_URL"], api_key=os.environ["TRUEFOUNDRY_API_KEY"])
    identify = make_identify(gw, model=os.environ["VISION_MODEL"])
    gate = FrameGate(change_distance=args.change_distance, min_interval_s=args.min_interval)

    cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW)
    if not cap.isOpened():
        sys.exit(f"could not open camera index {args.cam}")
    for _ in range(15):                  # warmup: let exposure settle
        cap.read()

    snap = Path(__file__).resolve().parent.parent / "camera_live.jpg"
    print(f"streaming cam #{args.cam} for {args.seconds:.0f}s "
          f"(change>={args.change_distance}, rate-cap={args.min_interval}s). Move to a device to trigger a look.",
          flush=True)

    t0 = time.monotonic()
    looks = 0
    try:
        while time.monotonic() - t0 < args.seconds:
            ok, frame = cap.read()
            if not ok or frame is None:
                await asyncio.sleep(0.02)
                continue
            b = _jpg(frame)
            if not b or not gate.should_process(b):
                await asyncio.sleep(0.03)         # idle: cheap, no VLM call
                continue

            # --- gated: the view changed -> ONE vision call ---
            looks += 1
            snap.write_bytes(b)
            t = time.monotonic() - t0
            try:
                r = await identify([b], CATALOG)
            except Exception as e:
                print(f"[t={t:5.1f}s] look#{looks} VLM ERROR: {type(e).__name__} {str(e)[:80]}", flush=True)
                continue
            pid = r.product_id or "?"
            prob = (r.problem.summary or "").strip()
            flag = "CONFIRM" if r.needs_confirmation else "RESOLVED"
            print(f"[t={t:5.1f}s] look#{looks} {flag:8} {pid:9} conf={r.confidence:0.2f} "
                  f"src={r.model_source:12} | {prob[:90]}", flush=True)
            # flush stale buffered frames accumulated during the await
            for _ in range(3):
                cap.read()
    except KeyboardInterrupt:
        print("stopped.", flush=True)
    finally:
        cap.release()
        print(f"done. {looks} gated look(s) in {time.monotonic()-t0:0.0f}s.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
