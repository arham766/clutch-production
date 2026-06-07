"""STTProvider / TTSProvider protocols — the voice swap point (HLD 05 §3, 00 §7)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:                       # never imported at runtime → selection needs no livekit
    from livekit.agents import stt as lk_stt, tts as lk_tts


class STTProvider(Protocol):
    def stream(self) -> "lk_stt.STT": ...   # fresh LiveKit STT plugin per call


class TTSProvider(Protocol):
    def stream(self) -> "lk_tts.TTS": ...   # fresh LiveKit TTS plugin per call
