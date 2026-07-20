from __future__ import annotations

from app.agent_loop_lib.context.base import ContextWindow
from app.agent_loop_lib.core.messages import Message, MessageRole
from app.agent_loop_lib.core.tokens import count_message_tokens


class SlidingWindowContext(ContextWindow):
    """Drops oldest messages when token limit is exceeded."""

    def __init__(self, max_tokens: int = 100_000) -> None:
        self._max_tokens = max_tokens
        self._messages: list[Message] = []

    async def add(self, message: Message) -> None:
        self._messages.append(message)
        await self._evict()

    async def _evict(self) -> None:
        while await self.token_count() > self._max_tokens:
            # Find the index of the first non-SYSTEM message
            evicted = False
            for i, msg in enumerate(self._messages):
                if msg.role != MessageRole.SYSTEM:
                    self._messages.pop(i)
                    evicted = True
                    break
            if not evicted:
                # Only system messages remain — cannot evict further
                break

    async def messages(self) -> list[Message]:
        return list(self._messages)

    async def token_count(self) -> int:
        return sum(count_message_tokens(m) for m in self._messages)

    async def clear(self) -> None:
        self._messages = [m for m in self._messages if m.role == MessageRole.SYSTEM]
