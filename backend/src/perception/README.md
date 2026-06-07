# `src/perception/` — Perception / Vision (Section C · Tonmoy)

Closed-set product ID + problem signals from live camera frames (HLD
[02](../../docs/clutch/hld/02-perception.md)). One vision call against the company catalog → a
structured `IdentifyResult`. Vision describes-and-routes only; never repair advice.

- **Exposes:** `identify(frames, catalog, *, provider) -> IdentifyResult`
- **Receives (passed in):** `gateway_client` (vision, via the provider)
- **Imports only:** `src/contracts.py` — no sibling subfolder
- **Build-against mock:** `mock_vision` (built) / `canned_identify`
- **Status — built:** `frames.py` (best-frame: sharpness + pHash), `providers/` (`base.py`, `mock.py`,
  `qwen_gateway.py`), `identify.py` (normalizer + confidence gating); 8 tests pass

See the brief: [13 — Tonmoy](../../docs/clutch/hld/engineers/13-tonmoy-vision-orchestrator.md) and the
connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
