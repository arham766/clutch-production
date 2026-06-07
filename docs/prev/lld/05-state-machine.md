# Fixalong LLD 05 — Repair state machine + speak gate
**Implements:** HLD 05 · **Module(s):** agent/state/machine.py, agent/state/referent.py · **Build tier:** 0 · **Status:** Draft v0.1

## 1. Responsibility
The single owner of `RepairState`. It ingests the two live streams — vision (primary,
continuous, via `on_visual_observation`) and voice (control, intermittent, via
`on_final_transcript`) — folds each into `RepairState`, walks the deterministic repair graph
(`advance_node`), and decides *whether to speak at all* (`should_speak`), *what to say*
(`emit`), and *in what tone* (persona mode). It resolves pronoun referents ("it"/"this")
against the live visual state. It does **not** retrieve, verify, compose-with-LLM, guardrail,
or speak directly — it calls out to LLD 02/06/07/11 and returns `Optional[(text, mode)]` to
the live loop (12). Ambient = silent by default; speech is the exception.

## 2. Files & public surface
| File | Exports |
|---|---|
| `agent/state/machine.py` | `class StateMachine` with `on_visual_observation`, `on_final_transcript`, `should_speak`, `advance_node`, `emit`, `note_interrupt`, `is_done`; helpers `_set_phase`, `_detect_hazard`, `_classify_intent` |
| `agent/state/referent.py` | `resolve_referent(text, state) -> ReferentResult`; consts `PRONOUNS`, `ResolveStatus` |

`StateMachine.__init__(self, session: SessionState, retrieve, verify, compose, guardrail,
remember, clock=time.monotonic)` — collaborators are injected (testability); `retrieve`,
`verify`, `compose`, `guardrail`, `remember` are the LLD 02/11/—/06/11 callables.

## 3. Dependencies
- **Internal:** `agent/contracts.py` (`RepairState`, `RepairNode`, `VisualObservation`,
  `Verdict`, `PersonaMode`, `Phase`); `agent/knowledge/retrieve.py` (LLD 02 `retrieve`,
  `prefetch`, `build_query`, `remember`); `agent/knowledge/verify.py` (LLD 11 `verify`);
  `agent/safety/guardrail.py` (LLD 06 `guardrail`); Claude composer (Tier 1, via 06 gateway).
- **Libs:** stdlib only (`time`, `dataclasses`, `difflib` for fuzzy referent match). No LLM
  on the gate/traversal path — deterministic for the demo.
- **Env vars:** none owned here; gateway/keys live in 02/06/11.
- **Graph source:** the repair graph (`list[RepairNode]`) is loaded by the orchestrator (13)
  from ingestion (01) and handed in via `session`; this module only reads it.

## 4. Data structures (beyond shared contracts)
```python
# agent/state/referent.py
ResolveStatus = Literal["resolved", "ambiguous", "none"]
@dataclass
class ReferentResult: status: ResolveStatus; part: dict | None; candidates: list[dict]

# agent/state/machine.py
Trigger = Literal["vision", "voice"]
Intent  = Literal["question", "observation", "danger", "ack"]
@dataclass
class SpeakDecision: speak: bool; reason: str          # reason ∈ {danger,clear_step,user_asked,confirm,silent_*}
# RepairState carries scratch fields used only by this module (string-valued in Moss; richer here):
#   state.last_spoken: str            # last utterance text (anti-repeat)
#   state._last_speak_t: float        # monotonic ts of last speech (debounce)
#   state._spoken_node_ids: set[str]  # nodes whose step we've already delivered
#   state._pending_confirm: dict|None # the part/step awaiting user confirmation
```

## 5. Key functions

