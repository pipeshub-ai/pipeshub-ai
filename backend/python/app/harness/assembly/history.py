"""The legacy history a durable session imported (``session.imported``, P1b T3).

Node journals the conversation's legacy turns as the ``previousConversations``
entries the legacy route sends Python. A durable run gives the model the
messages legacy seeds a request with for them (``history_messages``): the same
conversion of each turn, its attachments resolved from storage, their
citation refs in the run's mapper, so the model sees the history exactly as
legacy would have given it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.kernel.core.messages import Message
    from harness_core.logs import HarnessLog


class LegacyHistory:
    def __init__(self, env: ToolEnvironment, log: HarnessLog) -> None:
        self._env = env
        self._log = log

    async def messages(self, transcript: bytes) -> list[Message]:
        from app.agents.agent_loop.factory import history_messages

        turns: list[dict[str, Any]] = [t for t in json.loads(transcript) if isinstance(t, dict)]
        messages = await history_messages(turns, self._env.context)
        self._log.info(
            "history.rendered",
            entries=len(turns),
            messages=len(messages),
            attachments=sum(len(t.get("attachments") or []) for t in turns),
        )
        return messages


__all__ = ["LegacyHistory"]
