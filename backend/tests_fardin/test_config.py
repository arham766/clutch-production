"""
Tests for src/config.py — configuration loading + validation.

Covers:
- Successful load with all core vars set
- Missing a single core var → ConfigError naming it
- Missing multiple core vars → all named
- Feature cred missing → degraded list populated, no crash
- Role defaults applied when env vars absent
- Role overrides respected
"""

from __future__ import annotations

import os
import textwrap
import tempfile

import pytest

from src.config import Config, ConfigError, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all Clutch-related env vars before each test."""
    for key in [
        "MOSS_API_KEY", "MOSS_BASE_URL",
        "TRUEFOUNDRY_API_KEY", "TRUEFOUNDRY_BASE_URL",
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "FIREBASE_PROJECT_ID", "FIREBASE_CREDENTIALS",
        "REASON_MODEL", "VISION_MODEL", "EMBED_MODEL",
        "STT_PROVIDER", "TTS_PROVIDER", "PARSE_PROVIDER",
        "UNSILOED_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)


def _set_all_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every core env var to a placeholder value."""
    monkeypatch.setenv("MOSS_API_KEY", "moss-key-123")
    monkeypatch.setenv("MOSS_BASE_URL", "https://moss.example.com")
    monkeypatch.setenv("TRUEFOUNDRY_API_KEY", "tf-key-456")
    monkeypatch.setenv("TRUEFOUNDRY_BASE_URL", "https://tf.example.com")
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.example.com")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "clutch-test")
    monkeypatch.setenv("FIREBASE_CREDENTIALS", "/path/to/sa.json")


class TestLoadConfigSuccess:
    def test_all_core_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_all_core(monkeypatch)
        cfg = load_config()

        assert cfg.moss_api_key == "moss-key-123"
        assert cfg.moss_base_url == "https://moss.example.com"
        assert cfg.tf_api_key == "tf-key-456"
        assert cfg.tf_base_url == "https://tf.example.com"
        assert cfg.livekit_url == "wss://livekit.example.com"
        assert cfg.livekit_api_key == "lk-key"
        assert cfg.livekit_api_secret == "lk-secret"
        assert cfg.firebase_project_id == "clutch-test"
        assert cfg.firebase_credentials == "/path/to/sa.json"

    def test_role_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_all_core(monkeypatch)
        cfg = load_config()

        assert cfg.reason_model == "openrouter/minimax-minimax-m2.5"
        assert cfg.vision_model == "openrouter/qwen-qwen2.5-vl-72b-instruct"
        assert cfg.embed_model == "text-embedding-3-small"
        assert cfg.stt_provider == "cartesia"
        assert cfg.tts_provider == "cartesia"
        assert cfg.parse_provider == "unsiloed"

    def test_role_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_all_core(monkeypatch)
        monkeypatch.setenv("REASON_MODEL", "gpt-4o")
        monkeypatch.setenv("STT_PROVIDER", "deepgram")
        cfg = load_config()

        assert cfg.reason_model == "gpt-4o"
        assert cfg.stt_provider == "deepgram"

    def test_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_all_core(monkeypatch)
        cfg = load_config()
        with pytest.raises(AttributeError):
            cfg.moss_api_key = "changed"  # type: ignore[misc]


class TestLoadConfigFeatureDegradation:
    def test_no_unsiloed_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        _set_all_core(monkeypatch)
        monkeypatch.delenv("UNSILOED_API_KEY", raising=False)
        dummy = tmp_path / ".env.empty"
        dummy.write_text("")
        cfg = load_config(str(dummy))

        assert cfg.unsiloed_api_key is None
        assert "unsiloed" in cfg.degraded

    def test_with_unsiloed_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        _set_all_core(monkeypatch)
        monkeypatch.setenv("UNSILOED_API_KEY", "unsilo-key-789")
        dummy = tmp_path / ".env.empty"
        dummy.write_text("")
        cfg = load_config(str(dummy))

        assert cfg.unsiloed_api_key == "unsilo-key-789"
        assert "unsiloed" not in cfg.degraded


class TestLoadConfigFailFast:
    def test_missing_single_core_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        _set_all_core(monkeypatch)
        monkeypatch.delenv("MOSS_API_KEY")

        # Pass a dummy empty env file so dotenv doesn't reload the real .env
        dummy = tmp_path / ".env.empty"
        dummy.write_text("")
        with pytest.raises(ConfigError, match="MOSS_API_KEY"):
            load_config(str(dummy))

    def test_missing_multiple_core_vars(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        # _clean_env fixture already stripped all vars; pass empty file
        dummy = tmp_path / ".env.empty"
        dummy.write_text("")
        with pytest.raises(ConfigError) as exc_info:
            load_config(str(dummy))

        msg = str(exc_info.value)
        assert "MOSS_API_KEY" in msg
        assert "TRUEFOUNDRY_API_KEY" in msg
        assert "LIVEKIT_URL" in msg
        assert "FIREBASE_PROJECT_ID" in msg

    def test_missing_firebase_credentials(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        _set_all_core(monkeypatch)
        monkeypatch.delenv("FIREBASE_CREDENTIALS")

        dummy = tmp_path / ".env.empty"
        dummy.write_text("")
        with pytest.raises(ConfigError, match="FIREBASE_CREDENTIALS"):
            load_config(str(dummy))


class TestLoadConfigFromFile:
    def test_load_from_env_file(self, tmp_path: Any) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(textwrap.dedent("""\
            MOSS_API_KEY=file-moss-key
            MOSS_BASE_URL=https://moss.file.com
            TRUEFOUNDRY_API_KEY=file-tf-key
            TRUEFOUNDRY_BASE_URL=https://tf.file.com
            LIVEKIT_URL=wss://lk.file.com
            LIVEKIT_API_KEY=file-lk-key
            LIVEKIT_API_SECRET=file-lk-secret
            FIREBASE_PROJECT_ID=file-project
            FIREBASE_CREDENTIALS=/file/sa.json
        """))

        cfg = load_config(str(env_file))
        assert cfg.moss_api_key == "file-moss-key"
        assert cfg.firebase_project_id == "file-project"
