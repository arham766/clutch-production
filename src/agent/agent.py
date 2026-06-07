"""ConversationAgent — the multi-modal support brain (HLD 04).

One agent serves Type / Talk / See over a single `SupportSession`. Per turn it decides whether to
retrieve, composes a **grounded** answer (cited from the company's docs via the injected
`retrieve`/`compose` — seam S4), and responds. Its signature move is **modality escalation**
("hit See and show me"). Grounding is non-negotiable: it states only retrieved, cited facts and
escalates rather than invent; it withholds a hardware step that lacks its doc's safety warning.

Dependencies are **passed in** (no sibling imports): `retrieve(RetrievalQuery)->list[Chunk]` and
`compose(query, chunks)->Answer` (Fardin's 03). Build/test against simple mocks.
"""

from __future__ import annotations

import inspect
import time
from typing import Callable, Optional

from contracts import (
    Answer,
    CatalogEntry,
    Chunk,
    IdentifyResult,
    RetrievalQuery,
    SupportSession,
)

# --- Tier-0 heuristics (the heavy net is the gateway guardrail, 06; this is local + testable) ---
_DANGER = ("smell", "burning", "smoke", "spark", "fire", "shock", "shocked", "sparking")
_ACK = ("thanks", "thank you", "got it", "ok", "okay", "great", "perfect", "cool", "nice")
_RESOLVED = ("that worked", "it works", "fixed", "working now", "resolved", "all good", "solved")
_SMALLTALK = ("hi", "hello", "hey", "good morning", "good afternoon", "good evening")
_AFFIRM = ("yes", "yeah", "yep", "yup", "correct", "right", "that's it", "thats it", "it is",
           "sure", "exactly", "ya", "yup", "affirmative", "this one")
_NEGATE = ("no", "nope", "nah", "not", "wrong", "isn't", "incorrect", "different", "another")
_QUESTION_STARTS = ("how", "what", "why", "where", "when", "which", "can", "do", "does", "is", "my")
_PHYSICAL = (
    "light", "blink", "led", "red", "green", "amber", "orange", "fell", "crack", "leak",
    "stuck", "jam", "noise", "loose", "smoke", "burning", "won't turn", "wont turn",
    "flashing", "broken", "bent", "wire", "port", "cable", "screen", "button",
)
# Hazards only — touching one of these needs a precaution. (NOT "open the"/"remove the"/"unplug":
# those are normal/safe steps; "unplug" is itself a precaution, see _WARN.)
_RISKY = ("the wire", "live wire", "bare wire", "exposed wire", "power supply", "capacitor",
          "high voltage", "voltage", "mains", "fuser", "hot surface", "blade", "sharp edge")
_WARN = ("warning", "caution", "danger", "unplug", "power off", "power-off", "turn off",
         "switch off", "disconnect", "wait", "cool", "gloves", "ppe", "before you", "first", "safety")

_SEE_NUDGE = "Want to just show me? Hit **See** and point your camera at it."

# Visual-question cues: in See mode, these mean "use the camera / what do you see" → route to VQA
# (look at the frame) instead of answering blind from docs. High-recall on purpose — in See mode a
# frame-grounded answer is rarely worse, and missing a "can you see this?" is the failure that hurts.
_VISUAL_CUES = (
    "see ", "seeing", "look", "watch", "show you", "showing", "shown",
    "this one", "is this", "does this", "what is this", "what's this", "whats this",
    "does it look", "how does it look", "what do you see", "can you see", "do you see",
    "you see", "check the", "check it", "check this", "right here", "over here",
    "what's wrong", "whats wrong", "what is wrong", "tell me what", "in the camera",
    "the crack", "cracked", "the lens", "the screen", "the port", "the button", "the light",
)


