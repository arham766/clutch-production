# Clutch LLD 03-S — Speculative Retrieval (interim-STT pre-fetch)

**Status:** Draft v0.1 (reflects shipped code) · **Build tier:** 0 · **Owner:** Fardin (Section D)
**Implements:** latency optimization for [HLD 03 — Retrieval & Grounding](../hld/03-retrieval-grounding.md) +
[HLD 05 — Realtime](../hld/05-realtime.md) turn-taking
**Lives in:** `src/agent/agent.py` (the `ConversationAgent` speculation machinery) + the realtime glue
in `src/realtime/{session,bridge}.py`. **Not** a new subfolder — it is a cross-seam optimization on the
existing Agent (04) ↔ Retrieval (03) ↔ Realtime (05) path.
**Scope of this doc:** the *speculative pre-fetch* mechanism only — firing a Moss retrieve on interim
STT partials so the chunk round-trip overlaps the endpointing wait, and reusing that pre-fetch when the
turn commits. The retrieve/compose internals themselves are [LLD 03](03-retrieval-grounding.md).

> Implementation-level: the exact state, the fire path, the consume path, the cache-key matching
> strategy (current + planned upgrade), the correctness invariants that keep it from ever changing an
> answer, the concurrency/cancellation model, the new config knobs, and the test plan (the four shipped
> tests + the gaps).

---

## 0. Why this exists (and what it's actually worth)

A spoken turn's wall-clock, measured live against the OpenRouter gateway (2026-06-07):

```
mic → STT → endpoint wait → [retrieve] → [LLM compose TTFT] → TTS → audio
              0.2–1.0s        ~0.1s warm     ~1.8s (race)      ~0.15s
```

The two **serial** nodes between turn-end and first token are **retrieve** and **LLM compose**. The LLM
TTFT (~1.8s) dominates and cannot be started early — it needs the *final* transcript **and** the chunks.
The retrieve, however, only needs the **query text**, and a good approximation of that text exists
*before* the turn commits: the interim STT partials streaming in during the user's speech.

**The idea:** fire the Moss retrieve on an interim partial, in the background, so by the time
endpointing fires and the final transcript commits, the chunks are already in hand. The retrieve
round-trip is moved off the critical path and **hidden behind the endpointing wait** (which the system
is spending anyway).

**Honest cost/benefit (post-measurement).** Warm Moss is ~0.1s. So speculation hides ~100ms on a hit —
real, but small next to the 1.8s LLM TTFT. It is best understood as **cheap insurance against a cold or
slow retrieve** (network blip, post-idle index eviction, a heavier `top_k`) plus a steady ~100ms, not as
the headline latency win. The headline win is the compose-model race (LLD 03 §7); this is the second-order
trim. This doc documents it precisely so the win is *real when it hits* and *never harmful when it misses*.

---

## 1. Ownership & seam contract

| Direction | Item | How it stays in-bounds |
|---|---|---|
| **Owns** | `ConversationAgent.speculate`, `_retrieve_for`, `_spec_key`, `_norm`, the `_spec_cache`/`_spec_inflight` state (`agent.py:105-164`) | All inside the brain (04); imports only `contracts` + stdlib `asyncio`. |
| **Trigger (in)** | interim STT transcript | Realtime (05) `session.on("user_input_transcribed")` → `brain.prefetch_interim(text)` (`session.py:120-127`) → `agent.speculate(text)` if the brain exposes it (duck-typed, `bridge.py:58-66`). |
| **Consume (in)** | the committed turn | `on_user_turn` / `stream_turn` call `_retrieve_for(q)` instead of `retrieve` directly (`agent.py:207`, `:281`). |
| **Depends on** | the injected `retrieve(RetrievalQuery)->Chunk[]` (S4) | Same callable the non-speculative path uses; speculation never reaches Moss itself. |

**Duck-typed, never required.** `prefetch_interim` no-ops if the brain has no `speculate` (`bridge.py:60`);
`speculate` no-ops outside an event loop (sync mock/test context, `agent.py:130-133`). A brain with no
speculation, or a realtime layer that never calls it, both work unchanged — speculation is purely
additive.

---

## 2. Data flow