### 5.1 `should_speak(trigger, intent=None) -> bool`
The speak gate (HLD 05 §5). Danger always wins and ignores anti-chatter; everything else is
subject to no-repeat + debounce.
```
def should_speak(self, trigger, intent=None) -> bool:
    now = self.clock()
    # 1. DANGER — pre-empts everything, bypasses debounce & no-repeat
    if self.state.phase == "warning":
        self._decision = SpeakDecision(True, "danger");  return True
    # 2. anti-chatter window (skipped only for danger, handled above)
    if now - self.state._last_speak_t < DEBOUNCE_S:
        self._decision = SpeakDecision(False, "silent_debounce");  return False
    speak, reason = False, "silent_default"
    # 3. user asked / expressed intent (voice only)
    if trigger == "voice" and intent in ("question", "danger"):
        speak, reason = True, "user_asked"
    # 4. confirmation needed — ambiguous referent or low-confidence vision
    elif self.state.phase == "confirming" and self.state._pending_confirm is not None:
        speak, reason = True, "confirm"
    # 5. clear next step — advanced to an unspoken node with a single confident action
    elif self._has_clear_next_step():
        speak, reason = True, "clear_step"
    # 6. ack with nothing new, or a visual change that doesn't change the advice → silence
    self._decision = SpeakDecision(speak, reason)
    if not speak: return False
    # 7. no-repeat: never re-utter the last thing verbatim (danger already returned above)
    draft_key = self._candidate_node_key()
    if draft_key in self.state._spoken_node_ids and reason == "clear_step":
        self._decision = SpeakDecision(False, "silent_norepeat");  return False
    return True

def _has_clear_next_step(self) -> bool:
    node = self.current_node()
    return (node is not None
            and node.id not in self.state._spoken_node_ids
            and len(node.instructions) > 0
            and self._preconditions_met(node))   # single, actionable, not yet given
```

### 5.2 `resolve_referent(text, state) -> ReferentResult`  (`agent/state/referent.py`)
Pronoun → highest-confidence visible part; ties / low margin → ambiguous (→ confirming).
```
PRONOUNS = {"it","this","that","that one","these","those","the thing"}

def resolve_referent(text, state) -> ReferentResult:
    t = text.lower()
    if not any(p in t for p in PRONOUNS):
        # explicit noun? try to bind it directly to a visible part by name
        named = _match_named_part(t, state.visible_parts)
        return ReferentResult("resolved", named, [named]) if named else \
               ReferentResult("none", None, [])
    parts = sorted(state.visible_parts, key=lambda p: p.get("confidence", 0.0), reverse=True)
    if not parts:
        return ReferentResult("none", None, [])           # nothing in view → ask
    top = parts[0]
    if top.get("confidence", 0.0) < REF_MIN_CONF:
        return ReferentResult("ambiguous", None, parts[:3])
    if len(parts) > 1 and (top["confidence"] - parts[1]["confidence"]) < REF_MARGIN:
        return ReferentResult("ambiguous", None, parts[:3])  # too close to call
    return ReferentResult("resolved", top, parts[:3])
```
`_match_named_part` uses `difflib.get_close_matches` (cutoff 0.8) over part labels — cheap,
deterministic, no LLM.

### 5.3 `advance_node() -> None`
Deterministic graph traversal over `RepairNode.conditions → next`, with precondition skipping.
```
def advance_node(self):
    node = self.current_node()
    if node is None: return
    facts = self._observed_facts()          # union of vision facts + voice observations
    for cond, nxt in zip(node.conditions, node.next):
        if _cond_satisfied(cond, facts):
            target = self._skip_satisfied_preconds(nxt, facts)  # hop over done branches
            if target != self.state.current_node_id:
                self.state.current_node_id = target
                self._maybe_phase_from_node(target)             # high-risk next → warning
            return
    # no edge matched → stay; flag unknown observation for a clarifying question
    self.state._pending_confirm = self.state._pending_confirm or {"reason": "unknown_obs"}

def _skip_satisfied_preconds(self, node_id, facts):
    n = self.node(node_id)
    while n and all(_cond_satisfied(pc, facts) for pc in n.preconditions) and n.preconditions:
        # this node's preconditions are already true (e.g. rear panel already open on camera)
        if not n.next: break
        node_id = n.next[0]; n = self.node(node_id)
    return node_id
```
`_cond_satisfied` matches a condition string against `facts` (substring/keyword + part-name
match). Conditions and preconditions share the same matcher.

