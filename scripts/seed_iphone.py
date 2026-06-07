"""Seed the `clutch-demo` Moss index with dummy iPhone 15 troubleshooting docs (product_id iphone-15).

Lets us run the full See+Talk loop against a device we actually have. Recreates the index.

    uv run python -m scripts.seed_iphone
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
load_dotenv(_ROOT / ".env.local")
load_dotenv(_ROOT / ".env")

INDEX = os.getenv("CLUTCH_INDEX", "clutch-demo")
MODEL_ID = os.getenv("MOSS_MODEL_ID", "moss-minilm")
PID = "iphone-15"
SRC = "iPhone 15 User Guide.pdf"

# (id, section, text)
_ROWS = [
    ("iphone-restart", "Force Restart",
     "To force restart iPhone 15 when the screen is frozen or unresponsive: press and quickly "
     "release the Volume Up button, press and quickly release the Volume Down button, then press "
     "and hold the Side button until the Apple logo appears, then release."),
    ("iphone-charging", "Charging Problems",
     "If your iPhone 15 will not charge, inspect the USB-C charging port for lint or debris and "
     "clean it gently with a dry soft brush. Try a different USB-C cable and a known-good power "
     "adapter. A red low-battery icon means it is too low to power on; charge for at least 15 minutes."),
    ("iphone-battery", "Battery Health",
     "Check battery condition in Settings, then Battery, then Battery Health and Charging. If "
     "Maximum Capacity is below 80 percent the battery is degraded and should be serviced. Turn on "
     "Optimized Battery Charging to slow battery aging."),
    ("iphone-faceid", "Face ID Not Working",
     "If Face ID is not working, make sure nothing covers the TrueDepth camera at the top of the "
     "screen and clean it. Hold the phone 25 to 50 cm from your face. If it still fails, reset it in "
     "Settings, Face ID and Passcode, then set up Face ID again."),
    ("iphone-overheat", "Overheating",
     "If the iPhone becomes hot, remove any case, move it out of direct sunlight, and stop charging. "
     "A temperature warning screen means you must let it cool before using it again. Prolonged gaming "
     "or video recording can cause temporary warming."),
    ("iphone-sound", "No Sound",
     "If there is no sound, check that the Ring/Silent switch on the left edge is not set to silent "
     "(orange visible means silent). Raise the volume, clean the speaker grilles at the bottom, and "
     "turn off Bluetooth in case audio is routing to another device."),
    ("iphone-screen", "Cracked Screen",
     "A cracked screen should be repaired by an authorized service provider. Warning: do not keep "
     "using a badly cracked screen, as broken glass can cut you. Back up your data before any repair."),
    ("iphone-camera", "Camera Issues",
     "If the rear camera looks blurry, clean the lens and remove any case lip blocking it. If the "
     "camera shows a black screen, force restart the phone. A missing or damaged rear camera cover "
     "exposes the lenses and should be serviced."),
]


async def main() -> int:
    from moss import DocumentInfo, MossClient

    c = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
    existing = {ix.name for ix in await c.list_indexes()}
    if INDEX in existing:
        print(f"deleting existing index {INDEX!r} ...")
        await c.delete_index(INDEX)

    docs = [
        DocumentInfo(id=cid, text=text,
                     metadata={"source": SRC, "section": section, "product_id": PID})
        for cid, section, text in _ROWS
    ]
    print(f"creating {INDEX!r} with {len(docs)} iPhone docs (model {MODEL_ID}) ...")
    res = await c.create_index(INDEX, docs, MODEL_ID)
    print(f"  created: docs={getattr(res, 'doc_count', None)}")
    name = await c.load_index(INDEX, auto_refresh=False)
    print(f"  loaded: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
