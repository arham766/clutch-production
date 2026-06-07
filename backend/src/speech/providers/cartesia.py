"""Cartesia STT/TTS as LiveKit plugins — the voice primary (HLD 05 §7).

Verified against LiveKit Agents v1 (June 2026). API key is checked BEFORE the lazy livekit import so
a missing key raises a clean RuntimeError even where the plugin isn't installed (keeps the keyless
selection/missing-key tests in §8 livekit-free).
"""

from __future__ import annotations

import os
from typing import Optional

_MISSING = "CARTESIA_API_KEY unset — required to build the Cartesia {kind} plugin"


class CartesiaSTT:
    def __init__(self, cfg=None,
                 api_key: Optional[str] = None, model: Optional[str] = None,
                 language: Optional[str] = None):
        self.api_key  = api_key  or os.environ.get("CARTESIA_API_KEY")
        # Default model UNSET on purpose: let the plugin resolve it from language — English → ink-2
        # (streaming + interim_results, needed for responsive live turn-taking), other → ink-whisper.
        # Forcing ink-whisper put English on the LEGACY recognize path (interim_results=False, and the
        # "Session is closed" ws bug) and broke live STT. Override only via CARTESIA_STT_MODEL.
        self.model    = model    or os.environ.get("CARTESIA_STT_MODEL")  # None → plugin default
        self.language = language or os.environ.get("CARTESIA_LANGUAGE", "en")

    def stream(self) -> "livekit.STT":
        if not self.api_key:
            raise RuntimeError(_MISSING.format(kind="STT"))
        from livekit.plugins import cartesia            # lazy: only this path needs the plugin

        kw = {"api_key": self.api_key, "language": self.language}
        if self.model:                       # omit → plugin picks ink-2 (en) / ink-whisper (other)
            kw["model"] = self.model
        return cartesia.STT(**kw)


class CartesiaTTS:
    def __init__(self, cfg=None,
                 api_key: Optional[str] = None, model: Optional[str] = None,
                 voice: Optional[str] = None, language: Optional[str] = None):
        self.api_key  = api_key  or os.environ.get("CARTESIA_API_KEY")
        self.model    = model    or os.environ.get("CARTESIA_TTS_MODEL", "sonic-3")
        self.voice    = voice    or os.environ.get("CARTESIA_VOICE_ID", "")   # "" → plugin default
        self.language = language or os.environ.get("CARTESIA_LANGUAGE", "en")

    def stream(self) -> "livekit.TTS":
        if not self.api_key:
            raise RuntimeError(_MISSING.format(kind="TTS"))
        from livekit.plugins import cartesia

        kw = {"api_key": self.api_key, "model": self.model, "language": self.language}
        if self.voice:                       # omit → use the plugin's default voice
            kw["voice"] = self.voice
        return cartesia.TTS(**kw)             # NB: no sample_rate — see note above
