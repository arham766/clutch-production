"""Agent: the multi-modal conversation orchestrator (HLD 04).

Public surface (seam S2):
    ConversationAgent(session, retrieve, compose, *, escalate, catalog)
        .on_user_turn(text) -> Answer | None
        .on_identify(IdentifyResult) -> Answer | None
"""

from src.agent.agent import ConversationAgent

__all__ = ["ConversationAgent"]
