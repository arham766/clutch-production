"""Vision providers: pluggable backends for the identify call.

- `MockVisionProvider` — deterministic, for tests/dev (no keys).
- `QwenGatewayProvider` — Qwen vision via the TrueFoundry AI Gateway (OpenAI-compatible).
All implement the `VisionProvider` protocol in `base`.
"""