### 5.4 `on_visual_observation(obs) -> Optional[(text, mode)]` (PRIMARY — usually silent)
```
def on_visual_observation(self, obs):
    self.state.update_from_vision(obs)            # visible_parts, panel_state, hazards
    if self._detect_hazard(source=obs.hazards):   # vision hazard → warning, pre-empts
        self._set_phase("warning")
    self.state.add_observation(obs.visible_object)   # SALIENT in-memory recall (§5.6.5) — PRIMARY
    self.prefetch(self.state, obs=obs)               # warm context for what's in view (02)
    self.advance_node()
    if self.cfg.session_memory:                      # Tier-2 OPTIONAL: ephemeral session index (11)
        self.remember(self.state.session_id, {"type":"vision","obs":obs.visible_object})
    if self.should_speak(trigger="vision"):
        return self.emit()
    return None                                    # stay silent, keep watching
```

### 5.5 `on_final_transcript(text) -> Optional[(text, mode)]` (CONTROL)
```
def on_final_transcript(self, text):
    intent = self._classify_intent(text)                       # question|observation|danger|ack
    ref    = resolve_referent(text, self.state)                # referent.py
    if ref.status == "ambiguous":
        self.state._pending_confirm = {"candidates": ref.candidates}
        self._set_phase("confirming")
    self.state.add_observation(text, ref.part)        # SALIENT in-memory recall (§5.6.5) — PRIMARY
    if intent == "danger" or self._detect_hazard(source=text):
        self._set_phase("warning")
    self.advance_node()
    if self.cfg.session_memory:                       # Tier-2 OPTIONAL: ephemeral session index (11)
        self.remember(self.state.session_id, {"type":"voice","text":text,"ref":ref.part})
    if self.should_speak(trigger="voice", intent=intent):
        return self.emit()
    return None
```

### 5.6 `emit() -> Optional[(text, PersonaMode)]`
Build query → verify → compose → guardrail. Fail-safe at every hop.
```
def emit(self):
    # retrieve takes the RepairState (it builds the query from state internally) + optional obs.
    chunks = self.retrieve(self.state, obs=self._obs)         # → LLD 02 (bound; often prefetch hit)
    chunks = [c for c in (self.verify(c) for c in chunks)     # → LLD 11 verify_chunk: stamps c.verdict (str)
              if c.verdict != "REFUSE"]
    if not chunks and self.state.phase != "warning":
        return self._no_coverage_utterance()                  # "manual doesn't cover that…"
    draft = self.compose(self.state, chunks)                  # Tier0 template | Tier1 Claude
    if draft is None:                                         # composer down → Tier0 fallback
        draft = self._template_compose(self.state, chunks)
    safe = self.guardrail(draft, self.state, chunks)          # → LLD 06 → SafetyDecision
    if not safe.allowed:                                      # SafetyDecision.allowed (not .verdict)
        return self._refuse_utterance(safe)                   # escalate / silence; never speak the blocked draft
    text = safe.safe_rewrite or draft                         # guardrail may rewrite (e.g. prepend "unplug first")
    self._mark_spoken(text)                                   # last_spoken, _last_speak_t, node id
    return text, self._persona_mode()
```
**Composition tiers (HLD 05 §8):** Tier 0 = `_template_compose` — slot-fill a canned phrase
per phase from the top verified chunk (deterministic, fast, the demo default). Tier 1 =
Claude via the 06 gateway, prompted to use *only* the **recall context + chunks** (§5.6.5), no
outside knowledge. `compose` chooses tier by config flag; Tier 0 is always the fallback.

