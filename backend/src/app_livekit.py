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

try:
    from src.agent.agent import ConversationAgent
except ImportError:
    class ConversationAgent:
        def __init__(self, session, retrieve_for, compose, **kwargs):
            self.session = session
            self.s = session
            self.retrieve_for = retrieve_for
            self.compose = compose
            
        async def on_user_turn(self, text):
            from src.contracts import Answer
            chunks = self.retrieve_for(text)
            # handle async vs sync compose
            import inspect
            if inspect.iscoroutinefunction(self.compose):
                ans = await self.compose(text, chunks)
            else:
                ans = self.compose(text, chunks)
            return Answer(text=ans.text if hasattr(ans, 'text') else str(ans), citations=getattr(ans, 'citations', []))
            
        async def on_identify(self, r):
            from src.contracts import Answer
            return Answer(text=f"I see {r.product_id}. How can I help?", citations=[])
        
        def is_resolved(self):
            return False
from src.contracts import Answer, CatalogEntry, Chunk, IdentifyResult, RetrievalQuery, SupportSession
from src.retrieval import make_retrieval


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
        try:
            from src.perception import make_identify
            vision_model = model or getattr(cfg, "vision_model", None) or "vision.identify"
            accept = getattr(cfg, "vision_accept", None) or 0.65
            return make_identify(gateway_client, model=vision_model, accept=accept)
        except ImportError:
            # Fallback to mock if Phase 2 is not integrated yet
            pass

    try:
        from src.perception import identify as _identify
        from src.perception.providers.mock import MockVisionProvider
        provider = MockVisionProvider()

        async def identify(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult:
            return await _identify(frames, catalog, provider=provider)
            
        return identify
    except ImportError:
        async def identify_mock(frames: list[bytes], catalog: list[CatalogEntry]) -> IdentifyResult:
            from src.contracts import IdentifyResult
            return IdentifyResult(product_id=catalog[0].product_id if catalog else "unknown", resolved=True)
        return identify_mock


def build_look(gateway_client=None, *, cfg=None, model: Optional[str] = None):
    """Perception (02) See-VQA: returns an async `look(frames, question) -> str`, or None when no
    gateway (the agent then simply skips the visual path and answers from docs)."""
    if gateway_client is None:
        return None
    try:
        from src.perception import make_look
        vision_model = model or getattr(cfg, "vision_model", None) or "vision.identify"
        return make_look(gateway_client, model=vision_model)
    except ImportError:
        return None



def make_scope_session(cfg=None, *, catalog: Optional[list[CatalogEntry]] = None,
                       company_resolver: Optional[Callable[[str], list[CatalogEntry]]] = None):
    """S6 ScopeFn for Realtime (05): (company_key, modality) -> SupportSession with the company's
    catalog stamped on (it feeds both vision closed-set and the agent's confirm prompts).

    `company_resolver(company_key) -> [CatalogEntry]` is where Arham's tenancy (06) plugs in; absent,
    every session gets the single `catalog` passed here (demo / single-tenant).
    """
    def scope_session(company_key: str, modality: str) -> SupportSession:
        import uuid
        cat = (company_resolver(company_key) if company_resolver else None) or catalog or []
        def minter(company_id: str, modality: str) -> dict:
            # Map API concepts (text/voice/video) to LiveKit routing concepts (type/talk/see)
            mod_map = {"text": "type", "voice": "talk", "video": "see"}
            internal_modality = mod_map.get(modality, "type")
            import time, json
            room_name = f"clutch-{company_id}-{int(time.time())}"
            metadata = json.dumps({"company_key": str(company_id), "modality": internal_modality})
            return {"room_name": room_name, "metadata": metadata}
        return SupportSession(
            session_id=uuid.uuid4().hex,
            company_id=company_key or "demo",
            modality=modality if modality in ("type", "talk", "see") else "type",
            catalog=list(cat),
        )
    return scope_session


def build_speech(cfg=None):
    """Speech (05): raw LiveKit STT/TTS plugins (AgentSession wants the raw plugin, not the wrapper).
    Raises a clean RuntimeError if CARTESIA_API_KEY is unset."""
    from src.speech import get_stt, get_tts
    return get_stt(cfg).stream(), get_tts(cfg).stream()


def build_clutch_worker(cfg=None, gateway_client=None, *, catalog: Optional[list[CatalogEntry]] = None,
                        company_resolver=None, scope_session=None, vad=None):
    """Full composition root for the realtime worker (05): wires the REAL per-session agent (04) +
    perception (02) + retrieval (03) + speech into a runnable WorkerOptions (→ realtime.run_worker /
    cli.run_app). Needs the `voice` deps + LiveKit/Cartesia creds to actually run.

    The agent is passed as a FACTORY: `run_session` builds one stateful ConversationAgent per room
    from the scoped session (one room == one SupportSession)."""
    from src.realtime import build_worker
    retrieve_for, compose = build_retrieval(cfg, gateway_client)

    # Low-latency raw token streaming for voice (no citation/safety gate). RACE: MiniMax (primary,
    # high quality but slow to first token) vs a fast instruct fallback — if the primary hasn't
    # produced a token within the deadline, switch to the fallback so first audio stays ~real-time.
    compose_stream = None
    if gateway_client is not None:
        import os as _os
        from src.retrieval import gateway_compose_race
        import openai
        
        _primary = getattr(cfg, "reason_model", None) or "reason.compose"
        _fast = (getattr(cfg, "reason_fast_model", None)
                 or _os.environ.get("REASON_FAST_MODEL") or "openrouter/qwen3-30b-a3b-instruct-2507")
        _deadline = float(getattr(cfg, "reason_deadline_s", None)
                          or _os.environ.get("REASON_DEADLINE_S", 2.0))

        # We must use a real openai.AsyncOpenAI client for streaming because GatewayClient lacks chat.completions.create
        base_url = getattr(cfg, "openrouter_base_url", None) or getattr(cfg, "tf_base_url", None)
        # Ensure it has /v1 for openai client
        if base_url and not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
            
        api_key = getattr(cfg, "openrouter_api_key", None) or getattr(cfg, "tf_api_key", None)
        openai_client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

        from src.gateway import route
        try:
            _primary_model = route(_primary)
        except Exception:
            _primary_model = _primary

        try:
            _fast_model = route(_fast)
        except Exception:
            _fast_model = _fast

        if base_url and "openrouter.ai" in base_url and _primary_model.startswith("openrouter/"):
            _primary_model = _primary_model.replace("openrouter/", "", 1)
        if base_url and "openrouter.ai" in base_url and _fast_model.startswith("openrouter/"):
            _fast_model = _fast_model.replace("openrouter/", "", 1)

        def compose_stream(answer_query, chunks, history=None, summary=""):  # noqa: E306  async gen
            return gateway_compose_race(answer_query, chunks, client=openai_client,
                                        primary_model=_primary_model, fallback_model=_fast_model,
                                        primary_deadline=_deadline,
                                        history=history, summary=summary)

    look = build_look(gateway_client, cfg=cfg)                         # See VQA (real if gw given)

    def make_agent(session: SupportSession) -> ConversationAgent:      # per-session stateful brain
        return ConversationAgent(session, retrieve_for, compose, compose_stream=compose_stream,
                                 look=look,   # See: answer visual questions off the live frame
                                 warm=True,   # background-warm Moss so the first turn isn't cold
                                 catalog=(getattr(session, "catalog", None) or catalog))

    identify = build_identify(gateway_client, cfg=cfg)                 # S8 vision (real if gw given)
    # Register the plugins on the MAIN THREAD here (importing the plugin module registers it; LiveKit
    # forbids registration from a job-runner thread). The STT/TTS *instances* are still built per-job
    # (factories below) inside run_session's loop, so Cartesia's aiohttp ws binds to the live loop
    # (building instances pre-loop → "Session is closed"). Best of both: import-on-main, build-in-loop.
    from livekit.plugins import cartesia, silero  # noqa: F401  (import = register on main thread)
    from src.speech import get_stt, get_tts
    stt = lambda: get_stt(cfg).stream()        # noqa: E731  (built per-job in the loop)
    tts = lambda: get_tts(cfg).stream()        # noqa: E731
    if vad is None:
        vad = silero.VAD.load()                # stateless model — fine to load once (main thread)
    scope = scope_session or make_scope_session(cfg, catalog=catalog, company_resolver=company_resolver)
    return build_worker(agent=make_agent, identify=identify, stt=stt, tts=tts, vad=vad,
                        scope_session=scope, cfg=cfg)


__all__ = ["build_retrieval", "build_agent", "build_identify",
           "make_scope_session", "build_speech", "build_clutch_worker"]
