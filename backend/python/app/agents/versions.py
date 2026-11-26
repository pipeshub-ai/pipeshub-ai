"""Immutable published versions of an agent (OWD-14, ADR-OWD-14).

The agent document and its edges are the draft, edited in place as before. Publishing freezes the
draft's definition into ``agentVersions``: one document per version, numbered 1, 2, 3… per agent,
created once (``ensure_nodes``) and never written again. Rollback publishes an older version's
definition again as the next number, so history only grows and a recorded version always names what
ran. The rollback route also rewrites the draft to the version it rolls back to,
so a later save cannot bring discarded edits back. Runs and schedules pin a number (Node pins it as ``x.pipeshub.agent.definitionVersion``); a chat
runs the latest. An agent with no version yet gets version 1 from its current definition (GA-01): from
the startup migration, or on its first read here, whichever comes first.

Only the backend-agnostic graph surface is used (``ensure_nodes``, ``get_document``,
``get_nodes_by_filters``, ``get_documents_paginated``, ``delete_nodes``), so it runs unchanged on
Neo4j and ArangoDB.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_VERSIONS = CollectionNames.AGENT_VERSIONS.value

# Who owns, shares and administers the agent, and the graph's bookkeeping: always read from the live
# agent, never frozen into a version, so publishing or rolling back can neither undo a deletion nor
# hand an agent back to a former owner or sharing.
AGENT_IDENTITY_FIELDS = frozenset({
    "_key", "_id", "_rev", "id", "orgId",
    "createdBy", "updatedBy", "createdAtTimestamp", "updatedAtTimestamp",
    "deletedAtTimestamp", "deletedByUserId", "isDeleted", "isActive",
    "isServiceAccount", "shareWithOrg", "ownerTeam",
    # A read-failure marker of get_agent, not part of what the agent does.
    "mcpServersUnavailable",
})

# The graph's bookkeeping on the linked toolsets, tools, servers and knowledge: a save (or a rollback)
# re-creates a node with the same content under a new key, time and writer, which is not a change of definition.
_NODE_ID_FIELDS = frozenset({
    "_key", "_id", "_rev", "id", "createdAtTimestamp", "updatedAtTimestamp", "createdBy", "updatedBy", "userId",
})

NOTE_MAX_CHARS = 500
# Publishers racing for one number: each loser takes the next. Bounded so a store that never keeps a
# write cannot spin.
MAX_PUBLISH_ATTEMPTS = 8


class VersionSource(StrEnum):
    PUBLISH = "publish"
    ROLLBACK = "rollback"
    MIGRATION = "migration"


class AgentVersionConflict(RuntimeError):
    """No number could be claimed in ``MAX_PUBLISH_ATTEMPTS`` tries: the caller may retry."""


@dataclass(frozen=True, slots=True)
class AgentVersion:
    agent_key: str
    version: int
    digest: str
    definition: dict[str, Any]
    source: VersionSource
    published_by: str | None
    published_at: int
    rolled_back_from: int | None
    note: str | None
    publish_id: str

    def summary(self) -> dict[str, Any]:
        """The version as a list shows it: never its definition (its prompts)."""
        return {
            "agentKey": self.agent_key,
            "version": self.version,
            "digest": self.digest,
            "source": self.source.value,
            "publishedBy": self.published_by,
            "publishedAtTimestamp": self.published_at,
            "rolledBackFrom": self.rolled_back_from,
            "note": self.note,
        }

    def wire(self) -> dict[str, Any]:
        return {**self.summary(), "definition": copy.deepcopy(self.definition)}


def agent_definition(agent: Mapping[str, Any]) -> dict[str, Any]:
    """What a version freezes of a ``get_agent`` document: everything but its identity."""
    return {key: copy.deepcopy(value) for key, value in agent.items() if key not in AGENT_IDENTITY_FIELDS}


def _without_node_ids(value: object) -> object:
    if isinstance(value, dict):
        entries = cast("dict[str, object]", value)
        return {k: _without_node_ids(v) for k, v in entries.items() if k not in _NODE_ID_FIELDS}
    if isinstance(value, list):
        return [_without_node_ids(v) for v in cast("list[object]", value)]
    return value


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def definition_digest(definition: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(_without_node_ids(dict(definition))).encode()).hexdigest()


def pinned_agent(live: Mapping[str, Any], version: AgentVersion) -> dict[str, Any]:
    """The agent as a run of ``version`` sees it: the live agent's identity, the version's definition.
    A field the draft gained since is not carried over: only the version says what the agent does."""
    identity = {key: value for key, value in live.items() if key in AGENT_IDENTITY_FIELDS}
    return {**copy.deepcopy(identity), **copy.deepcopy(version.definition)}


def version_key(agent_key: str, version: int) -> str:
    return f"{agent_key}-v{version}"


def _note(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()[:NOTE_MAX_CHARS]


def version_document(
    org_id: str,
    agent_key: str,
    version: int,
    agent: Mapping[str, Any],
    *,
    published_by: str | None,
    source: VersionSource = VersionSource.PUBLISH,
    rolled_back_from: int | None = None,
    note: object = None,
    publish_id: str | None = None,
) -> dict[str, Any]:
    """The stored document of ``agent``'s definition as ``version``."""
    definition = agent_definition(agent)
    return {
        "id": version_key(agent_key, version),
        "orgId": org_id,
        "agentKey": agent_key,
        "version": version,
        "definition": _canonical(definition),
        "digest": definition_digest(definition),
        "source": source.value,
        "rolledBackFrom": rolled_back_from,
        "note": _note(note),
        "publishedBy": published_by,
        "publishedAtTimestamp": get_epoch_timestamp_in_ms(),
        "publishId": publish_id or uuid.uuid4().hex,
    }


