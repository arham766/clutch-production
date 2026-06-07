"""get_stt(cfg) -> STTProvider. Picks a LiveKit STT plugin by role-named config; Cartesia default."""

from __future__ import annotations

from src.speech.providers.base import STTProvider

DEFAULT = "cartesia"   # primary + only shipped impl; other vendors are additive


def get_stt(cfg) -> STTProvider:
    name = (getattr(cfg, "stt_provider", None) or DEFAULT).lower()
    if name in ("cartesia", "default"):
        from src.speech.providers.cartesia import CartesiaSTT     # lazy: importing livekit only on use

        return CartesiaSTT(cfg)
    # extension point — one new elif per added vendor, no change above:
    #   if name == "deepgram":
    #       from speech.providers.deepgram import DeepgramSTT
    #       return DeepgramSTT(cfg)
    raise ValueError(f"unknown stt_provider {name!r} (have: cartesia)")
