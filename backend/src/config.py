"""
Clutch configuration + secrets.

Loads from .env. Defines frozen singletons used throughout the system.
Follows the model-portability guarantee (HLD 00 §7): model keys are named
by role (reason_model, vision_model), never by vendor.

Fails fast on missing core credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when a core configuration value is missing or invalid."""
    pass


@dataclass(frozen=True, slots=True)
class Config:
    # -----------------------------------------------------------------------
    # Core (Mandatory)
    # -----------------------------------------------------------------------
    # Moss (retrieval)
    moss_project_id: str
    moss_api_key: str
    moss_base_url: str

    # TrueFoundry (model gateway)
    tf_api_key: str
    tf_base_url: str

    # LiveKit (realtime transport)
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str

    # Firebase (auth, DB)
    firebase_project_id: str
    firebase_credentials: str  # Path to service account JSON, or literal JSON string

    # Cloudflare R2 (storage)
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str

    # -----------------------------------------------------------------------
    # Roles (Logical names, not vendors)
    # -----------------------------------------------------------------------
    reason_model: str = "minimax-reasoning"
    vision_model: str = "qwen-vl-plus"
    embed_model: str = "text-embedding-3-small"
    stt_provider: str = "cartesia"
    tts_provider: str = "cartesia"
    parse_provider: str = "unsiloed"

    # -----------------------------------------------------------------------
    # Features (Optional, degrade gracefully)
    # -----------------------------------------------------------------------
    unsiloed_api_key: str | None = None

    # Which subsystems are running without credentials?
    degraded: tuple[str, ...] = field(default_factory=tuple)


def load_config(env_path: str | None = None) -> Config:
    """Load config from the environment or .env file. Fails fast on core vars."""
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()

    missing_core = []

    def get_core(key: str) -> str:
        val = os.getenv(key)
        if not val:
            missing_core.append(key)
            return ""
        return val

    cfg_kwargs = {
        "moss_project_id": get_core("MOSS_PROJECT_ID"),
        "moss_api_key": get_core("MOSS_API_KEY"),
        "moss_base_url": get_core("MOSS_BASE_URL"),
        "tf_api_key": get_core("TRUEFOUNDRY_API_KEY"),
        "tf_base_url": get_core("TRUEFOUNDRY_BASE_URL"),
        "livekit_url": get_core("LIVEKIT_URL"),
        "livekit_api_key": get_core("LIVEKIT_API_KEY"),
        "livekit_api_secret": get_core("LIVEKIT_API_SECRET"),
        "firebase_project_id": get_core("FIREBASE_PROJECT_ID"),
        "firebase_credentials": get_core("FIREBASE_CREDENTIALS"),
        "r2_account_id": get_core("R2_ACCOUNT_ID"),
        "r2_access_key_id": get_core("R2_ACCESS_KEY_ID"),
        "r2_secret_access_key": get_core("R2_SECRET_ACCESS_KEY"),
        "r2_bucket_name": get_core("R2_BUCKET_NAME"),
    }

    if missing_core:
        raise ConfigError(f"Missing mandatory core environment variables: {', '.join(missing_core)}")

    # Load roles (with defaults)
    roles = {
        "reason_model": os.getenv("REASON_MODEL", "minimax-reasoning"),
        "vision_model": os.getenv("VISION_MODEL", "qwen-vl-plus"),
        "embed_model": os.getenv("EMBED_MODEL", "text-embedding-3-small"),
        "stt_provider": os.getenv("STT_PROVIDER", "cartesia"),
        "tts_provider": os.getenv("TTS_PROVIDER", "cartesia"),
        "parse_provider": os.getenv("PARSE_PROVIDER", "unsiloed"),
    }
    cfg_kwargs.update(roles)

    # Load optional features
    unsiloed_key = os.getenv("UNSILOED_API_KEY")
    cfg_kwargs["unsiloed_api_key"] = unsiloed_key

    degraded = []
    if not unsiloed_key:
        degraded.append("unsiloed")
    cfg_kwargs["degraded"] = tuple(degraded)

    return Config(**cfg_kwargs)  # type: ignore[arg-type]
