"""Composition root — the C↔D fusing point (HLD 10 §4; Tonmoy 13 "connect later").

This is the ONE module allowed to import multiple sibling packages: it wires the real dependencies
together and hands them to Realtime (05), which constructs nothing itself. Everything below is a
config/credential switch — no FSM or seam shape changes (00 §7).

    agent      = build_agent(session, cfg=cfg, gateway_client=gw, catalog=catalog)
    identify   = build_identify(gw, cfg=cfg)          # S8 vision (the "See" path)
    # → pass agent + identify into realtime.run_session(...)

`build_retrieval` binds Fardin's S4 (retrieve_for + compose); `build_agent` injects them into the
ConversationAgent. With no creds everything resolves to the keyless mock path (local-cosine retrieve,
template compose, mock vision) so the whole stack runs offline.
"""

from __future__ import annotations

from typing import Callable, Optional

from agent import ConversationAgent
from contracts import Answer, CatalogEntry, Chunk, IdentifyResult, RetrievalQuery, SupportSession
from retrieval import make_retrieval


def build_retrieval(cfg=None, gateway_client=None):
    """Fardin's S4 (03): returns (retrieve_for, compose).

    gateway_client None → template compose (deterministic, no LLM).
    Moss creds in cfg/env → MossRetriever primary, local-cosine fallback; else local-cosine only.
    """
    return make_retrieval(cfg, gateway_client)


def build_agent(
    session: SupportSession,
    *,
    cfg=None,
    gateway_client=None,
    catalog: Optional[list[CatalogEntry]] = None,
    escalate: Optional[Callable[[str], None]] = None,
) -> ConversationAgent:
    """Build the real multi-modal brain (04) wired to retrieval (03). Realtime (05) awaits its
    `on_user_turn` / `on_identify`; it never sees which retrieval backend served."""
    retrieve_for, compose = build_retrieval(cfg, gateway_client)
    return ConversationAgent(
        session, retrieve_for, compose, escalate=escalate, catalog=catalog,
    )


