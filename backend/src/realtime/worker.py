"""build_worker — bind the cross-layer deps and produce a runnable WorkerOptions.

The entrypoint LiveKit calls takes only ``(ctx)``, so the cross-layer deps are bound onto
``run_session`` via ``functools.partial`` — keeping ``run_session`` itself dependency-injected
and testable. ``cli.run_app(WorkerOptions(...))`` runs the worker against the LiveKit server
(creds from cfg / env per LLD §13).

Verified against livekit-agents 1.5.17:
    WorkerOptions(entrypoint_fnc=…, ws_url=…, api_key=…, api_secret=…)
    cli.run_app(WorkerOptions)
"""

from __future__ import annotations

import functools

from livekit.agents import WorkerOptions, cli

from .session import run_session


def build_worker(
    entrypoint=run_session,
    *,
    agent,
    identify,
    stt,
    tts,
    vad,
    scope_session,
    cfg,
    agent_name: str | None = None,
) -> WorkerOptions:
    """Bind deps onto the entrypoint and return WorkerOptions ready for cli.run_app.

    `agent_name`: leave None for **automatic dispatch** (production — the worker is assigned to every
    new room). Set it to use **explicit dispatch** (the caller must create an AgentDispatch for the
    room) — used by the test harness to make dispatch deterministic instead of timing-dependent.
    """
    bound = functools.partial(
        entrypoint,
        agent=agent,
        identify=identify,
        stt=stt,
        tts=tts,
        vad=vad,
        scope_session=scope_session,
        cfg=cfg,
    )

    kwargs = dict(
        entrypoint_fnc=bound,
        ws_url=cfg.livekit_url,
        api_key=cfg.livekit_api_key,
        api_secret=cfg.livekit_api_secret,
    )
    if agent_name:
        kwargs["agent_name"] = agent_name  # explicit-dispatch mode (disables auto-dispatch)
    return WorkerOptions(**kwargs)


def run_worker(opts: WorkerOptions) -> None:
    """Blocking helper — run the worker (the dev/prod entry, not used by --check)."""
    cli.run_app(opts)