class ConversationAgent:
    def __init__(
        self,
        session: SupportSession,
        retrieve: Callable[[RetrievalQuery], list[Chunk]],
        compose: Callable[[RetrievalQuery, list[Chunk]], Answer],
        *,
        escalate: Optional[Callable[[str], None]] = None,
        catalog: Optional[list[CatalogEntry]] = None,
        compose_stream=None,          # optional async-gen compose(query_text, chunks) -> token deltas
        look=None,                    # optional async look(frames, question) -> str (See VQA)
        summarize=None,               # optional async summarize(text)->str: background memory compaction
        warm: bool = False,           # background-warm retrieval on start (worker; off in tests)
        clock: Callable[[], float] = time.monotonic,
        speak_debounce_s: float = 1.0,
        recall_n: int = 10,
    ):
        self.s = session
        self._retrieve = retrieve
        self._compose = compose
        self._compose_stream = compose_stream
        self._look_fn = look                              # See VQA: look(frames, question) -> str
        self._summarize_fn = summarize                    # background memory compaction (off hot path)
        self._summarizing = False
        self._last_stream_citations: list = []
        self._escalate = escalate or (lambda reason: None)
        self._catalog = {c.product_id: c for c in (catalog or [])}
        self._clock = clock
        self._debounce = speak_debounce_s
        self._recall_n = recall_n
        self.rolling_summary = ""
        self._last_spoken: Optional[str] = None
        self._last_speak_t = float("-inf")
        self._last_identify_sig: Optional[tuple] = None   # anti-loop: skip unchanged See looks
        self._pending_product: Optional[str] = None       # product Qwen guessed, awaiting user confirm
        self._rejected: set = set()                       # products the user said "no" to
        # Optional UI hook (set by realtime): on_context(query, chunks, ms) → publish 'moss_context'
        # so the widget's live Knowledge Matches panel + grounding sources light up per turn.
        self.on_context: Optional[Callable] = None
        # Optional frame source (set by realtime when a video track attaches): () -> jpeg bytes | None.
        # Lets a voice turn in See mode grab the current camera frame for a visual answer (VQA).
        self.frame_provider: Optional[Callable] = None
        # Speculative prefetch cache: realtime warms retrieval on the in-progress (interim STT)
        # utterance so the turn's retrieve is already done at endpoint. norm(query) -> (t, chunks).
        self._prefetch: dict = {}
        self._prefetch_inflight: set = set()
        if self.s.phase in (None, "", "active"):
            self.s.phase = "greeting"
        # Warm retrieval in the background on session start so the user's first question doesn't pay
        # the cold index-load (~1.6s) on the hot path. Opt-in (worker), and only with a running loop.
        if warm:
            try:
                import asyncio as _a
                _a.get_running_loop()
                _a.ensure_future(self._warm())
            except RuntimeError:
                pass

    async def _warm(self) -> None:
        try:
            await self._maybe(self._retrieve(
                RetrievalQuery(self.s.company_id, "warmup", self.s.product_id)))
        except Exception:
            pass

    # ---------------------------------------------------------------- seam S2
    async def on_user_turn(self, text: str) -> Optional[Answer]:
        """Type/Talk text turn → grounded turn (or a brief non-claim reply). Never raises.

        Async because the injected `retrieve`/`compose` (Fardin's S4) are async, and Realtime (05)
        awaits this method (`await agent.on_user_turn(...)`). Sync mock deps still work (_maybe).
        """
        try:
            self.add_turn("user", text)
            kind, payload = self._route(text)
            if kind == "chit":
                return self._emit(Answer(payload, []), "turn", "chit")
            return self._emit(await self._answer(payload, "question"), "turn", "question")
        except Exception:
            return None              # a turn never crashes the session (04 §6)

    async def stream_turn(self, text: str):
        """Low-latency Type/Talk turn: retrieve, then YIELD answer token-deltas as the LLM streams,
        so TTS can start almost immediately. Trades the citation/safety gate for latency (raw stream).
        Falls back to the one-shot _answer when no streaming composer is wired. Never raises."""
        try:
            self.add_turn("user", text)
            kind, payload = self._route(text)
            if kind == "chit":
                self._last_stream_citations = []
                self.add_turn("agent", payload)
                yield payload
                return
            import sys as _s, time as _t
            t0 = _t.monotonic()

            # See VQA: if they're on camera asking something visual, LOOK at the current frame first
            # (Qwen-VL), speak what we actually see, THEN ground the repair STEPS in docs on top of it.
            obs = self._maybe_look(payload)
            obs = (await obs) if inspect.isawaitable(obs) else obs
            if obs:
                print(f"[latency] look={(_t.monotonic()-t0)*1000:.0f}ms (vision saw it)",
                      file=_s.stderr, flush=True)
                yield obs + ("" if obs.endswith((".", "!", "?")) else ".")
                yield " "

            # Retrieve repair steps grounded on what we SAW (if anything) + the question.
            retrieval_text = (obs + " " + payload).strip() if obs else payload
            q = RetrievalQuery(self.s.company_id, retrieval_text, self.s.product_id)
            # Speculative prefetch: if realtime already warmed retrieval for this utterance while the
            # user was speaking, take it off the hot path; otherwise retrieve now.
            chunks = self._take_prefetched(retrieval_text)
            prefetched = chunks is not None
            if not prefetched:
                chunks = await self._maybe(self._retrieve(q)) or []
            t_ret = (_t.monotonic() - t0) * 1000
            self._emit_context(payload, chunks, t_ret)        # → live Knowledge Matches panel
            if not chunks or self._compose_stream is None:
                if obs:                                        # already spoke what we saw; no doc steps
                    self._last_stream_citations = []
                    self.add_turn("agent", obs)
                    return
                ans = await self._answer(payload, "question")   # fallback: one-shot (grounded path)
                self._last_stream_citations = ans.citations if ans else []
                if ans and ans.text:
                    self.add_turn("agent", ans.text, ans.citations)
                    yield ans.text
                return
            acc: list[str] = []
            # Recall: hand the composer the prior turns (minus the just-added current one) + rolling
            # summary, so the agent stays coherent across the call instead of answering each turn cold.
            ctx = self.recall_context()
            recent = ctx["recent"][:-1] if ctx["recent"] else []
            summary = self._case_summary(ctx)
            if obs:
                summary = (summary + f" [Seen now] {obs}").strip()  # steps target what we just saw
            try:
                stream = self._compose_stream(payload, chunks, history=recent, summary=summary)
            except TypeError:
                stream = self._compose_stream(payload, chunks)   # mock composer w/o recall params
            async for tok in stream:                            # raw token deltas
                if not acc:
                    print(f"[latency] retrieve={t_ret:.0f}ms{' (prefetched)' if prefetched else ''}  "
                          f"llm_first_token={(_t.monotonic()-t0)*1000:.0f}ms (since turn start)",
                          file=_s.stderr, flush=True)
                acc.append(tok)
                yield tok
            full = (((obs + " ") if obs else "") + "".join(acc)).strip()
            # citations for the card = the sources we retrieved from (not per-sentence-gated)
            self._last_stream_citations = [
                {"doc": c.source or c.metadata.get("source", ""),
                 "section": c.metadata.get("section", ""), "score": c.score}
                for c in chunks[:3]
            ]
            if full:
                self.add_turn("agent", full, self._last_stream_citations)
        except Exception:
            return

    async def on_identify(self, result: IdentifyResult) -> Optional[Answer]:
        """See: Qwen looked at the device. Drive a CONVERSATION — confirm the product first
        ("I see an X — is that right?"), then ask for the problem on confirm. Never blurts a fix
        off the image. Anti-loop + change-gating keep it from re-asking every frame. Never raises."""
        try:
            self.s.modality = "see"                   # they're on camera
            # Anti-loop: ignore looks that haven't materially changed (same product / candidates).
            sig = (result.product_id, tuple(c.product_id for c in result.candidates))
            if sig == self._last_identify_sig:
                return None
            self._last_identify_sig = sig

            # Pick the best guess Qwen offers that the user hasn't already rejected.
            guess = result.product_id
            if guess is None:
                guess = next((c.product_id for c in result.candidates
                              if c.product_id not in self._rejected), None)
            if guess in self._rejected:
                guess = None

            if guess and guess == self.s.product_id:
                return None                           # already confirmed this device → stay quiet
            if guess:
                self._pending_product = guess
                self.s.phase = "confirming"
                name = self._name(guess)
                return self._emit(
                    Answer(f"I see a {name} — is that the device you need help with? (yes / no)", []),
                    "vision", "confirm")
            # No usable guess → ask the customer.
            self.s.phase = "confirming"
            return self._emit(
                Answer("I can't quite tell what device this is — could you hold it steady in view, "
                       "or tell me what it is?", []),
                "vision", "confirm")
        except Exception:
            return None

    # ---------------------------------------------------------------- conversation routing
    def _route(self, text: str) -> tuple:
        """Decide what a Type/Talk turn means given the conversation phase. Returns (kind, payload):
        'chit' → speak payload verbatim (confirm/problem prompts, acks); 'answer' → grounded answer
        on payload. This is the conversational glue: confirm product → ask problem → answer."""
        t = text.lower().strip()
        # 1) We asked "is this an X?" and are waiting for yes/no.
        if self.s.phase == "confirming" and self._pending_product:
            name = self._name(self._pending_product)
            if self._is_negate(t):
                self._rejected.add(self._pending_product)
                self._pending_product = None
                self.s.phase = "identifying"
                self._last_identify_sig = None        # let the next look re-guess
                return ("chit", "No problem — show me the device again, or tell me what it is.")
            if self._is_affirm(t):
                self.s.product_id = self._pending_product
                self._pending_product = None
                self.s.phase = "awaiting_problem"
                return ("chit", f"Great — what problem are you having with your {name}?")
            return ("chit", f"Just to confirm — is this a {name}? (yes / no)")
        # 2) Product confirmed; this turn IS the problem description → grounded answer.
        if self.s.phase == "awaiting_problem":
            self.s.phase = "assisting"
            return ("answer", text)
        # 3) Normal turn.
        if self.s.phase in ("greeting", "identifying"):
            self.s.phase = "assisting"
        if self._is_resolved_text(text):
            self.s.phase = "resolved"
        intent = self._classify(text)
        if intent in ("ack", "smalltalk"):
            return ("chit", self._chit(intent))
        return ("answer", text)

    def _is_affirm(self, t: str) -> bool:
        return any(w in t for w in _AFFIRM) and not self._is_negate(t)

    def _is_negate(self, t: str) -> bool:
        words = t.replace("'", "").split()
        return any(w in words or t == w or t.startswith(w + " ") for w in _NEGATE)

    def _name(self, pid: Optional[str]) -> str:
        c = self._catalog.get(pid) if pid else None
        return c.name if c else (pid or "this device")

    # ---------------------------------------------------------------- core
    @staticmethod
    async def _maybe(value):
        """Await `value` if it's awaitable — lets the agent accept async (real S4) or sync (mock) deps."""
        return await value if inspect.isawaitable(value) else value

    async def _answer(self, text: str, intent: str, *, retrieval_text: Optional[str] = None) -> Answer:
        """Retrieve, then compose grounded. `text` is the natural question handed to compose;
        `retrieval_text` (defaults to `text`) is what's matched against docs — the See path passes
        distilled signal keywords for matching while compose still gets the natural prose summary,
        since the LLM composer refuses a bag of keywords as a 'question' (NOT_IN_DOCS)."""
        in_video = self.s.modality == "see"
        # `need` stays empty: it's for semantic hints, not the classifier intent, and every extra
        # token hurts the keyless overlap score (reintroduce doc-keyword hints once Moss is primary).
        q = RetrievalQuery(self.s.company_id, retrieval_text or text, self.s.product_id)
        chunks = await self._maybe(self._retrieve(q)) or []
        self._emit_context(q.text, chunks)               # → live Knowledge Matches panel
        if not chunks:
            return self._no_coverage(offer_see=not in_video)
        ans = await self._maybe(self._compose(text, chunks))     # S4: compose(answer_query, chunks)
        if not ans or not ans.citations:              # ungrounded / refusal → never speak it
            return self._no_coverage(offer_see=not in_video)
        ans = self._safety_filter(ans)
        if self.should_escalate_modality(ans, q, chunks):
            ans = Answer((ans.text + " " + _SEE_NUDGE).strip(), ans.citations)
        return ans

    def should_escalate_modality(self, answer, query: RetrievalQuery, chunks) -> bool:
        """Nudge to See when not already on camera and the problem is physical/visual."""
        return self.s.modality != "see" and self._is_physical(query.text)

    def _no_coverage(self, offer_see: bool) -> Answer:
        self.s.phase = "escalating"
        self._escalate("no_doc_coverage")
        msg = "I don't see that in the docs."
        msg += (" " + _SEE_NUDGE) if offer_see else " Let me connect you with a person."
        return Answer(msg, [])

    def _safety_filter(self, ans: Answer) -> Answer:
        """Withhold a risky hardware step that lacks a safety warning (04 §3)."""
        t = ans.text.lower()
        if any(k in t for k in _RISKY) and not any(w in t for w in _WARN):
            self.s.phase = "escalating"
            self._escalate("unsafe_step_without_warning")
            return Answer(
                "That step has safety risks I can't confirm from the docs — "
                "let me connect you with a person.",
                [],
            )
        return ans

    # ---------------------------------------------------------------- speak gate + recall
    def should_speak(self, trigger: str, intent: Optional[str] = None) -> bool:
        if trigger == "turn":
            return True                               # always respond to a user turn
        return intent in ("fix", "confirm", "safety")  # ambient (See): only when useful

    def _emit(self, ans: Answer, trigger: str, intent: str) -> Optional[Answer]:
        if not self.should_speak(trigger, intent):
            return None
        now = self._clock()
        if ans.text == self._last_spoken and (now - self._last_speak_t) < self._debounce:
            return None                               # no-repeat within debounce
        self.add_turn("agent", ans.text, ans.citations)
        self._last_spoken = ans.text
        self._last_speak_t = now
        return ans

    def add_turn(self, role: str, text: str, refs=None) -> None:
        if self.s.history and self.s.history[-1].get("role") == role and self.s.history[-1].get("text") == text:
            return                                    # dedup consecutive identical
        self.s.history.append({"role": role, "text": text, "refs": refs or []})
        if len(self.s.history) > self._recall_n * 2:
            self._fold_summary()        # immediate, deterministic bound (always-available read)
            self._maybe_compact()       # background LLM compaction (off the hot path)

    def _fold_summary(self) -> None:
        old, self.s.history = self.s.history[: -self._recall_n], self.s.history[-self._recall_n:]
        # Keep BOTH roles (user + agent), labelled — a user-only summary loses what the agent already
        # said/recommended, so it "forgets what it was talking about". And keep the TAIL of the
        # rolling summary (most recent), not the head — appending then truncating to [:N] froze the
        # summary on the OLDEST content and dropped every newer fold (the real recall bug).
        snippet = "; ".join(f"{h['role']}: {h['text']}" for h in old if h.get("text"))
        self.rolling_summary = (self.rolling_summary + " " + snippet).strip()[-900:]

    def _maybe_compact(self) -> None:
        """Off the hot path: once the rolling summary has grown, spawn a background LLM pass that
        rewrites it tighter (denser memory, same budget). Never blocks a turn; the deterministic fold
        above already produced a usable summary, and reads always use the latest available one."""
        if not self._summarize_fn or self._summarizing or len(self.rolling_summary) < 400:
            return
        try:
            import asyncio as _a
            _a.get_running_loop()                          # no loop (sync tests) → skip; fold stands
        except RuntimeError:
            return
        self._summarizing = True
        import asyncio as _a
        _a.ensure_future(self._compact_async())

    async def _compact_async(self) -> None:
        try:
            base = self.rolling_summary
            better = (await self._maybe(self._summarize_fn(base)) or "").strip()
            if better:
                # preserve anything appended to the summary while we were compacting (a plain suffix)
                extra = self.rolling_summary[len(base):] if self.rolling_summary.startswith(base) else ""
                self.rolling_summary = (better + ((" " + extra.strip()) if extra.strip() else "")).strip()[-1000:]
                import sys as _s
                print(f"[memory] compacted summary -> {len(self.rolling_summary)} chars",
                      file=_s.stderr, flush=True)
        except Exception:
            pass
        finally:
            self._summarizing = False

    def recall_context(self) -> dict:
        return {
            "phase": self.s.phase,
            "product_id": self.s.product_id,
            "recent": self.s.history[-self._recall_n:],
            "summary": self.rolling_summary,
        }

    def _case_summary(self, ctx: dict) -> str:
        """Stable per-call 'case facts' (confirmed device) PLUS the rolling summary. The device is
        prepended so it survives history folding — the agent never loses which product it's helping
        with, even deep into a long call."""
        case = ""
        if self.s.product_id:
            case = f"[Case] Helping with: {self._name(self.s.product_id)}."
        summary = (ctx.get("summary") or "").strip()
        return (case + (" " + summary if summary else "")).strip()

    def _emit_context(self, query: str, chunks, ms=None) -> None:
        """Fire the optional UI hook (set by realtime) so the widget's Knowledge Matches panel +
        grounding sources update with what was retrieved for this turn. Never raises into the turn."""
        if self.on_context and chunks:
            try:
                self.on_context(query, chunks, ms)
            except Exception:
                pass

    def is_resolved(self) -> bool:
        return self.s.phase == "resolved"

    # ---------------------------------------------------------------- heuristics
    def _classify(self, text: str) -> str:
        t = text.lower().strip()
        words = t.split()
        if any(k in t for k in _DANGER):
            return "danger"
        if any(t == g or t.startswith(g + " ") for g in _SMALLTALK):
            return "smalltalk"
        if any(k in t for k in _ACK) and len(words) <= 5:
            return "ack"
        if "?" in t or (words and words[0] in _QUESTION_STARTS):
            return "question"
        return "observation"

    def _is_physical(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in _PHYSICAL)

    def _is_visual_question(self, text: str) -> bool:
        """Keyword heuristic: does this turn want the camera ('can you see…', 'look at this',
        'what's wrong', 'the crack')? High-recall — in See mode a frame-grounded answer rarely hurts."""
        t = " " + text.lower().strip() + " "
        return any(cue in t for cue in _VISUAL_CUES)

    async def _maybe_look(self, question: str) -> str:
        """In See mode with a visual question + a live frame, ask the vision model what it sees and
        return one short observation ("" when not applicable / on any failure — never blocks the turn)."""
        if not (self.s.modality == "see" and self._look_fn and self.frame_provider
                and self._is_visual_question(question)):
            return ""
        try:
            frame = self.frame_provider()
        except Exception:
            frame = None
        if not frame:
            return ""
        try:
            return (await self._maybe(self._look_fn([frame], question)) or "").strip()
        except Exception:
            return ""

    # ---------------------------------------------------------------- speculative prefetch
    @staticmethod
    def _norm_q(text: str) -> str:
        return " ".join((text or "").lower().split()).rstrip("?.!,")

    async def prefetch_retrieval(self, text: str) -> None:
        """Speculatively retrieve for an in-progress utterance (interim STT) so the turn's retrieve is
        already warm at endpoint. Cached by normalized query; safe to drop (retrieval has no side
        effects). De-duped by key so repeated/identical interims don't refire. Never raises."""
        key = self._norm_q(text)
        if len(key) < 8 or key in self._prefetch or key in self._prefetch_inflight:
            return
        self._prefetch_inflight.add(key)
        try:
            q = RetrievalQuery(self.s.company_id, text, self.s.product_id)
            chunks = await self._maybe(self._retrieve(q)) or []
            self._prefetch[key] = (self._clock(), chunks)
            if len(self._prefetch) > 8:                       # bound the cache
                oldest = min(self._prefetch, key=lambda k: self._prefetch[k][0])
                self._prefetch.pop(oldest, None)
        except Exception:
            pass
        finally:
            self._prefetch_inflight.discard(key)

    def _take_prefetched(self, text: str, *, max_age_s: float = 8.0):
        """Return (and consume) prefetched chunks for this query if a fresh speculative result exists."""
        hit = self._prefetch.pop(self._norm_q(text), None)
        if hit and (self._clock() - hit[0]) <= max_age_s:
            return hit[1]
        return None

    def _is_resolved_text(self, text: str) -> bool:
        t = text.lower()
        return any(k in t for k in _RESOLVED)

    def _chit(self, intent: str) -> str:
        return "You're welcome — anything else I can help with?" if intent == "ack" \
            else "Hi! What can I help you fix today?"

    def _confirm_text(self, result: IdentifyResult) -> str:
        names = [(self._catalog[c.product_id].name if c.product_id in self._catalog else c.product_id)
                 for c in result.candidates[:3]]
        if names:
            return "I want to pull the right manual — is this the " + " or the ".join(names) + "?"
        return "I can't tell which product this is — could you tell me the model?"
