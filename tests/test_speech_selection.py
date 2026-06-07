"""Tier-1 selection units for src/speech/ (LLD 05-speech §8).

Pure selection logic only: default → Cartesia, unknown → ValueError, missing key → RuntimeError at
stream(). `livekit` must NOT be imported by these — they stay green on a bare clone (no voice group).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from speech.stt import get_stt
from speech.tts import get_tts


def test_get_stt_default_is_cartesia():
    assert type(get_stt(SimpleNamespace())).__name__ == "CartesiaSTT"


def test_get_tts_selects_cartesia():
    assert type(get_tts(SimpleNamespace(tts_provider="cartesia"))).__name__ == "CartesiaTTS"
    # also the plain default-cfg path:
    assert type(get_tts(SimpleNamespace())).__name__ == "CartesiaTTS"


def test_unknown_stt_provider_raises():
    with pytest.raises(ValueError):
        get_stt(SimpleNamespace(stt_provider="bogus"))


def test_unknown_tts_provider_raises():
    with pytest.raises(ValueError):
        get_tts(SimpleNamespace(tts_provider="bogus"))


def test_cartesia_stt_missing_key_raises(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    prov = get_stt(SimpleNamespace())
    with pytest.raises(RuntimeError):
        prov.stream()


def test_cartesia_tts_missing_key_raises(monkeypatch):
    monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
    prov = get_tts(SimpleNamespace())
    with pytest.raises(RuntimeError):
        prov.stream()


def test_livekit_not_imported_by_selection():
    """Selection path must never pull in livekit (testable with livekit not installed)."""
    assert "livekit" not in sys.modules
