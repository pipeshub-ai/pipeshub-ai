"""One org's agent names by key, for Node's reports and admin lists (ADR-UI-03).

A report that holds data by agent key names the agents when it is read, never storing a name with its rows. Only an
agent of the token's org is named, deleted ones too (their rows stay on the report); a key that is unknown or another
org's is simply absent, like an agent nobody can name. A list that pins agent versions (schedules) also asks for each
named agent's latest published version, in the same answer, so the page never asks per row.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, Protocol

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of

if TYPE_CHECKING:
    from app.harness.agent_deletion import AgentDocuments

class VersionNumbers(Protocol):
    """The slice of ``AgentVersionStore`` this reads: an agent's published version numbers, ascending."""

    async def numbers(self, org_id: str, agent_key: str) -> list[int]: ...


# Node's batch (`AGENT_NAMES_PER_REQUEST`); a longer list is refused rather than read.
MAX_AGENT_KEYS: Final = 200
MAX_NAME_CHARS: Final = 512
_READS_AT_ONCE: Final = 16


async def agent_names(graph: AgentDocuments, agent_keys: list[str], org_id: str) -> dict[str, str]:
    """``{key: name}`` for each key that is an agent of ``org_id``. Agents carry no org of their own; the creator's
    org stands for it, as in the agent routes. A graph failure raises, so Node shows the agents unnamed."""
    keys = list(dict.fromkeys(key for key in agent_keys if key))
    gate = asyncio.Semaphore(_READS_AT_ONCE)
    creators: dict[str, str | None] = {}

    async def creator_org(user_key: str) -> str | None:
        if user_key not in creators:
            user = await graph.get_document(user_key, CollectionNames.USERS.value, raise_on_error=True)
            creators[user_key] = (org_of(user) or "").strip() or None
        return creators[user_key]

    async def name_of(key: str) -> tuple[str, str | None]:
        async with gate:
            agent = await graph.get_document(key, CollectionNames.AGENT_INSTANCES.value, raise_on_error=True)
            if agent is None:
                return key, None
            creator = str(agent.get("createdBy") or "").strip()
            if not creator or await creator_org(creator) != org_id:
                return key, None
            name = agent.get("name")
            if not isinstance(name, str) or not name.strip():
                return key, None
            return key, name.strip()[:MAX_NAME_CHARS]

    named = await asyncio.gather(*(name_of(key) for key in keys))
    return {key: name for key, name in named if name is not None}


async def latest_versions(versions: VersionNumbers, agent_keys: list[str], org_id: str) -> dict[str, int]:
    """``{key: n}``, each key's latest published version in ``org_id``; a key with none yet is absent. Only reads: the
    migration that gives an agent its version 1 runs on the agent's own reads, never on an admin list's. A store
    failure raises, so Node shows the list without versions."""
    keys = list(dict.fromkeys(key for key in agent_keys if key))
    gate = asyncio.Semaphore(_READS_AT_ONCE)

    async def latest_of(key: str) -> tuple[str, int | None]:
        async with gate:
            numbers = await versions.numbers(org_id, key)
            return key, numbers[-1] if numbers else None

    found = await asyncio.gather(*(latest_of(key) for key in keys))
    return {key: number for key, number in found if number is not None}


__all__ = ["MAX_AGENT_KEYS", "VersionNumbers", "agent_names", "latest_versions"]