```
            user speaking …
   ┌──────────────────────────────────────────────────────────┐
   │ STT (Cartesia ink-2, streaming, interim_results=True)      │
   └───────────────┬───────────────────────────────────────────┘
                   │ interim "how do I clear a paper" (is_final=False)
                   ▼
   session._on_stt ──► brain.prefetch_interim(text) ──► agent.speculate(text)
                                                            │  (debounce, min-words,
                                                            │   dedup, loop check)
                                                            ▼
                                              asyncio.ensure_future(retrieve(q))   ── background ──┐
                                                            │                                       │
   ── endpointing wait (0.2–1.0s) runs concurrently ────────┼───────────────────────────────────  │
                   │ final "How do I clear a paper jam?" (is_final=True)                            │
                   ▼                                                                                │
   BrainLLM.chat ─► stream_turn(text) ─► _retrieve_for(q)                                           │
                                              │  key match?                                         │
                                              ├─ completed  → pop from _spec_cache  ◄───────────────┘ (done)
                                              ├─ in-flight  → await the running task ◄────────────── (still running)
                                              └─ miss       → live retrieve(q)  (correctness-neutral)
                                              ▼
                                          chunks → compose race → tokens → TTS
```

The LLM is reached **only** from the committed-turn path (`stream_turn`), and **only** after
`_retrieve_for` returns. Speculation never feeds the LLM; it only pre-populates the chunk cache.

---

## 3. State (all on the `ConversationAgent` instance)

```python
# agent.py:80-85  (per SupportSession — one agent per room)
self._spec_cache:    dict[tuple, list[Chunk]] = {}   # key -> completed pre-fetch results
self._spec_inflight: dict[tuple, object]      = {}   # key -> asyncio.Task (running pre-fetches)
self._spec_last_t:   float = float("-inf")           # last time we FIRED (for debounce)
self._spec_interval_s: float = 0.3                   # debounce: max one fire / 300ms
self._spec_min_words:  int   = 3                     # ignore partials with < 3 content words
self._spec_cache_max:  int   = 4                     # bound the completed-cache (insertion-ordered)
```

`_spec_cache` and `_spec_inflight` are keyed by the **scope-aware** key (§4). Both are bounded:
`_spec_cache` evicts oldest-first past `_spec_cache_max` (`agent.py:145-146`); `_spec_inflight` drains
itself via each task's done-callback (`agent.py:139-148`).

---

## 4. The cache key — scope-aware, normalized

```python
@staticmethod
def _norm(text: str) -> str:                                   # agent.py:106
    return " ".join((text or "").lower().split()).strip(" .,!?;:")

def _spec_key(self, company_id, product_id, text) -> tuple:    # agent.py:110
    return (company_id, product_id, self._norm(text))
```

The key is `(company_id, product_id, normalized_text)`. Two non-obvious requirements both come from this:

1. **Normalization absorbs casing/punctuation drift.** The interim `"how do i clear a paper jam"` and the
   final `"How do I clear a paper jam?"` normalize to the same string → **hit** (proven by
   `test_speculation_hit_skips_live_retrieve`). Without this, every final would miss.
2. **`product_id` + `company_id` are part of the key — non-negotiable for correctness.** A pre-fetch
   scoped to product A must **never** serve a turn that has since re-scoped to product B (the user
   confirmed a different product mid-utterance). Including the scope in the key makes a re-scope a
   guaranteed miss → a fresh, correctly-scoped retrieve (proven by
   `test_rescope_to_other_product_does_not_serve_stale_chunks`).

### 4.1 The matching gap (current limitation + planned upgrade)

**Current:** exact match on the *normalized* string. The final transcript is almost always a **superset**
of the interim that triggered the pre-fetch (more words arrived after the partial fired). So unless the
final happens to normalize-equal a partial we fired on, the consume path falls to the **in-flight await**
(partial win — we still skip starting fresh) or a **live retrieve** (no win, but correct). Measured warm
Moss being ~100ms, this is acceptable today, but the hit rate is the lever that decides whether
speculation pays off at all.

