"""get_tts(cfg) -> TTSProvider. Picks a LiveKit TTS plugin by role-named config; Cartesia default."""

from __future__ import annotations

from speech.providers.base import TTSProvider

DEFAULT = "cartesia"   # primary + only shipped impl; other vendors are additive


def get_tts(cfg) -> TTSProvider:
    name = (getattr(cfg, "tts_provider", None) or DEFAULT).lower()
    if name in ("cartesia", "default"):
        from speech.providers.cartesia import CartesiaTTS     # lazy: importing livekit only on use

        return CartesiaTTS(cfg)
    # extension point — one new elif per added vendor, no change above:
    #   if name == "elevenlabs":
    #       from speech.providers.elevenlabs import ElevenLabsTTS
    #       return ElevenLabsTTS(cfg)
    raise ValueError(f"unknown tts_provider {name!r} (have: cartesia)")
