"""EventPublisher (S1) — the single writer of the four typed UI events.

Serializes via `events.dumps` and writes to the LiveKit **reliable** data channel on the
dedicated ``"clutch"`` topic (separate from the framework's reserved `lk.transcription` /
`lk.chat` text streams). Reliable delivery so `answer`/`confirm` cards are never dropped
(HLD 05 §6).

`room.local_participant.publish_data` is a coroutine in livekit-rtc, so `send` schedules it
as a fire-and-forget task — the publisher must never block the voice loop.
"""

from __future__ import annotations

import asyncio
import logging

from livekit import rtc

from src.events import ConfirmEvent, Event, VoiceStateEvent, dumps

logger = logging.getLogger("clutch.realtime.publisher")


class EventPublisher:
    def __init__(self, room: rtc.Room, topic: str = "clutch"):
        self._room = room
        self._topic = topic
        self._tasks: set[asyncio.Task] = set()

    def send(self, e: Event) -> None:
        """Fire-and-forget publish of one typed event on the reliable 'clutch' topic."""
        payload = dumps(e)
        coro = self._room.local_participant.publish_data(
            payload, reliable=True, topic=self._topic
        )
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            # No running loop (shouldn't happen inside the session) — best effort, drop.
            logger.warning("EventPublisher.send called with no event loop; event dropped")
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_failure)

    @staticmethod
    def _log_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("data-channel publish failed: %r", exc)

    # convenience wrappers used by the session / state machine ────────────────
    def confirm(self, prompt: str, options: list[str]) -> None:
        self.send(ConfirmEvent(prompt=prompt, options=options))

    def voice(self, state: str) -> None:
        self.send(VoiceStateEvent(state))  # type: ignore[arg-type]
