"""Role-named configuration (Spine-adjacent).

Config names ROLES (stt_provider, reason_model, retrieval_alpha), never vendors
(00 §7). Vendor identity + secrets live in environment variables, read lazily inside
each provider (see .env.example). `from_env()` hydrates only the few values that are
both role-named and operationally needed (Moss creds, LiveKit creds).

Each module reads only the attributes it needs (duck-typed); fields below are the
union the three Section-D Limbs consume. If a module needs a new knob, add it here in
one place (the shared coordination point) rather than inventing a parallel config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    # ── retrieval (LLD 03 §9.2) ──────────────────────────────────────────────
    retrieval_alpha: float = 0.70          # hybrid semantic<->keyword blend
    retrieval_top_k: int = 5
    retrieval_min_score: float = 0.35      # refusal floor
    reason_model: str = "reason.compose"   # LOGICAL gateway name, not a vendor id
    moss_project_id: str = ""              # from env MOSS_PROJECT_ID (live path only)
    moss_project_key: str = ""             # from env MOSS_PROJECT_KEY

    # ── speech (LLD 05-speech §7.1) ──────────────────────────────────────────
    stt_provider: str = "cartesia"
    tts_provider: str = "cartesia"

    # ── realtime (LLD 05-realtime §7) ────────────────────────────────────────
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    see_fps: float = 1.5
    see_batch: int = 2
    see_min_interval_ms: int = 1500
    endpoint_min_ms: int = 300
    endpoint_max_ms: int = 2000
    min_interruption_ms: int = 200
    barge_in: bool = True
    latency_badge: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        """Hydrate creds from the environment; role knobs keep their defaults."""
        return cls(
            moss_project_id=os.environ.get("MOSS_PROJECT_ID", ""),
            moss_project_key=os.environ.get("MOSS_PROJECT_KEY", ""),
            stt_provider=os.environ.get("STT_PROVIDER", "cartesia"),
            tts_provider=os.environ.get("TTS_PROVIDER", "cartesia"),
            livekit_url=os.environ.get("LIVEKIT_URL", ""),
            livekit_api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            livekit_api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
        )

    def real(self, section: str) -> bool:
        """app.py uses this to pick real vs mock wiring. True when the section's creds exist."""
        if section == "retrieval":
            return bool(self.moss_project_id and self.moss_project_key)
        if section == "realtime":
            return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)
        if section == "speech":
            return bool(os.environ.get("CARTESIA_API_KEY"))
        return False
