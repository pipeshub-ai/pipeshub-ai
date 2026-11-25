"""The built-in assistant's `/chat/stream` modes, run on the agent loop.

`setup.py` builds a turn (the mode's tools, prefetch and instructions);
`policy.py` explains why no `ToolPolicy`/`factory.py`/`tool_loader.py`
changes were needed. The legacy stream is `stream_bridge.run_chat_stream`;
a durable run assembles the same turn in `app.harness.assembly`.
"""

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
]
