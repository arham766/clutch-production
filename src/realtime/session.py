"""run_session — the LiveKit Agents job entrypoint (one room per SupportSession).

Receives ALL cross-layer dependencies as keyword args (agent, identify, stt, tts, vad,
scope_session, cfg) — constructs nothing it doesn't own and imports no sibling subfolder.

Duck-typed boundaries (NOT imported — passed in):
    agent (AgentLike, S2):   async on_user_turn(text) -> Answer | None
                             async on_identify(r: IdentifyResult) -> Answer | None
    identify (IdentifyFn, S8): async identify(frames: list[bytes], catalog) -> IdentifyResult
    scope_session (ScopeFn, S6): scope_session(company_key, modality) -> SupportSession
    stt / tts / vad:         LiveKit plugin instances (from speech.get_stt/get_tts, silero VAD)

Verified against livekit-agents 1.5.17 (see LLD §13):
    AgentSession(stt, tts, vad, allow_interruptions, min_interruption_duration,
                 min_endpointing_delay, max_endpointing_delay)  — llm omitted (lives in llm_node)
    session.start(agent=, room=, room_input_options=RoomInputOptions(audio/video/text_enabled),
                  room_output_options=RoomOutputOptions(audio_enabled, transcription_enabled))
    session.on("agent_state_changed") → ev.new_state
    session.input.set_audio_enabled / set_video_enabled
"""

from __future__ import annotations

import asyncio
import json
import logging

from livekit import rtc
from livekit.agents import (
    AgentSession,
    JobContext,
    RoomInputOptions,
    RoomOutputOptions,
)

from contracts import SupportSession  # noqa: F401  (documented seam shape)
from events import AnswerEvent
from .bridge import BrainLLM, ClutchAgent
from .frames import FrameSampler
from .intents import on_control_data
from .publisher import EventPublisher

logger = logging.getLogger("clutch.realtime.session")

_FORWARDED_STATES = ("listening", "thinking", "speaking", "idle")


def _read_room_context(ctx: JobContext) -> tuple[str, str, str]:
    """Resolve (company_key, modality, user_id) — room metadata (06 token mint) + dispatch metadata."""
    company_key, modality, user_id = "", "type", ""
    meta = getattr(ctx.room, "metadata", "") or ""
    if meta:
        try:
            data = json.loads(meta)
            company_key = data.get("company_key", company_key)
            modality = data.get("modality", modality)
            user_id = data.get("user_id", user_id)
        except ValueError:
            logger.warning("room metadata not JSON: %r", meta)
    # Local participant attributes are a fallback channel for the same keys.
    attrs = getattr(ctx.room.local_participant, "attributes", None) or {}
    company_key = attrs.get("company_key", company_key)
    modality = attrs.get("modality", modality)
    user_id = attrs.get("user_id", user_id)
    # Agent-dispatch metadata carries a stable per-user id ({"user_id": ...}) in some frontends.
    jmeta = getattr(getattr(ctx, "job", None), "metadata", "") or ""
    if jmeta and not user_id:
        try:
            user_id = json.loads(jmeta).get("user_id", user_id)
        except ValueError:
            pass
    return company_key, modality, user_id


