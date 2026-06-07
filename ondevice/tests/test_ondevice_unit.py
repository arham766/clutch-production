"""Pure invariants unique to the on-device path (LLD 03-LOCAL §10) — no Moss, no keys, no network.

Everything behavioral (real retrieve, boot-from-disk, egress boundary, incremental upsert, …) lives
in `scripts/ondevice_mini.py --check`. These three pin the cheap, dependency-free contracts.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ondevice/ on path

import pytest  # noqa: E402

from ondevice import (  # noqa: E402
    Chunk, ConfigError, LocalIndexStore, OnDeviceConfig, OnDeviceMossRetriever,
    make_retrieval, resolve_reason_endpoint,
)


def test_registry_dispatch():
    """config-keyed selection: on_device builds the backend; an unknown mode fails LOUD."""
    rf, comp = make_retrieval(OnDeviceConfig(retrieval_mode="on_device"))
    assert callable(rf) and callable(comp)
    with pytest.raises(ConfigError):
        make_retrieval(OnDeviceConfig(retrieval_mode="nope"))
    with pytest.raises(ConfigError):
        make_retrieval(OnDeviceConfig(reason_endpoint="nope"))


def test_endpoint_resolution():
    """local-LLM-first rule: local→local, auto+healthy→local, auto+down→gateway, missing→template."""
    sentinel = object()
    assert resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="local"),
                                   gateway_client="GW", local_client=sentinel) is sentinel
    assert resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="auto"),
                                   gateway_client="GW", local_client=sentinel) is sentinel
    assert resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="auto"),
                                   gateway_client="GW", local_client=None) == "GW"
    assert resolve_reason_endpoint(OnDeviceConfig(reason_endpoint="local"),
                                   gateway_client="GW", local_client=None) is None   # → template


def test_no_push_contract():
    """A full upsert + boot + retrieve never calls push_index — the corpus stays on the box."""
    import asyncio
    store = LocalIndexStore(tempfile.mkdtemp(prefix="ondevice_"), embed_model="moss-minilm")
    store.upsert([Chunk(id="c1", text="open the rear tray and clear the jam",
                        metadata={"source": "m.pdf"}, source="m.pdf")])
    r = OnDeviceMossRetriever(store, OnDeviceConfig())
    asyncio.run(r.retrieve("demo", "clear the jam", None, top_k=5, min_score=0.2))
    assert r._session.push_calls == []