def build_identify(gateway_client=None, *, cfg=None, model: Optional[str] = None):
    """Perception (02) S8: returns an async `identify(frames, catalog) -> IdentifyResult`.

    gateway_client present → real Qwen-VL via the gateway (model from `model`/cfg.vision_model).
    None → deterministic mock vision (keyless), so the See path runs offline.
    """
    if gateway_client is not None:
        from perception import make_identify
        vision_model = model or getattr(cfg, "vision_model", None) or "vision.identify"
        # Live camera dips confidence (motion/angle/glare); accept a bit lower than the still default
        # so a clear-enough look scopes the product instead of looping on "which product?".
        accept = getattr(cfg, "vision_accept", None) or 0.65
        return make_identify(gateway_client, model=vision_model, accept=accept)

    from perception import identify as _identify
    from perception.providers.mock import MockVisionProvider

    provider = MockVisionProvider()

    async def identify(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult:
        return await _identify(frames, catalog, provider=provider)

    return identify


def build_look(gateway_client=None, *, cfg=None, model: Optional[str] = None):
    """Perception (02) See-VQA: returns an async `look(frames, question) -> str`, or None when no
    gateway (the agent then simply skips the visual path and answers from docs)."""
    if gateway_client is None:
        return None
    from perception import make_look
    vision_model = model or getattr(cfg, "vision_model", None) or "vision.identify"
    return make_look(gateway_client, model=vision_model)


def make_scope_session(cfg=None, *, catalog: Optional[list[CatalogEntry]] = None,
                       company_resolver: Optional[Callable[[str], list[CatalogEntry]]] = None):
    """S6 ScopeFn for Realtime (05): (company_key, modality) -> SupportSession with the company's
    catalog stamped on (it feeds both vision closed-set and the agent's confirm prompts).

    `company_resolver(company_key) -> [CatalogEntry]` is where Arham's tenancy (06) plugs in; absent,
    every session gets the single `catalog` passed here (demo / single-tenant).
    """
    def scope_session(company_key: str, modality: str) -> SupportSession:
        cat = (company_resolver(company_key) if company_resolver else None) or catalog or []
        return SupportSession(
            company_id=company_key or "demo",
            modality=modality if modality in ("type", "talk", "see") else "type",
            catalog=list(cat),
        )
    return scope_session


def build_speech(cfg=None):
    """Speech (05): raw LiveKit STT/TTS plugins (AgentSession wants the raw plugin, not the wrapper).
    Raises a clean RuntimeError if CARTESIA_API_KEY is unset."""
    from speech import get_stt, get_tts
    return get_stt(cfg).stream(), get_tts(cfg).stream()


def build_clutch_worker(cfg=None, gateway_client=None, *, catalog: Optional[list[CatalogEntry]] = None,
                        company_resolver=None, scope_session=None, vad=None):
    """Full composition root for the realtime worker (05): wires the REAL per-session agent (04) +
    perception (02) + retrieval (03) + speech into a runnable WorkerOptions (→ realtime.run_worker /
    cli.run_app). Needs the `voice` deps + LiveKit/Cartesia creds to actually run.

    The agent is passed as a FACTORY: `run_session` builds one stateful ConversationAgent per room
    from the scoped session (one room == one SupportSession)."""
    from realtime import build_worker
    retrieve_for, compose = build_retrieval(cfg, gateway_client)

    # Low-latency raw token streaming for voice (no citation/safety gate). RACE: MiniMax (primary,
    # high quality but slow to first token) vs a fast instruct fallback — if the primary hasn't
    # produced a token within the deadline, switch to the fallback so first audio stays ~real-time.
    compose_stream = None
    warm_llm = None
    summarize = None
    if gateway_client is not None:
        import os as _os
        from retrieval import gateway_compose_race
        _primary = getattr(cfg, "reason_model", None) or "reason.compose"
        _fast = (getattr(cfg, "reason_fast_model", None)
                 or _os.environ.get("REASON_FAST_MODEL") or "openrouter/qwen3-30b-a3b-instruct-2507")
        _deadline = float(getattr(cfg, "reason_deadline_s", None)
                          or _os.environ.get("REASON_DEADLINE_S", 2.0))

        def compose_stream(answer_query, chunks, history=None, summary=""):  # noqa: E306  async gen
            return gateway_compose_race(answer_query, chunks, client=gateway_client,
                                        primary_model=_primary, fallback_model=_fast,
                                        primary_deadline=_deadline,
                                        history=history, summary=summary)

        async def warm_llm():     # noqa: E306  — establish the gateway connection + warm both racers
            import asyncio as _a

            async def _ping(model):
                try:
                    await gateway_client.chat.completions.create(
                        model=model, messages=[{"role": "user", "content": "hi"}],
                        max_tokens=1, temperature=0)
                except Exception:
                    pass

            await _a.gather(_ping(_primary), _ping(_fast))

        _SUMMARY_SYS = (
            "Compress this support-call memory into 2-4 short sentences. Keep only the facts: the "
            "device, the problem, what's been tried, and any resolution. No preamble, no list.")

        async def summarize(text):    # noqa: E306  — background memory compaction (fast model)
            try:
                r = await gateway_client.chat.completions.create(
                    model=_fast,
                    messages=[{"role": "system", "content": _SUMMARY_SYS},
                              {"role": "user", "content": text}],
                    max_tokens=160, temperature=0)
                return (r.choices[0].message.content or "").strip()
            except Exception:
                return ""

    look = build_look(gateway_client, cfg=cfg)                         # See VQA (real if gw given)

    def make_agent(session: SupportSession) -> ConversationAgent:      # per-session stateful brain
        return ConversationAgent(session, retrieve_for, compose, compose_stream=compose_stream,
                                 look=look,   # See: answer visual questions off the live frame
                                 summarize=summarize,  # background memory compaction (off hot path)
                                 warm=True,   # background-warm Moss so the first turn isn't cold
                                 catalog=(getattr(session, "catalog", None) or catalog))

    identify = build_identify(gateway_client, cfg=cfg)                 # S8 vision (real if gw given)
    # Register the plugins on the MAIN THREAD here (importing the plugin module registers it; LiveKit
    # forbids registration from a job-runner thread). The STT/TTS *instances* are still built per-job
    # (factories below) inside run_session's loop, so Cartesia's aiohttp ws binds to the live loop
    # (building instances pre-loop → "Session is closed"). Best of both: import-on-main, build-in-loop.
    from livekit.plugins import cartesia, silero  # noqa: F401  (import = register on main thread)
    from speech import get_stt, get_tts
    stt = lambda: get_stt(cfg).stream()        # noqa: E731  (built per-job in the loop)
    tts = lambda: get_tts(cfg).stream()        # noqa: E731
    if vad is None:
        vad = silero.VAD.load()                # stateless model — fine to load once (main thread)
    scope = scope_session or make_scope_session(cfg, catalog=catalog, company_resolver=company_resolver)
    from memory import FileMemoryStore                 # cross-call recall (per company/user)
    mem = FileMemoryStore()
    return build_worker(agent=make_agent, identify=identify, stt=stt, tts=tts, vad=vad,
                        scope_session=scope, cfg=cfg, warm_llm=warm_llm, memory=mem)


__all__ = ["build_retrieval", "build_agent", "build_identify",
           "make_scope_session", "build_speech", "build_clutch_worker"]