async def run_session(
    ctx: JobContext,
    *,
    agent,           # AgentLike  (S2) — held, never introspected
    identify,        # IdentifyFn (S8)
    stt,             # livekit STT plugin (from speech.get_stt)
    tts,             # livekit TTS plugin (from speech.get_tts)
    vad,             # livekit VAD plugin (silero.VAD.load()) — endpointing
    scope_session,   # ScopeFn (S6) — company_key -> SupportSession
    cfg,             # realtime config slice (§7)
    warm_llm=None,   # optional async () -> None: warm the reason gateway/models (latency)
    memory=None,     # optional cross-call memory store: load(key)/save(key, summary)
) -> None:
    # 1. Connect & resolve the company-scoped session.
    import sys as _sys
    print("[run_session] ENTRYPOINT invoked, connecting...", file=_sys.stderr, flush=True)
    await ctx.connect()
    print("[run_session] connected to room", file=_sys.stderr, flush=True)
    # Warm the reason gateway connection + both race models in the background, so the first real
    # turn doesn't pay TLS/connection setup or a cold model. Non-blocking — overlaps session setup.
    if warm_llm is not None:
        try:
            asyncio.ensure_future(warm_llm())
        except RuntimeError:
            pass
    company_key, modality, user_id = _read_room_context(ctx)
    session: SupportSession = scope_session(company_key, modality)
    catalog = getattr(session, "catalog", None) or []
    logger.info("session start company=%s modality=%s", company_key, modality)
    mem_key = f"{company_key or 'demo'}:{user_id or 'default'}"

    # stt/tts/vad may be passed as INSTANCES (stub/harness) or as FACTORIES (production): build them
    # here, inside the job's running loop + http context, so Cartesia's aiohttp websocket session is
    # bound to the live loop (creating them at worker-build time → "Session is closed" on connect).
    if callable(stt):
        stt = stt()
    if callable(tts):
        tts = tts()
    if callable(vad):
        vad = vad()

    # 2. The single writer to the "clutch" data topic.
    pub = EventPublisher(ctx.room)

    # 3. The brain node + its LLM adapter.
    #    `agent` may be a pre-built AgentLike (stateless stub) OR a factory make_agent(session) ->
    #    AgentLike. A stateful ConversationAgent owns ONE SupportSession, so it must be built per
    #    room from the scoped session (one room == one session).
    agent_inst = agent(session) if callable(agent) else agent
    # Wire the live 'Knowledge Matches' panel: the agent fires on_context(query, chunks, ms) after
    # each retrieval → publish a 'moss_context' event the widget renders (sources + scores live).
    if hasattr(agent_inst, "on_context"):
        agent_inst.on_context = lambda query, chunks, ms=None: pub.moss_context(query, chunks, ms)

    # Cross-call memory: SEED the agent's rolling summary from prior calls now (immediate read at
    # session start, before any turn), and PERSIST it on session end (background shutdown callback).
    # Neither touches the per-turn hot path.
    if memory is not None and hasattr(agent_inst, "rolling_summary"):
        try:
            prior = await memory.load(mem_key)
            if prior:
                cur = getattr(agent_inst, "rolling_summary", "") or ""
                agent_inst.rolling_summary = (prior + ((" " + cur) if cur else "")).strip()
                print(f"[memory] loaded prior summary for {mem_key} ({len(prior)} chars)",
                      file=_sys.stderr, flush=True)
        except Exception as exc:
            logger.warning("memory load failed: %r", exc)

        async def _persist_memory() -> None:
            try:
                await memory.save(mem_key, getattr(agent_inst, "rolling_summary", "") or "")
                print(f"[memory] saved summary for {mem_key}", file=_sys.stderr, flush=True)
            except Exception as exc:
                logger.warning("memory save failed: %r", exc)

        try:
            ctx.add_shutdown_callback(_persist_memory)     # fires when the job/room ends
        except Exception:
            pass

    brain = ClutchAgent(agent_inst, pub, latency_badge=getattr(cfg, "latency_badge", True))

    # 4. The LiveKit voice pipeline. The brain is wired as the session LLM: generate_reply (both
    #    spoken STT turns and typed lk.chat turns) requires an LLM model, and BrainLLM routes that
    #    through the agent's on_user_turn — so Type and Talk share one grounded path.
    sess = AgentSession(
        llm=BrainLLM(brain),
        stt=stt,
        tts=tts,
        vad=vad,
        allow_interruptions=cfg.barge_in,
        min_interruption_duration=cfg.min_interruption_ms / 1000,
        min_endpointing_delay=cfg.endpoint_min_ms / 1000,
        max_endpointing_delay=cfg.endpoint_max_ms / 1000,
    )

    # 5. Wire voice-state events — the framework's state machine IS our indicator (§6).
    @sess.on("agent_state_changed")
    def _on_state(ev) -> None:  # noqa: ANN001
        state = ev.new_state  # verified field name in 1.5.17
        if state in _FORWARDED_STATES:
            pub.voice(state)

    # STT visibility + speculative prefetch: log each transcript, and while the user is still speaking
    # (interim transcripts) warm retrieval for the in-progress utterance so the turn's retrieve is
    # already done at endpoint. Throttled by growth so we don't refire on every token.
    _pf = {"len": 0}

    @sess.on("user_input_transcribed")
    def _on_stt(ev) -> None:  # noqa: ANN001
        import sys as _s
        transcript = getattr(ev, "transcript", "") or ""
        is_final = getattr(ev, "is_final", False)
        print(f"[stt] transcript={transcript!r} final={is_final}", file=_s.stderr, flush=True)
        # Speculative: only on interim, only once the utterance has grown enough to be worth a query.
        if (not is_final and len(transcript) >= _pf["len"] + 8 and len(transcript.split()) >= 3
                and hasattr(agent_inst, "prefetch_retrieval")):
            _pf["len"] = len(transcript)
            try:
                asyncio.ensure_future(agent_inst.prefetch_retrieval(transcript))
            except RuntimeError:
                pass
        elif is_final:
            _pf["len"] = 0                          # reset for the next utterance

    # 5b. Re-publish the current voice state when a participant joins — reliable data isn't queued
    #     for a participant whose path isn't established yet, so the single startup 'listening' packet
    #     can be missed in the join window. Re-emit on join so the widget reliably gets a state.
    @ctx.room.on("participant_connected")
    def _on_join(p) -> None:  # noqa: ANN001
        import sys as _s
        pubs = list((getattr(p, "track_publications", None) or {}).values())
        print(f"[track] participant_connected {getattr(p, 'identity', '?')} "
              f"publications={[ (getattr(tp, 'kind', '?'), getattr(tp, 'subscribed', '?')) for tp in pubs ]}",
              file=_s.stderr, flush=True)
        pub.voice(getattr(sess, "agent_state", None) or "listening")

    # DIAGNOSTIC: log every track the browser PUBLISHES (uplink), regardless of kind/subscription.
    @ctx.room.on("track_published")
    def _on_pub(publication, participant) -> None:  # noqa: ANN001
        import sys as _s
        print(f"[track] PUBLISHED kind={getattr(publication, 'kind', '?')} "
              f"source={getattr(publication, 'source', '?')} from={getattr(participant, 'identity', '?')}",
              file=_s.stderr, flush=True)

    # 7a. Modality-escalate control messages on the "clutch" topic (decoded by intents).
    ctx.room.on("data_received", on_control_data(sess))

    # 7b. See path: attach a FrameSampler when a video track is subscribed.
    sampler_tasks: set[asyncio.Task] = set()

    async def _on_see(result) -> None:
        """S8→S2: get the agent's spoken text for this look and SPEAK it via the AgentSession.
        Uses `sess.say` (not Agent.session, which has no activity context in the sampler task)."""
        spoken = await brain.handle_identify(result)   # publishes the card, returns text ("" = silent)
        if spoken:
            try:
                sess.say(spoken)                       # TTS through the live session (works in bg task)
            except Exception as exc:
                logger.warning("see say failed: %r", exc)

    @ctx.room.on("track_subscribed")
    def _on_track(track, publication, participant) -> None:  # noqa: ANN001
        import sys as _s
        print(f"[track] SUBSCRIBED kind={track.kind} from={getattr(participant, 'identity', '?')}",
              file=_s.stderr, flush=True)
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        sampler = FrameSampler(
            identify,
            _on_see,  # routes S8 result → S2 (on_identify) → pub + speak
            fps=cfg.see_fps,
            batch=cfg.see_batch,
            min_interval_ms=cfg.see_min_interval_ms,
            change_distance=getattr(cfg, "see_change_distance", 10),  # pHash gate (only on change)
        )
        # Let the agent grab the freshest frame on a voice turn (visual-question / VQA path).
        if hasattr(agent_inst, "frame_provider"):
            agent_inst.frame_provider = lambda: sampler.latest_frame
        task = asyncio.ensure_future(sampler.run(track, catalog))
        sampler_tasks.add(task)
        task.add_done_callback(sampler_tasks.discard)
        logger.info("FrameSampler attached to video track from %s", getattr(participant, "identity", "?"))

    # 6. Start the pipeline, choosing input/output tracks by modality.
    await sess.start(
        agent=brain,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            audio_enabled=(modality in ("talk", "see")),
            # Vision is OWNED by FrameSampler → Qwen (a separate module). The AgentSession runs a TEXT
            # LLM (BrainLLM) and must NOT ingest video, or audio+video get double-routed through one
            # module. FrameSampler still attaches via the raw track_subscribed handler below.
            video_enabled=False,
            text_enabled=True,  # lk.chat text input always on (Type works in every mode)
        ),
        room_output_options=RoomOutputOptions(
            audio_enabled=(modality != "type"),
            transcription_enabled=True,
        ),
    )

    import sys as _sys2
    print(f"[run_session] session.start complete modality={modality}", file=_sys2.stderr, flush=True)

    # 8. Proactive greeting so the user knows the agent is live — spoken (TTS) + shown as a card.
    greeting = ("Hi! I'm your Clutch support assistant. Point your camera at your device and I'll take "
                "a look, or just tell me what's going on.")
    pub.send(AnswerEvent(text=greeting, citations=[]))
    try:
        await sess.say(greeting)
    except Exception:
        pass
    # The worker holds the job open until the room disconnects; LiveKit drives the loop.
