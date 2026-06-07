"""Realtime & Voice (server side) — the LiveKit Agents voice loop (LLD 05-realtime).

Public surface (the only things other layers / the harness touch):
    run_session      — the per-room job entrypoint (STT -> Agent -> TTS, barge-in, See, events)
    build_worker     — binds the cross-layer deps via functools.partial and returns WorkerOptions
    FrameSampler     — video track -> raw JPEG list[bytes] at ~1-2 fps (S8)
    EventPublisher   — single writer of the four typed events on the "clutch" data topic (S1)

This layer imports ONLY the Spine (`contracts`, `events`) + `config`. Every cross-layer
dependency (agent, identify, stt, tts, vad, scope_session) arrives via run_session(...) args —
no sibling subfolder (agent/perception/retrieval/speech) is ever imported here.
"""

from __future__ import annotations

from .frames import FrameSampler
from .publisher import EventPublisher
from .session import run_session
from .worker import build_worker, run_worker

__all__ = ["run_session", "build_worker", "run_worker", "FrameSampler", "EventPublisher"]
