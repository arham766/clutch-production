"""
Clutch data-channel event schema — part of the Spine.

Defines the typed JSON events the server (Fardin's ``realtime/``) publishes
over the LiveKit data channel and the widget (Arman's ``widget/``) renders.

Mirrored in ``widget/src/events.ts`` (TypeScript). Changes to this file are
a Spine PR requiring all-hands review (HLD 10 §3).

Reference: HLD 05 §4 (Realtime, Voice & Widget — data-channel events).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


EventType = Literal["answer", "confirm", "voice_state", "latency"]


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CitationData:
    """A single citation in an answer — provenance for one claim."""

    doc: str
    section: str
    score: float


@dataclass(frozen=True, slots=True)
class AnswerPayload:
    """Grounded answer with inline citations."""

    text: str
    citations: list[CitationData] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConfirmPayload:
    """Product-confirmation prompt (when ``needs_confirmation`` is set)."""

    prompt: str
    options: list[str] = field(default_factory=list)


VoiceState = Literal["listening", "speaking", "thinking"]


@dataclass(frozen=True, slots=True)
class VoiceStatePayload:
    """Current voice pipeline state — drives the UI indicator."""

    state: VoiceState


@dataclass(frozen=True, slots=True)
class LatencyPayload:
    """Real Moss retrieval latency. Hidden in the UI when absent (never faked)."""

    time_taken_ms: float


# ---------------------------------------------------------------------------
# Envelope — the top-level event shape sent over the data channel
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChannelEvent:
    """Top-level event envelope. ``type`` discriminates the payload.

    Serialized as JSON over the LiveKit data channel::

        {"type": "answer",  "data": {"text": "...", "citations": [...]}}
        {"type": "confirm", "data": {"prompt": "...", "options": [...]}}
        {"type": "voice_state", "data": {"state": "listening"}}
        {"type": "latency", "data": {"time_taken_ms": 42.5}}
    """

    type: EventType
    data: AnswerPayload | ConfirmPayload | VoiceStatePayload | LatencyPayload

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict for data-channel publishing."""
        import dataclasses

        return {
            "type": self.type,
            "data": dataclasses.asdict(self.data),
        }

    @classmethod
    def answer(cls, text: str, citations: list[CitationData] | None = None) -> ChannelEvent:
        """Factory for an answer event."""
        return cls(type="answer", data=AnswerPayload(text=text, citations=citations or []))

    @classmethod
    def confirm(cls, prompt: str, options: list[str]) -> ChannelEvent:
        """Factory for a confirm event."""
        return cls(type="confirm", data=ConfirmPayload(prompt=prompt, options=options))

    @classmethod
    def voice_state(cls, state: VoiceState) -> ChannelEvent:
        """Factory for a voice-state event."""
        return cls(type="voice_state", data=VoiceStatePayload(state=state))

    @classmethod
    def latency(cls, time_taken_ms: float) -> ChannelEvent:
        """Factory for a latency event."""
        return cls(type="latency", data=LatencyPayload(time_taken_ms=time_taken_ms))
