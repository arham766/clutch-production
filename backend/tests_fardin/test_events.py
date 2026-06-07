"""
Tests for src/events.py — data-channel event schema.
"""

from __future__ import annotations

import json
from src.events import (
    AnswerEvent,
    ConfirmEvent,
    VoiceStateEvent,
    LatencyEvent,
    dumps,
)

def test_answer_event():
    ev = AnswerEvent(text="Hello", citations=[{"doc": "manual.pdf", "section": "§1", "score": 0.9}])
    assert ev.type == "answer"
    assert ev.text == "Hello"
    assert len(ev.citations) == 1
    
    b = dumps(ev)
    d = json.loads(b)
    assert d["type"] == "answer"
    assert d["text"] == "Hello"
    assert d["citations"][0]["doc"] == "manual.pdf"

def test_confirm_event():
    ev = ConfirmEvent(prompt="Is this it?", options=["Yes", "No"])
    assert ev.type == "confirm"
    assert ev.prompt == "Is this it?"
    
    d = json.loads(dumps(ev))
    assert d["type"] == "confirm"
    assert d["options"] == ["Yes", "No"]

def test_voice_state_event():
    ev = VoiceStateEvent(state="listening")
    assert ev.type == "voice_state"
    assert ev.state == "listening"
    
    d = json.loads(dumps(ev))
    assert d["state"] == "listening"

def test_latency_event():
    ev = LatencyEvent(time_taken_ms=120)
    assert ev.type == "latency"
    assert ev.time_taken_ms == 120
    
    d = json.loads(dumps(ev))
    assert d["time_taken_ms"] == 120