### 5.6.5 Recall context (what `compose` is given — the memory model)
`compose(state, chunks)` builds its prompt from in-memory state only — **no Moss conversation
index**:
```python
def recall_context(self) -> dict:
    return {
        "node":     self.state.current_node_id,                 # graph cursor
        "phase":    self.state.phase,
        "visible":  self.state.visible_parts,                   # current frame
        "hazards":  self.state.hazards,
        "recent":   self.recent_observations(self.cfg.RECALL_N),# last-N salient (default 8)
        "summary":  self.state.rolling_summary,                 # compact "session so far"
        "done":     self.state.actions_taken,
    }
# add_observation keeps the log SALIENT + bounded; older detail folds into rolling_summary.
def add_observation(self, text, referent=None):
    norm = _salient(text, referent)                            # state-change? else drop
    if norm is None or norm == self._last_obs_key: return       # dedup
    self.state.user_observations.append({"text": text, "ref": referent, "ts": self._t()})
    self._last_obs_key = norm
    if len(self.state.user_observations) > self.cfg.OBS_CAP:    # bounded
        self.update_rolling_summary()                          # fold oldest into summary, trim
def recent_observations(self, n=8): return self.state.user_observations[-n:]
def update_rolling_summary(self):
    # template (Tier 0) or one cheap LLM call (Tier 1): merge trimmed observations + actions
    # into state.rolling_summary; then drop the folded entries from user_observations.
    ...
```
**Why this is enough (HLD 05 §8.5):** a repair is minutes; salient items number in the dozens →
a few K tokens → fits the model context. Categorical recall ("what step / what done") is exact.
**Never** store raw per-frame vision output (the gate drops ~80–95%; only state changes land
here). Conversation is not indexed in Moss; the optional Tier-2 `remember()` writes a *separate
ephemeral* `session-<id>` index, never the shared manual index.

### 5.7 Persona mode + hazard detection
```
def _persona_mode(self) -> PersonaMode:
    return {"warning":"safety","diagnosing":"diagnosis","confirming":"confirm",
            "guiding":"normal","done":"expert"}[self.state.phase]

def _detect_hazard(self, source) -> bool:
    if isinstance(source, list):                              # obs.hazards from vision
        return len(source) > 0
    return any(k in source.lower() for k in HAZARD_KEYWORDS)  # voice: "smoke","spark","shock",…
```

## 6. Control flow / sequence
1. **Vision frame** (every gated `VisualObservation`, 04) → `on_visual_observation` →
   fold state → maybe `warning` → prefetch (02) → `advance_node` → remember (11) → gate →
   `emit` or `None`. The common case is `None` (silent).
2. **User speaks** (final transcript, 03) → `on_final_transcript` → classify + resolve
   referent → fold → maybe `warning`/`confirming` → `advance_node` → remember → gate → emit.
3. **Barge-in** (03) → `note_interrupt()` sets a flag the gate reads to bias toward `warning`
   re-evaluation on the next turn (user interrupting often means "stop").
4. `emit` is the only path that touches 02/06/07/11; it returns `(text, mode)` to the loop (12).

## 7. Config & tuning (defaults)
| Param | Default | Meaning |
|---|---|---|
| `DEBOUNCE_S` | `4.0` | min seconds between utterances (bypassed by danger) |
| `REF_MIN_CONF` | `0.55` | min vision confidence to bind a pronoun without confirming |
| `REF_MARGIN` | `0.15` | min top-vs-second confidence gap to disambiguate |
| `HAZARD_KEYWORDS` | smoke, spark, shock, burn, gas, leak, hot, cut | voice hazard trigger set |
| `COMPOSE_TIER` | `0` | 0 = templated (demo default), 1 = Claude phrasing |
| `_close_match_cutoff` | `0.8` | `difflib` cutoff for named-part binding |

Bias (HLD 05 §11): **toward speaking on danger, silence otherwise.** Tune `DEBOUNCE_S` up if
chatty, down if it feels unresponsive in `guiding`.

