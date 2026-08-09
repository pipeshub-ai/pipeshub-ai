"""IConversationWriter port (§5.3). The Node-backed adapter mints a scoped JWT
and POSTs to the Node internal route. Keeping it behind a port means a future
in-Python conversation store needs no caller change."""
from __future__ import annotations

from typing import Protocol

from app.services.workflows.domain.models import RunResultMessage


class IConversationWriter(Protocol):
    async def append_result(
        self, conversation_id: str, org_id: str, msg: RunResultMessage
    ) -> None:
        """Append a compact result message to the conversation.
        Must tolerate a 404 (conversation deleted) without raising (edge case §8)."""
        ...

    async def write(
        self,
        *,
        run_id: str,
        org_id: str,
        content: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
        kind: str = "text",
    ) -> None:
        """Append a mid-run message emitted by workflow code (`ctx.emit()`).

        Unlike `append_result` this is not the end of the run, so it must not
        change the conversation's status. Must never raise: a workflow that
        cannot narrate its progress should still finish."""
        ...

    async def list_linked_workflows(self, conversation_id: str, org_id: str) -> list[str]:
        """Ids of workflows attached to a conversation they were not created
        from. The link is conversation-side state, so only the conversation
        store can answer it. Must never raise: an unavailable store degrades
        the listing to the created-from set rather than failing it."""
        ...
