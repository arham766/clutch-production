# `src/agent/` — Conversation Agent (Section C · Tonmoy)

The multi-modal support brain (HLD [04](../../docs/clutch/hld/04-agent.md)). One deterministic FSM
serves Type / Talk / See over a single `SupportSession`: decides whether to retrieve, composes a
grounded answer, and owns modality escalation ("hit See and show me").

- **Exposes:** `ConversationAgent` with `on_user_turn(text) -> Answer?` and
  `on_identify(IdentifyResult) -> Answer?`
- **Receives (passed in):** `retrieve`, `compose` (constructor args — the agent never imports
  `retrieval/`)
- **Imports only:** `src/contracts.py` — no sibling subfolder
- **Build-against mock:** `mock_retrieve` / `mock_compose`

See the brief: [13 — Tonmoy](../../docs/clutch/hld/engineers/13-tonmoy-vision-orchestrator.md) and the
connection plan [10 §5](../../docs/clutch/hld/engineers/10-work-division-and-fusing.md).
