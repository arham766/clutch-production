# Section C — Tonmoy · Cognition Layer (Vision & Orchestrator)

**Engineer:** Tonmoy — ML Engineer, Vision & Orchestrator (very strong at vision optimization)
**Owns HLDs:** [02 — Perception](../02-perception.md) · [04 — Conversation Agent](../04-agent.md)
**Owns directories:** `src/perception/` (built; extend) · `src/agent/` (new)
**Seams you own:** **S3** (Agent⇄Perception, internal) · **S2** (Realtime⇄Agent) · **S4** (Agent⇄Retrieval) — [10 §4](10-work-division-and-fusing.md)

> **Independence ([10 §5](10-work-division-and-fusing.md)).** `src/perception/` + `src/agent/` import
> only `src/contracts.py` — never a sibling subfolder. **You expose:** `identify(...)` and
> `ConversationAgent` (`on_user_turn` / `on_identify`). **You receive (passed in):** `retrieve` /
> `compose` (into the agent), `gateway_client`, the session's catalog. **Build against:**
> `mock_retrieve` + `template_compose`, `mock_gateway`. (Perception already ships `mock_vision` + 8
> tests.) **Connect later:** `src/app.py` passes Fardin's real `retrieve` / `compose` into the agent —
> no FSM change.

---

## 1. Your mission
You build **the eyes and the brain**. **Perception (02):** turn a burst of live camera frames into a
structured **product + problem** (`IdentifyResult`) via *one* optimized vision call against the
company's closed catalog. **Agent (04):** the single multi-modal orchestrator that serves Type / Talk
/ See over one `SupportSession` — it decides whether to retrieve, composes grounded answers, and owns
the **modality-escalation** move ("hit See and show me"). These two are paired because the Agent is
the *consumer* of your vision output — keeping them in one section makes S3 an internal call, not a
cross-engineer seam.

## 2. Scope
**In — Perception (02):**
- **`FrameGate`** (`frames.py`): a rolling-pHash change gate + rate cap across the **continuous** See
  feed — the VLM re-looks only when the view meaningfully changes (Fardin's loop drives it before
  `identify`, S8). Zero VLM calls while the customer holds still; this is your cost/latency win.
- Best-frame selection (`frames.py`: sharpness + pHash dedup) → sharpest 1–2 distinct frames.
- **Exactly one** vision call via the `VisionProvider` protocol; closed-set classification over the
  catalog + OCR label read + `ProblemSignals` extraction, all in one pass.
- Normalize → `IdentifyResult` (clamp confidence, keep ids in-catalog, rank candidates, set
  `needs_confirmation`). Mock-first (`MockVisionProvider`) + real `QwenGatewayProvider` (via S5).
- This is where your **vision optimization** edge lands: frame selection, prompt design, OCR-vs-visual
  weighting, 1-vs-2-frame cost/recall (02 §8).

**In — Agent (04):**
- `SupportSession` ownership: state, phase FSM (`greeting→assisting→confirming→escalating→resolved`),
  history (salient, deduped) + rolling summary, in-memory recall.
- Per-turn loop: fold input → decide-retrieve → build `RetrievalQuery` → grounded compose → maybe
  escalate modality → emit `Answer`.
- The **speak gate** (turn-driven for Type/Talk, ambient for See; debounce + no-repeat).
- Consuming `IdentifyResult` incl. `needs_confirmation` → confirm vs scope+answer.
- **Grounding & safety discipline:** compose only over retrieved chunks, every claim cited; never emit
  a hardware step without its doc warning — withhold + escalate (04 §3).

**Out:** retrieval/compose *mechanics* (Fardin 03 — you *call* them); STT/TTS/transport/frame
sampling (Fardin 05-server); the widget (Arman 05); model routing (Arham 06 — you call `route`).

## 3. What you build against (mock-first, day 1)
- Perception already has **8 passing tests** on `MockVisionProvider` — extend from there; the real
  provider only needs the gateway (S5) which Arham mocks first.
- The Agent is injected with `retrieve`, `compose`, `escalate` callables (04 §4 constructor) — develop
  the entire FSM against Fardin's **mock retrieve/compose** (local-cosine + template-fill). You need
  zero live infra to reach "FSM green."

## 4. The single paths you connect through
- **S3 (internal):** `identify(frames, catalog, *, provider) -> IdentifyResult`. The Agent calls this
  when the user hits See. Both sides are yours → tightest possible loop.
- **S2 (Realtime⇄Agent):** Fardin's voice loop calls `agent.on_user_turn(text)` and
  `agent.on_identify(result)`, each returning `Answer | None`. This is your **only** uplink to the
  transport — you return `Answer`s; you never touch LiveKit/STT/TTS.
- **S4 (Agent⇄Retrieval):** you call `retrieve_for(RetrievalQuery) -> Chunk[]` and
  `compose(query, chunks) -> Answer` (Fardin's). You build the `RetrievalQuery` (text from the user
  and/or `ProblemSignals.to_query()`, `product_id` scope); you do not query Moss yourself.
- **S5 (Gateway):** `vision.identify` and `reason.compose` go through `route` + `gateway_client`
  (Arham) — no provider SDK in your code.
- **S6 (Tenancy):** read `catalog` + `company_id` from the `SupportSession` Arham stamps.

## 5. Definition of done
- [ ] `identify()` returns a normalized `IdentifyResult` on real frames: in-catalog id or `null`,
      clamped confidence, ranked candidates, correct `needs_confirmation`/`resolved`.
- [ ] OCR-label path and visual-match-vs-`ref_image` path both exercised; out-of-catalog ids rejected.
- [ ] `QwenGatewayProvider` works through the TF gateway logical name (swap-safe; no Qwen SDK import).
- [ ] `ConversationAgent` FSM passes all phase transitions on mock retrieve/compose.
- [ ] Modality escalation fires correctly: physical/visual problem or empty retrieval + non-video →
      grounded partial answer **+** the See nudge.
- [ ] `on_identify`: `needs_confirmation` → ask which product (no scoping yet); else scope `product_id`
      + fold `problem` → grounded step.
- [ ] Grounding discipline holds: no uncited sentence; dangerous step without doc warning is withheld
      and escalates.
- [ ] Speak gate: ambient silence in See, speaks on next-step/safety/confirm; debounced, no repeats.
- [ ] Any per-turn exception → return `None` (session never crashes mid-support, 04 §6).

## 6. Failure modes you own (02 §6, 04 §6)
No catalog match → `needs_confirmation`, ask user to pick. Blurry frames → skip unreadable; none
survive → request re-capture (signal 05). Malformed model output → normalizer defaults to
`needs_confirmation`. Retrieval empty → "docs don't cover that," offer See/human, never invent.
Composer down → Arham's Tier-0 template-fill via gateway fallback.

## 7. Handoffs
- **To Fardin:** the `RetrievalQuery` shape you emit + when you call `on_*` returning `Answer` (S2/S4).
- **To Arman (via Fardin's events):** `Answer{text, citations}` and the confirm prompt content the
  widget renders — your text/options populate `answer`/`confirm` events.
- **From Arham:** `gateway_client` + `route` (S5), `catalog`/`company_id` on the session (S6).

## 8. Stretch / open questions
`FrameGate` `change_distance`/`min_interval_s` tuning (live-feed sensitivity vs cost);
`needs_confirmation` thresholds + candidate margin (tune live); 1 vs 2 frames; OCR-vs-visual weight on
disagreement; inline `ref_image` to the model vs text catalog (02 §8). Escalation-trigger aggression;
hand-authored FSM vs LLM-planned turns (default FSM, LLM for phrasing only) (04 §8).
