"""Speech Limb — the STT/TTS swap point (HLD 05 §3, 00 §7).

Given config, hands back an STT plugin and a TTS plugin (primary: Cartesia).
Imports only `contracts`/`config`; `livekit` is referenced as string annotations
only and never imported at module top, so provider *selection* is testable with
livekit NOT installed.
"""

from src.speech.stt import get_stt
from src.speech.tts import get_tts
from src.speech.providers.base import STTProvider, TTSProvider

__all__ = ["get_stt", "get_tts", "STTProvider", "TTSProvider"]
