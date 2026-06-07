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

from src.contracts import SupportSession  # noqa: F401  (documented seam shape)
from src.events import AnswerEvent
from .bridge import BrainLLM, ClutchAgent
from .frames import FrameSampler
from .intents import on_control_data
from .publisher import EventPublisher

logger = logging.getLogger("clutch.realtime.session")

_FORWARDED_STATES = ("listening", "thinking", "speaking", "idle")


async def _read_room_context(ctx: JobContext) -> tuple[str, str]:
    """Resolve (company_key, modality) from room metadata or participant attributes."""
    company_key, modality = "", "type"
    meta = getattr(ctx.room, "metadata", "") or ""
    if meta:
        try:
            data = json.loads(meta)
            company_key = data.get("company_key", company_key)
            modality = data.get("modality", modality)
        except ValueError:
            logger.warning("room metadata not JSON: %r", meta)
            
    import asyncio
    # The agent is local_participant. The user is a remote_participant.
    # We may need to wait slightly for the participant to be fully populated after connect.
    for _ in range(20):
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", None) or {}
            if attrs.get("company_key"):
                company_key = attrs["company_key"]
            if attrs.get("modality"):
                modality = attrs["modality"]
                
            pmeta = getattr(p, "metadata", "") or ""
            if pmeta:
                try:
                    data = json.loads(pmeta)
                    if "company_key" in data: company_key = data["company_key"]
                    if "modality" in data: modality = data["modality"]
                except ValueError:
                    pass
            if company_key and modality != "type":
                return company_key, modality
        if company_key and modality != "type":
            break
        await asyncio.sleep(0.1)

    return company_key, modality


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
) -> None:
    # 1. Connect & resolve the company-scoped session.
    import sys as _sys
    print("[run_session] ENTRYPOINT invoked, connecting...", file=_sys.stderr, flush=True)
    await ctx.connect()
    print("[run_session] connected to room", file=_sys.stderr, flush=True)
    company_key, modality = await _read_room_context(ctx)
    session: SupportSession = scope_session(company_key, modality)
    catalog = getattr(session, "catalog", None) or []
    logger.info("session start company=%s modality=%s", company_key, modality)

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
    brain = ClutchAgent(agent_inst, pub, latency_badge=getattr(cfg, "latency_badge", True))

    # 4. The LiveKit voice pipeline. The brain is wired as the session LLM: generate_reply (both
    #    spoken STT turns and typed lk.chat turns) requires an LLM model, and BrainLLM routes that
    #    through the agent's on_user_turn — so Type and Talk share one grounded path.
    sess = AgentSession(
        llm=BrainLLM(brain),
        stt=stt,
        tts=tts,
        vad=vad,
        allow_interruptions=getattr(cfg, "barge_in", True),
        min_interruption_duration=getattr(cfg, "min_interruption_ms", 1000) / 1000,
        min_endpointing_delay=getattr(cfg, "endpoint_min_ms", 500) / 1000,
        max_endpointing_delay=getattr(cfg, "endpoint_max_ms", 3000) / 1000,
    )

    # 5. Wire voice-state events — the framework's state machine IS our indicator (§6).
    @sess.on("agent_state_changed")
    def _on_state(ev) -> None:  # noqa: ANN001
        state = ev.new_state  # verified field name in 1.5.17
        if state in _FORWARDED_STATES:
            pub.voice(state)

    # STT visibility (diagnostic): log each transcript so we can confirm Cartesia is hearing the mic.
    @sess.on("user_input_transcribed")
    def _on_stt(ev) -> None:  # noqa: ANN001
        import sys as _s
        print(f"[stt] transcript={getattr(ev, 'transcript', '')!r} final={getattr(ev, 'is_final', '?')}",
              file=_s.stderr, flush=True)

    # 5b. Re-publish the current voice state when a participant joins — reliable data isn't queued
    #     for a participant whose path isn't established yet, so the single startup 'listening' packet
    #     can be missed in the join window. Re-emit on join so the widget reliably gets a state.
    @ctx.room.on("participant_connected")
    def _on_join(p) -> None:  # noqa: ANN001
        pub.voice(getattr(sess, "agent_state", None) or "listening")

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

    # DIAGNOSTIC: log every track the browser PUBLISHES (uplink), regardless of kind/subscription.
    @ctx.room.on("track_published")
    def _on_track_published(publication, participant) -> None:
        import sys as _s
        print(f"[track] PUBLISHED kind={getattr(publication, 'kind', '?')} "
              f"source={getattr(publication, 'source', '?')} from={getattr(participant, 'identity', '?')}",
              file=_s.stderr, flush=True)

    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant) -> None:
        import sys as _s
        print(f"[track] SUBSCRIBED kind={track.kind} from={getattr(participant, 'identity', '?')}",
              file=_s.stderr, flush=True)
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        sampler = FrameSampler(
            identify,
            _on_see,  # routes S8 result → S2 (on_identify) → pub + speak
            fps=getattr(cfg, "see_fps", 1.5),
            batch=getattr(cfg, "see_batch", 2),
            min_interval_ms=getattr(cfg, "see_min_interval_ms", 1500),
            change_distance=getattr(cfg, "see_change_distance", 10),
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
            # module. FrameSampler still attaches via the raw track_subscribed handler above.
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
