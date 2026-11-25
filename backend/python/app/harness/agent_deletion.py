"""Whether an agent's delete committed, for Node's agent-erasure sweeper (H5).

Node writes an erasure intent before it asks this service to delete an agent; an intent whose request was
interrupted is confirmed here before Node erases the agent's memories. Only the agent's tombstone confirms it:
an agent that is live, unknown, or another org's is never reported deleted.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of

if TYPE_CHECKING:
    from collections.abc import Mapping


class AgentDocuments(Protocol):
    """The slice of ``IGraphDBProvider`` this reads: documents by key, tombstones included."""

    async def get_document(
        self, document_key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False
    ) -> Mapping[str, Any] | None: ...


class AgentDeletionState(str, Enum):
    DELETED = "deleted"
    LIVE = "live"
    ABSENT = "absent"


def deletion_state(
    agent: Mapping[str, Any] | None, creator: Mapping[str, Any] | None, org_id: str
) -> AgentDeletionState:
    """The agent's state as ``org_id`` may learn it: an agent outside the org reads as absent, like an unknown key.

    Agents carry no org of their own; the creator's org stands for it, as in the agent routes.
    """
    if agent is None or creator is None or not org_id or (org_of(creator) or "").strip() != org_id:
        return AgentDeletionState.ABSENT
    return AgentDeletionState.DELETED if agent.get("isDeleted") is True else AgentDeletionState.LIVE


async def agent_deletion_state(graph: AgentDocuments, agent_id: str, org_id: str) -> AgentDeletionState:
    """Reads the agent document, tombstones included, and its creator; a graph failure raises (never "absent")."""
    agent = await graph.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value, raise_on_error=True)
    creator_key = (agent or {}).get("createdBy")
    creator = (
        await graph.get_document(str(creator_key), CollectionNames.USERS.value, raise_on_error=True)
        if creator_key
        else None
    )
    return deletion_state(agent, creator, org_id)


__all__ = ["AgentDeletionState", "AgentDocuments", "agent_deletion_state", "deletion_state"]
