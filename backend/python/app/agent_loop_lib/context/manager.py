from __future__ import annotations

from app.agent_loop_lib.context.base import ContextWindow
from app.agent_loop_lib.core.messages import Message
from app.agent_loop_lib.core.tokens import count_message_tokens


class ContextManager(ContextWindow):
    """Full history, no eviction — for use when external summarization handles trimming."""

    def __init__(self, max_tokens: int = 100_000) -> None:
        self._max_tokens = max_tokens
        self._messages: list[Message] = []

    async def add(self, message: Message) -> None:
        self._messages.append(message)

    async def messages(self) -> list[Message]:
        return list(self._messages)

    async def token_count(self) -> int:
        return sum(count_message_tokens(m) for m in self._messages)

    async def clear(self) -> None:
        self._messages = []
