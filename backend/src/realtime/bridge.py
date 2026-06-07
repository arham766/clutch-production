"""ClutchAgent (S2) + BrainLLM — wire the brain (Tonmoy's S2 agent) into the LiveKit voice loop.

LiveKit's `generate_reply` path REQUIRES an LLM model on the session (it raises "trying to generate
reply without an LLM model" otherwise) — and that path is hit by BOTH spoken turns (STT → reply) and
typed turns (lk.chat → reply). So the brain is wired as the **session LLM** via `BrainLLM`: its
`chat()` pulls the latest user text, calls the agent's S2 `on_user_turn`, publishes the grounded
answer card (+ latency), and yields the cited text — which the framework streams into TTS +
transcript. One path for Type and Talk; no raw model ever runs (the brain does grounding/safety).

The See path is separate: FrameSampler → `ClutchAgent.handle_identify` → agent `on_identify`.

This layer HOLDS the agent and calls only its two S2 methods (`on_user_turn`/`on_identify`); it never
reads the agent's internal state.

Verified against livekit-agents 1.5.17:
    Agent.llm_node default calls activity.llm.chat(...); activity.llm = agent.llm or session.llm.
    LLMStream._run pushes ChatChunk(delta=ChoiceDelta(content=...)) onto self._event_ch.
"""

from __future__ import annotations

import logging
from typing import Optional

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, Agent, llm

from src.contracts import Answer, IdentifyResult
from src.events import AnswerEvent, ConfirmEvent, LatencyEvent

logger = logging.getLogger("clutch.realtime.bridge")


def _last_user_text(chat_ctx) -> str:
    """Pull the most recent user-turn text out of the LiveKit ChatContext."""
    items = getattr(chat_ctx, "items", None) or []
    for item in reversed(items):
        text = getattr(item, "text_content", None)
        if text:
            return text
    return ""


class ClutchAgent(Agent):
    """The 'brain' node. Type/Talk turns flow via BrainLLM (the session llm) → run_user_turn;
    the See path is handle_identify. Both call the passed-in S2 agent and publish via the publisher."""

    def __init__(self, agent, pub, *, latency_badge: bool = True):
        super().__init__(instructions="")  # instructions unused — the brain decides what to say
        self._agent = agent                # AgentLike — on_user_turn / on_identify (S2)
        self._pub = pub                    # EventPublisher
        self._latency_badge = latency_badge

    async def run_user_turn(self, text: str) -> str:
        """S2 Type/Talk: grounded turn. Publishes the answer card; returns the text for TTS+transcript."""
        ans: Optional[Answer] = await self._agent.on_user_turn(text)
        return self._emit(ans)

    async def stream_user_turn(self, text: str):
        """Low-latency S2: YIELD answer token-deltas (→ TTS starts early), publish the card at the end."""
        acc: list[str] = []
        async for tok in self._agent.stream_turn(text):
            acc.append(tok)
            yield tok
        full = "".join(acc).strip()
        if full:
            cites = getattr(self._agent, "_last_stream_citations", []) or []
            self._pub.send(AnswerEvent(text=full, citations=cites))

    async def handle_identify(self, result: IdentifyResult) -> str:
        """See path — invoked by the FrameSampler glue with an IdentifyResult (S8 → S2). Publishes
        the answer card and RETURNS the spoken text ("" if the agent stays silent). The caller speaks
        it via the AgentSession (NOT Agent.session — that needs an agent turn context the sampler
        task lacks, raising 'no activity context found')."""
        ans: Optional[Answer] = await self._agent.on_identify(result)
        return self._emit(ans)               # _emit(None) → "" (publishes nothing); else card + text

    @staticmethod
    def _confirm_prompt(result: IdentifyResult) -> str:
        label = result.model or result.brand or result.product_id or "this product"
        return f"Is this the {label}?"

    def _emit(self, ans: Optional[Answer]) -> str:
        if ans is None:
            return ""  # agent chose silence → no TTS, no card
        self._pub.send(AnswerEvent(text=ans.text, citations=ans.citations))  # card to widget
        ms = getattr(ans, "time_taken_ms", None)
        if self._latency_badge and ms:  # REAL Moss number, if present (never a fake 0)
            self._pub.send(LatencyEvent(time_taken_ms=ms))
        return ans.text  # → TTS speaks exactly the cited text


class _BrainLLMStream(llm.LLMStream):
    """One-shot stream: run the brain turn, push the answer text as a single ChatChunk."""

    async def _run(self) -> None:
        text_in = _last_user_text(self._chat_ctx)
        # Stream token-deltas straight through → LiveKit feeds them to TTS as they arrive (low latency).
        async for tok in self._llm._brain.stream_user_turn(text_in):   # type: ignore[attr-defined]
            if tok:
                self._event_ch.send_nowait(
                    llm.ChatChunk(id="brain", delta=llm.ChoiceDelta(role="assistant", content=tok))
                )


class BrainLLM(llm.LLM):
    """Adapts the ClutchAgent brain to the LiveKit LLM interface so it can be the session LLM."""

    def __init__(self, brain: ClutchAgent):
        super().__init__()
        self._brain = brain

    @property
    def model(self) -> str:
        return "clutch-brain"

    @property
    def provider(self) -> str:
        return "clutch"

    def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS,
             parallel_tool_calls=None, tool_choice=None, extra_kwargs=None) -> llm.LLMStream:
        return _BrainLLMStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)
