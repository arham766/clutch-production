"""on_control_data — decode custom 'clutch'-topic control messages (modality escalate).

The widget escalates modality **within the same room** (HLD 05 §3) by sending a small JSON
control message on the dedicated ``"clutch"`` data topic, e.g.::

    {"t": "escalate", "modality": "talk"}   # or "see" / "type"

We decode it and toggle the live session's input tracks via
``session.input.set_audio_enabled(...)`` / ``set_video_enabled(...)``. Everything else
(`history`, `product_id`) carries over because the session never restarts.

This is registered as a ``RoomEvent.DataReceived`` handler filtered on ``topic == "clutch"``.
The handler is sync (LiveKit emits these synchronously), so it schedules the async toggle work.
"""

from __future__ import annotations

import asyncio
import json
import logging

from livekit import rtc

logger = logging.getLogger("clutch.realtime.intents")

_VALID = ("type", "talk", "see")


def _apply_modality(session, modality: str) -> None:
    """Toggle the live session's input tracks for the new modality."""
    audio = modality in ("talk", "see")
    video = modality == "see"
    try:
        session.input.set_audio_enabled(audio)
        session.input.set_video_enabled(video)
    except Exception as exc:
        logger.warning("failed to toggle modality %s: %r", modality, exc)


def on_control_data(session, *, topic: str = "clutch"):
    """Return a `RoomEvent.DataReceived` handler that decodes 'clutch'-topic control msgs."""

    def _handler(packet: rtc.DataPacket) -> None:
        if getattr(packet, "topic", None) != topic:
            return
        try:
            msg = json.loads(bytes(packet.data).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            logger.debug("ignoring undecodable control msg: %r", exc)
            return
        if not isinstance(msg, dict) or msg.get("t") != "escalate":
            return
        modality = msg.get("modality")
        if modality not in _VALID:
            logger.debug("ignoring escalate with bad modality: %r", modality)
            return
        logger.info("modality escalate -> %s", modality)
        # Toggle on the running loop without blocking the (sync) event callback.
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(_apply_modality, session, modality)

    return _handler