def _version_of(doc: Mapping[str, Any]) -> AgentVersion | None:
    try:
        definition: object = json.loads(doc["definition"])
        number = doc["version"]
        if not isinstance(definition, dict) or isinstance(number, bool) or not isinstance(number, int):
            return None
        rolled = doc.get("rolledBackFrom")
        return AgentVersion(
            agent_key=str(doc["agentKey"]),
            version=number,
            digest=str(doc["digest"]),
            definition=cast("dict[str, Any]", definition),
            source=VersionSource(doc.get("source") or VersionSource.PUBLISH.value),
            published_by=doc.get("publishedBy") if isinstance(doc.get("publishedBy"), str) else None,
            published_at=int(doc.get("publishedAtTimestamp") or 0),
            rolled_back_from=rolled if isinstance(rolled, int) and not isinstance(rolled, bool) else None,
            note=doc.get("note") if isinstance(doc.get("note"), str) else None,
            publish_id=str(doc.get("publishId") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


class AgentVersionStore:
    """An org's agent versions. Every read is checked against the org: a key alone never reads another's."""

    def __init__(self, graph: IGraphDBProvider) -> None:
        self._graph = graph

    async def _docs(self, org_id: str, agent_key: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
        docs: object = await self._graph.get_nodes_by_filters(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType] - the provider types documents loosely
            _VERSIONS, {"orgId": org_id, "agentKey": agent_key}, return_fields=fields, raise_on_error=True,
        )
        return cast("list[dict[str, Any]]", docs)

    async def numbers(self, org_id: str, agent_key: str) -> list[int]:
        numbers = [d.get("version") for d in await self._docs(org_id, agent_key, ["version"])]
        return sorted(n for n in numbers if isinstance(n, int) and not isinstance(n, bool) and n > 0)

    async def get(self, org_id: str, agent_key: str, version: int) -> AgentVersion | None:
        doc = await self._graph.get_document(version_key(agent_key, version), _VERSIONS, raise_on_error=True)
        if doc is None or org_of(doc) != org_id or doc.get("agentKey") != agent_key:
            return None
        return _version_of(doc)

    async def latest(self, org_id: str, agent_key: str) -> AgentVersion | None:
        numbers = await self.numbers(org_id, agent_key)
        return await self.get(org_id, agent_key, numbers[-1]) if numbers else None

    async def history(self, org_id: str, agent_key: str) -> list[AgentVersion]:
        """Every version, newest first."""
        versions = [v for d in await self._docs(org_id, agent_key) if (v := _version_of(d)) is not None]
        return sorted(versions, key=lambda v: v.version, reverse=True)

    async def publish(
        self,
        org_id: str,
        agent_key: str,
        agent: Mapping[str, Any],
        *,
        published_by: str | None,
        note: object = None,
        source: VersionSource = VersionSource.PUBLISH,
        rolled_back_from: int | None = None,
    ) -> tuple[AgentVersion, bool]:
        """Freezes ``agent``'s definition as the next version; ``(version, created)``. A definition equal to the
        latest version's publishes nothing and answers that version. A migration only ever makes version 1."""
        digest = definition_digest(agent_definition(agent))
        for _ in range(MAX_PUBLISH_ATTEMPTS):
            latest = await self.latest(org_id, agent_key)
            if latest is not None and (latest.digest == digest or source is VersionSource.MIGRATION):
                return latest, False
            number = 1 if latest is None else latest.version + 1
            doc = version_document(
                org_id, agent_key, number, agent,
                published_by=published_by, source=source, rolled_back_from=rolled_back_from, note=note,
            )
            await self._graph.ensure_nodes([doc], _VERSIONS)
            stored = await self.get(org_id, agent_key, number)
            if stored is not None and stored.publish_id == doc["publishId"]:
                return stored, True
        raise AgentVersionConflict(f"no version number could be claimed for agent {agent_key}")

    async def current(self, org_id: str, agent_key: str, live: Mapping[str, Any]) -> AgentVersion:
        """The latest version; an agent that has none gets version 1 from ``live`` first (GA-01)."""
        latest = await self.latest(org_id, agent_key)
        if latest is not None:
            return latest
        version, _ = await self.publish(org_id, agent_key, live, published_by=None, source=VersionSource.MIGRATION)
        return version

    async def erase_org(self, org_id: str, *, page: int) -> tuple[int, bool]:
        """Up to one page of an erased org's versions (CLEANUP-G1). ``(erased, remaining)``."""
        docs = await self._graph.get_documents_paginated(
            _VERSIONS, skip=0, limit=page + 1, filters={"orgId": org_id}, sort_field="_key", raise_on_error=True,
        )
        keys = [k for d in docs[:page] if (k := d.get("id") or d.get("_key"))]
        if keys:
            _ = await self._graph.delete_nodes(keys, _VERSIONS)
        return len(keys), len(docs) > page


__all__ = [
    "AGENT_IDENTITY_FIELDS",
    "AgentVersion",
    "AgentVersionConflict",
    "AgentVersionStore",
    "VersionSource",
    "agent_definition",
    "definition_digest",
    "pinned_agent",
    "version_document",
    "version_key",
]