**Planned upgrade (gated on measured hit rate, §11):** relax the consume-side match from equality to
**prefix containment** — on a miss, before going live, check whether any cached/in-flight key's normalized
text is a **prefix of** (or high token-overlap with) the final's normalized text under the same
`(company_id, product_id)`. The last interim before endpointing is typically `final` minus the trailing
word or two, so a prefix check converts most "superset" misses into hits. Kept **off by default** until
the hit-rate metric (§11) shows the exact-match rate is low enough to justify the extra comparison —
premature fuzzy-matching risks serving chunks retrieved for a *different* question, which would silently
degrade grounding. The scope tuple stays an exact-match guard regardless.

---

## 5. The fire path — `speculate(text)`

```python
def speculate(self, text: str) -> None:   # agent.py:115  — never blocks, never raises into caller
```

Ordered guards (each a cheap early-return), then the background fire:

| Step | Guard | Rationale |
|---|---|---|
| 1 | `len(_norm(text).split()) < _spec_min_words` → return | < 3 words isn't a meaningful query yet ("how do") — proven by `test_short_interim_is_not_speculated`. |
| 2 | `key in _spec_cache or key in _spec_inflight` → return | already have it / already fetching — no duplicate work. |
| 3 | `now - _spec_last_t < _spec_interval_s` → return | debounce: STT emits many partials/sec; cap fires to ~1/300ms so we don't hammer Moss. |
| 4 | no running event loop → return | sync mock/test context can't background a task; speculation is a live-path-only optimization. |
| 5 | fire | `task = ensure_future(self._maybe(self._retrieve(q)))`; record in `_spec_inflight[key]`; attach `_done` callback. |

The `_done` callback (`agent.py:139-148`) moves the task's result into `_spec_cache`, removes it from
`_spec_inflight`, and evicts the oldest cache entry past the bound. A task that raised is swallowed
(`except: return`) — a failed pre-fetch simply means the consume path will retrieve live.

**`speculate` is `void` and total:** the entire body is wrapped so nothing it does — a bad query, a
scheduling error, a Moss exception — can ever propagate into the STT event handler that called it
(`agent.py:120,149-150`). A pre-fetch is a bet; losing the bet costs nothing.

---

## 6. The consume path — `_retrieve_for(q)`

```python
async def _retrieve_for(self, q: RetrievalQuery) -> list[Chunk]:   # agent.py:152
    key = self._spec_key(q.company_id, q.product_id, q.text)
    if key in self._spec_cache:
        return self._spec_cache.pop(key) or []        # completed → instant, and consume (pop)
    task = self._spec_inflight.get(key)
    if task is not None:
        try:    return (await task) or []             # in-flight → await (cheaper than restarting)
        except Exception: pass                        # task failed → fall through to live
    return await self._maybe(self._retrieve(q)) or []  # miss → live retrieve (correctness-neutral)
```

Three outcomes, in order of latency win:

1. **Completed hit** — chunks already cached; returned instantly and **popped** (a pre-fetch is consumed
   once; it must not serve a later, different turn that happens to collide on key).
2. **In-flight hit** — the pre-fetch is still running; **await the same task** rather than starting a
   second identical query (proven by `test_inflight_speculation_is_awaited_not_restarted`).
3. **Miss** — no matching pre-fetch (or it errored); run the **live retrieve**. Identical result to a
   system with no speculation at all → correctness-neutral (proven by
   `test_mismatch_falls_back_to_live_retrieve`).

Both `on_user_turn`'s grounded `_answer` (`agent.py:281`) and the streaming `stream_turn` (`agent.py:207`)
go through `_retrieve_for`, so speculation benefits Type, Talk, and the streaming voice path uniformly.

---

## 7. Correctness invariants (what makes this safe)

| # | Invariant | Enforced by |
|---|---|---|
| I1 | **The LLM is always gated on the FINAL transcript.** Speculation pre-fetches *chunks*; it never composes or speaks. An early/wrong partial can at worst cause a wasted pre-fetch, never a wrong answer. | Compose is reached only from `stream_turn`/`_answer` after `_retrieve_for` (§2, §6). |
| I2 | **Correctness-neutral on miss.** A miss runs the exact live retrieve the non-speculative path would. | `_retrieve_for` fall-through (`agent.py:164`). |
| I3 | **No stale cross-scope serve.** A re-scoped product/company is a guaranteed key miss. | scope in `_spec_key` (§4). |
| I4 | **Single-consume.** A completed pre-fetch is `pop`-ed, not read, so it can't serve two different turns. | `_spec_cache.pop` (`agent.py:157`). |
| I5 | **Never raises into the hot path.** Both fire and consume swallow their own failures. | try/except in `speculate` + `_retrieve_for` (`agent.py:149,162`). |
| I6 | **Idempotent / read-only.** `retrieve` has no side effects, so firing it early and possibly discarding the result is safe to repeat. | property of the injected S4 retrieve. |
| I7 | **Bounded memory.** Both maps are capped/self-draining; a long session can't leak tasks or chunks. | `_spec_cache_max` eviction + done-callback `pop` (`agent.py:139-148`). |

