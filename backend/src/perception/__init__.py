"""Perception: turn camera frames into structured product + problem signals.

Public surface (seam S3 + the realtime gate):
    identify(frames, catalog, *, provider) -> IdentifyResult   # one VLM call per gated look
    make_identify(gateway_client, *, model)                    # real path: bind Qwen-via-gateway
    FrameGate                                                  # across-feed change gate (hot loop, S8)
    select_best_frames(frames, k)                              # within-burst best-of
    MockVisionProvider, canned_identify                        # mocks for solo/downstream build
"""

from src.perception.frames import FrameGate, select_best_frames
from src.perception.identify import identify, make_identify
from src.perception.look import look, make_look
from src.perception.providers.mock import MockVisionProvider, canned_identify
from src.perception.providers.qwen_gateway import QwenGatewayProvider

__all__ = [
    "identify",
    "make_identify",
    "look",
    "make_look",
    "FrameGate",
    "select_best_frames",
    "MockVisionProvider",
    "QwenGatewayProvider",
    "canned_identify",
]
