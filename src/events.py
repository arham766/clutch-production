"""Typed data-channel events (Spine — LLD 05-realtime §2).

Frozen wire contract between src/realtime/ (sole producer) and the widget's events.ts.
Carries no logic — four dataclasses + a dumps() helper. `latency` is OMITTED entirely
when the real number is unknown (never a fake 0).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal, Union

EventType = Literal["answer", "confirm", "voice_state", "latency"]
VoiceState = Literal["listening", "speaking", "thinking", "idle"]


@dataclass
class AnswerEvent:                                   # 04/03 -> widget: grounded cited card
    text: str
    citations: list[dict] = field(default_factory=list)   # [{doc, section, score}]
    type: Literal["answer"] = "answer"


@dataclass
class ConfirmEvent:                                  # 04 -> widget: "Is this the <X>?"
    prompt: str
    options: list[str] = field(default_factory=list)
    type: Literal["confirm"] = "confirm"


@dataclass
class VoiceStateEvent:                               # realtime -> widget: mic/speaker indicator
    state: VoiceState
    type: Literal["voice_state"] = "voice_state"


@dataclass
class LatencyEvent:                                  # realtime -> widget: REAL Moss ms (or omitted)
    time_taken_ms: int
    type: Literal["latency"] = "latency"


Event = Union[AnswerEvent, ConfirmEvent, VoiceStateEvent, LatencyEvent]


def dumps(e: Event) -> bytes:
    """The wire format the publisher writes onto the 'clutch' data topic."""
    return json.dumps(asdict(e), separators=(",", ":")).encode("utf-8")