---

## 8. Concurrency & cancellation

- **One agent per room/session** (`session.py:93`), so the speculation state is single-owner; no
  cross-session sharing, no lock needed on `_spec_cache`/`_spec_inflight` (single-threaded asyncio).
- **In-flight tasks are awaited, not cancelled, on consume** — the running query is the cheapest path to
  the chunks we need (§6.2). Tasks are only ever dropped by completing (the `_done` callback) or by the
  session ending (the event loop tearing down its tasks).
- **No explicit cancellation on barge-in / turn switch.** A pre-fetch for an abandoned partial completes
  in the background, lands in `_spec_cache`, and is evicted by the bound if never consumed. This is
  intentional: cancelling races the done-callback for no real saving (Moss query is ~100ms). *Open item
  §12: if `top_k`/network make pre-fetches expensive, add cancellation of superseded in-flight keys.*

---

## 9. Config knobs (currently hardcoded → promote to `config.py`)

The three tunables live as instance defaults today (`agent.py:83-85`). They should move to role-named
`Config` keys (00 §7) so they're tunable without an edit, mirroring the existing retrieval knobs:

```python
# src/config.py  (proposed additions, retrieval section)
spec_enabled:       bool  = True    # master switch (off → speculate() returns immediately)
spec_interval_s:    float = 0.3     # debounce between fires
spec_min_words:     int   = 3       # min content words in a partial before it's worth a query
spec_cache_max:     int   = 4       # completed-pre-fetch cache bound
spec_prefix_match:  bool  = False   # §4.1 upgrade — relax consume match to prefix containment
```

`ConversationAgent.__init__` already accepts these as parameters in spirit (it reads `self._spec_*`);
the wiring change is to thread `cfg` values in via `build_agent`/`make_agent` (`app.py:45,129`). Until
then the defaults above are the shipped behavior.

---

## 10. Failure modes

| Failure | Behavior | Why it's fine |
|---|---|---|
| Pre-fetch query raises (Moss down mid-speculation) | `_done` swallows; key never enters `_spec_cache`; consume misses → live retrieve (which itself falls back to local-cosine, LLD 03 §4) | I2, I5 |
| Final transcript ≠ any partial (superset) | consume misses → live retrieve; ~100ms not saved this turn | I2; §4.1 upgrade targets this |
| User re-scopes product mid-utterance | guaranteed miss → correctly-scoped live retrieve | I3 |
| No event loop (mock/unit context) | `speculate` no-ops; `_retrieve_for` still works (always live) | §5 step 4 |
| Rapid interims (10/sec) | debounce caps fires to ~3/sec; dedup drops same-key repeats | §5 steps 2-3 |
| Long session, many distinct partials | `_spec_cache` bounded to 4, oldest evicted; tasks self-drain | I7 |

---

## 11. Instrumentation — the metric that decides §4.1

Speculation is only worth its complexity if it **hits**. The one number to capture is the
**consume-path outcome distribution**: of all `_retrieve_for` calls on committed turns, what fraction were
completed-hit / in-flight-hit / miss. Proposed: a tiny counter on the agent, logged per turn alongside the
existing `stream_turn` timing print (`agent.py:218-220`):

```python
# in _retrieve_for, increment one of: self._spec_hit / self._spec_inflight_hit / self._spec_miss
print(f"[spec] hit={h} inflight={i} miss={m}  (this turn: {outcome})", file=sys.stderr)
```

Decision rule: if completed-hit rate is high, leave matching as exact (§4 today). If miss rate is
dominated by **supersets of a fired partial** (detectable: the final's normalized text *starts with* a
recent key), flip `spec_prefix_match` on (§4.1). Do **not** enable prefix matching speculatively — let the
metric justify it, because a loose match that serves chunks for a *different* question silently weakens
grounding (worse than a 100ms miss).