## 8. Error handling & fallbacks (fail-safe)
| Failure | Fallback |
|---|---|
| Composer (Claude) unavailable / `None` | `_template_compose` Tier-0 from verified chunks |
| No verified chunk (all REFUSE / empty) | `_no_coverage_utterance` — flag it, never invent |
| Guardrail returns REFUSE | `_refuse_utterance` — escalate or stay silent |
| Referent unresolved / ambiguous | set `_pending_confirm`, phase `confirming`, ask one question |
| Vision/voice conflict on a part | vision primary for presence; voice tie-breaks; else confirm |
| `advance_node` no edge matches | stay in node, raise `_pending_confirm{unknown_obs}` |
| Hazard uncertainty | bias to `warning` (false positive < missed hazard) |
| Any exception in `emit` | swallow → return `None` (silence) so the loop never crashes mid-repair |

## 9. Latency / perf notes
- Gate + traversal + referent are pure-Python, O(nodes·conditions) and O(visible_parts) —
  sub-millisecond; safe to run on every vision frame.
- `prefetch` is fire-and-forget (02) so the hot path doesn't block on Moss.
- `emit` cost is dominated by retrieve+verify+(optional Claude)+guardrail — only paid when the
  gate fires, which is rare by design. Tier-0 template path adds ~0ms over the network hops.

## 10. Test plan
**Unit (no network — mock `retrieve`/`verify`/`compose`/`guardrail`/`remember`):**
- `should_speak`: danger always True even inside debounce window; ack → False; question →
  True; repeated `clear_step` on same node → `silent_norepeat`; two non-danger calls inside
  `DEBOUNCE_S` → second is `silent_debounce`.
- `resolve_referent`: "tighten it" with one high-conf part → resolved; two near-equal parts →
  ambiguous; no visible parts → none; explicit "the black wheel" → named bind via difflib.
- `advance_node`: condition match moves node; precondition-satisfied next is skipped (rear
  panel already open → jump to persistent-jam node); no-match stays + sets `_pending_confirm`.
- `_detect_hazard`: voice "I smell smoke" → True; benign "looks clean" → False; obs.hazards
  non-empty → True.
- `emit` fail-safes: composer returns None → Tier-0 text; empty verified chunks → no-coverage
  line; guardrail REFUSE → refuse line; injected exception → returns None.
- `_persona_mode`: each phase → expected mode.

**Integration (synthetic streams — the core assertion is *silence vs speech*):**
- Feed a scripted list of `VisualObservation`s + final transcripts through the machine; assert
  the sequence of returned `Optional[(text,mode)]` matches an expected silence/speech timeline.
- *Ambient-silence* test: 10 near-identical frames that don't change advice → exactly one
  speech (or zero), rest `None`.
- *Hazard pre-emption* test: a benign sequence then one frame with `hazards` → next return is
  speech in `safety` mode regardless of debounce.
- *Referent→confirm* test: "is it broken?" with two equal parts → `confirming`, asks which.
- *Done* test: terminal node reached → `is_done()` True, drives receipt (09).
Drive everything from fixtures in `tests/state/`; the graph is a small hand-authored
`list[RepairNode]`.

## 11. Build checklist (Tier-0 first)
1. `referent.py`: `resolve_referent` + `_match_named_part` + unit tests.
2. `machine.py` skeleton: `__init__` (inject collaborators), `RepairState` scratch fields,
   `current_node`/`node` accessors.
3. `_classify_intent` (keyword/heuristic, no LLM) + `_detect_hazard`.
4. `advance_node` + `_cond_satisfied` + `_skip_satisfied_preconds` + tests.
5. `should_speak` + anti-chatter (debounce, no-repeat) + tests (silence-by-default first).
6. `_template_compose` (Tier 0) and `emit` wired to mocked 02/06/11; fail-safe paths + tests.
7. `on_visual_observation` / `on_final_transcript` end-to-end with synthetic streams.
8. `_persona_mode`, `note_interrupt`, `is_done`; hand to live loop (12) / orchestrator (13).
9. (Tier 1) enable Claude composer behind `COMPOSE_TIER=1`; keep Tier 0 as fallback.
