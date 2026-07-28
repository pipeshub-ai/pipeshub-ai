"""Agent-loop-backed implementation of the three `/chat/stream` modes.

See `bridge.py`'s module docstring for the full design; `policy.py` for
why no `ToolPolicy`/`factory.py`/`tool_loader.py` changes were needed.
"""

from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import (
    AgentCapabilities,
    ChatModePolicy,
    resolve_agent_policy,
    resolve_chat_mode_policy,
)

__all__ = [
    "AgentCapabilities",
    "ChatModePolicy",
    "resolve_agent_policy",
    "resolve_chat_mode_policy",
    "run_chat_stream",
]