---

## 12. Test plan

### 12.1 Shipped unit tests — `tests/test_speculative_retrieval.py` (all green, keyless)

| Test | Invariant proven |
|---|---|
| `test_speculation_hit_skips_live_retrieve` | completed-hit path + normalization absorbs casing/punctuation (I-norm, §6.1) |
| `test_inflight_speculation_is_awaited_not_restarted` | in-flight await, not a second query (§6.2) |
| `test_mismatch_falls_back_to_live_retrieve` | correctness-neutral miss (I2) |
| `test_rescope_to_other_product_does_not_serve_stale_chunks` | scope-keyed, no stale cross-scope serve (I3) |
| `test_short_interim_is_not_speculated` | min-words guard (§5 step 1) |

These use a `CountingRetrieve` stand-in that records every query text actually run — the calls
speculation is supposed to *avoid* — and set `_spec_interval_s = 0` to disable debounce so each interim
fires deterministically.

### 12.2 Gaps to add

| Test | Asserts |
|---|---|
| `test_debounce_caps_fire_rate` | with `_spec_interval_s = 0.3`, three interims inside 300ms → exactly one fire (debounce, §5 step 3) |
| `test_cache_bound_evicts_oldest` | > `_spec_cache_max` completed pre-fetches → oldest evicted, newest retained (I7) |
| `test_single_consume_pops` | a completed pre-fetch consumed once; a second `_retrieve_for` same key → live (I4) |
| `test_prefix_match_when_enabled` | with `spec_prefix_match=True`, final = interim + trailing word → in-flight/completed hit (§4.1) |
| `test_speculate_swallows_retrieve_error` | injected retrieve raises → `speculate` returns cleanly, consume falls to live (I5) |

### 12.3 No live lane

Like the rest of Section D, there is **no keyed pytest lane** for speculation — the latency win is
observed in the realtime e2e (`scripts/realtime_mini.py` / a live `run_clutch` turn) via the `[spec]`
and `[latency]` stderr lines (§11, `agent.py:218-220`), not asserted as a unit. The units above pin
*correctness*; the *latency benefit* is measured, not unit-tested.

---

## 13. Open questions

1. **Hit-rate measurement first (§11).** Until the consume-outcome distribution is logged from a real
   session, the prefix-match upgrade (§4.1) stays off. Ship the counter, run a handful of live turns,
   decide.
2. **Cancellation of superseded in-flight pre-fetches (§8).** Only worth it if `top_k`/network make a
   pre-fetch meaningfully expensive — current warm Moss (~100ms) doesn't justify the race against the
   done-callback.
3. **Speculate the *embedding*, not the whole query?** If Moss exposes query-embedding separately from
   search, the embedding (the slower half of a hybrid query) could be sped on the interim while the
   filter/scope binds on the final — finer-grained than today's whole-query pre-fetch. Needs SDK support
   (LLD 03 §5.1 documents no such split today).
4. **Coordinate with barge-in.** When `allow_interruptions` cuts a turn, the in-flight pre-fetch for the
   abandoned utterance is currently left to evict naturally — confirm that's still correct once the
   realtime turn-handling settles post-1.5.17 (see [orchestration note re: deprecations]).

---

*Confirmation: this LLD specifies speculative retrieval — pre-firing the Moss round-trip on interim STT
partials so it overlaps the endpointing wait, consumed on the committed turn via a scope-aware,
normalized cache key with completed/in-flight/miss resolution. It is additive and duck-typed (no-ops
without a loop or without a `speculate`-capable brain), correctness-neutral on miss, and scope-keyed so a
re-scoped turn never serves stale chunks. The measured win is ~100ms (warm-Moss insurance, not the
headline — the compose-model race is), so the prefix-match upgrade (§4.1) is gated on a shipped hit-rate
metric (§11) rather than enabled speculatively. State is bounded (I7), the hot path never raises (I5),
and the LLM is always gated on the final transcript (I1). Five unit tests ship today
(`tests/test_speculative_retrieval.py`); five more (§12.2) close the debounce/eviction/single-consume/
prefix/error-swallow gaps.*
