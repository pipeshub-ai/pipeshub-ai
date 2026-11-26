"""One org's capability configuration as today's stores hold it, for the control plane's migration (CPD-7).

The control plane plans installations and grants from these facts (Node,
``application/capability-plan.ts``); this module only reads. It reads:

* toolset instances from the shared KV list, and each member's personal ones (one key each, main #4324),
  marked with their owner;
* MCP instances through SEC5's org-scoped lookup, with their approved manifests;
* a page of agents, each with its toolset, MCP and skill edges (``IGraphDBProvider``).

An agent belongs to the org of its creator, as ``agent_deletion`` decides it. Platform toolsets attached to
an agent are left out: they are not catalog items, and a run gets them from its other fields (KN2). No secret
is read into the facts: instances carry no auth configuration here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from app.agents.constants.mcp_server_constants import get_mcp_instances_prefix
from app.agents.constants.toolset_constants import (
    get_toolset_org_user_instances_prefix,
    get_toolset_user_instance_path,
)
from app.agents.toolset_store import is_personal
from app.config.constants.arangodb import CollectionNames
from app.control_plane.contracts.generated.legacy_capabilities import LegacyCapabilities
from app.core.org_context import org_of
from harness_core.mcp_hub.annotations import declared_hints, hint_tags
from harness_core.tools.tags import EFFECT_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

TOOLSET_INSTANCES_PATH = "/services/toolset-instances"
MCP_INSTANCES_PREFIX = get_mcp_instances_prefix()
AGENT_PAGE = 200
Document = dict[str, Any]

_AGENTS = CollectionNames.AGENT_INSTANCES.value
_USERS = CollectionNames.USERS.value
_TOOLSETS = CollectionNames.AGENT_TOOLSETS.value
_MCP_SERVERS = CollectionNames.AGENT_MCP_SERVERS.value
_TOOLS = CollectionNames.AGENT_TOOLS.value
_SKILLS = CollectionNames.AGENT_SKILLS.value


class _Config(Protocol):
    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False  # noqa: FBT001, FBT002 - ConfigurationService's signature
    ) -> object: ...

    async def list_keys_in_directory(self, directory: str) -> list[str]: ...


class _Graph(Protocol):
    async def get_documents_paginated(
        self, collection: str, skip: int = 0, limit: int = 50, *, sort_field: str | None = None, raise_on_error: bool = False
    ) -> list[Document]: ...

    async def get_edges_from_node(self, node_id: str, edge_collection: str) -> list[Document]: ...

    async def get_document(self, document_key: str, collection: str, *, raise_on_error: bool = False) -> Document | None: ...


def _text(document: Document | None, key: str) -> str:
    value = (document or {}).get(key)
    return value if isinstance(value, str) else ""


def _millis(document: Document, *keys: str) -> int:
    for key in keys:
        value = document.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def _target_key(edge: Document) -> str:
    return (_text(edge, "_to") or _text(edge, "to_id")).rsplit("/", 1)[-1]


def _approved_tools(manifest: Document) -> dict[str, Document]:
    """An approval document's tools as their digest and effect; an entry without a digest approves nothing."""
    tools = cast("dict[str, object]", manifest.get("tools") or {})
    approved: dict[str, Document] = {}
    for name, raw in tools.items():
        entry = cast("Document", raw) if isinstance(raw, dict) else {}
        if digest := _text(entry, "digest"):
            approved[name] = {"digest": digest, "effect": _effect(entry)}
    return approved


def _effect(entry: object) -> str | None:
    annotations = cast("dict[str, Any]", entry).get("annotations") if isinstance(entry, dict) else None
    hints = declared_hints(annotations)
    if not hints:
        return None
    return next((tag.value for tag in hint_tags(hints) if tag.key == EFFECT_TAG_KEY), None)


class LegacyCapabilityReader:
    """Reads one org's facts; another org's instances and agents are never in them."""

    def __init__(
        self,
        graph: IGraphDBProvider,
        config: ConfigurationService,
        *,
        provider_of: Callable[[str], str | None],
        mcp_instances: Callable[[ConfigurationService, str], Awaitable[list[dict[str, Any]]]],
        read_manifest: Callable[[ConfigurationService, str], Awaitable[dict[str, Any] | None]],
        skill_digest: Callable[[str, str, str], Awaitable[str | None]],
    ) -> None:
        self._graph = cast("_Graph", graph)
        self._config = cast("_Config", config)
        self._config_service = config
        self._provider_of = provider_of
        self._mcp_instances = mcp_instances
        self._read_manifest = read_manifest
        self._skill_digest = skill_digest
        self._users: dict[str, Document | None] = {}

    async def read(
        self, org_id: str, *, after: str | None = None, agent_key: str | None = None, agents: bool = True
    ) -> LegacyCapabilities:
        first = after is None
        toolsets = await self._toolset_instances(org_id) if first else []
        mcp = await self._mcp(org_id) if first else []
        page: list[Document] = []
        next_agent: str | None = None
        if agents and agent_key is not None:
            page = [a for a in [await self._agent(org_id, agent_key)] if a is not None]
        elif agents:
            page, next_agent = await self._agent_page(org_id, int(after or 0))
        return LegacyCapabilities.model_validate(
            {
                "schemaVersion": 1,
                "orgId": org_id,
                "toolsetInstances": toolsets,
                "mcpInstances": mcp,
                "agents": page,
                "nextAgent": next_agent,
            }
        )

    async def _toolset_instances(self, org_id: str) -> list[Document]:
        stored = await self._config.get_config(TOOLSET_INSTANCES_PATH, default=[], use_cache=False, raise_on_error=True)
        rows = [cast("Document", i) for i in cast("list[object]", stored or []) if isinstance(i, dict)]
        org = [
            self._toolset(i)
            for i in rows
            if org_of(i) == org_id and _text(i, "_id") and _text(i, "toolsetType")
        ]
        return org + await self._personal_toolset_instances(org_id)

    async def _personal_toolset_instances(self, org_id: str) -> list[Document]:
        """Every member's personal instances, each its owner's. A store that cannot list fails the read: a sync
        reading none would mark every personal installation removed."""
        found: list[Document] = []
        for key in sorted(await self._config.list_keys_in_directory(get_toolset_org_user_instances_prefix(org_id))):
            record = await self._config.get_config(key, default=None, use_cache=False, raise_on_error=True)
            if not isinstance(record, dict):
                continue
            instance = cast("Document", record)
            owner = _text(instance, "createdBy")
            # The key names the owner; a record that names another (or none) is not trusted to be theirs.
            if (
                is_personal(instance)
                and org_of(instance) == org_id
                and owner
                and key == get_toolset_user_instance_path(org_id, owner, _text(instance, "_id"))
                and _text(instance, "toolsetType")
            ):
                found.append({**self._toolset(instance), "ownerId": owner})
        return found

    @staticmethod
    def _toolset(instance: Document) -> Document:
        return {
            "instanceId": _text(instance, "_id"),
            "instanceName": _text(instance, "instanceName")[:256],
            "toolsetType": _text(instance, "toolsetType"),
            "authType": _text(instance, "authType"),
            "createdBy": _text(instance, "createdBy") or None,
            "updatedAt": _millis(instance, "updatedAtTimestamp", "createdAtTimestamp"),
        }

    async def _mcp(self, org_id: str) -> list[Document]:
        # The scoped lookup answers [] when the store cannot list; a sync would then read every MCP
        # installation as removed. Listing here first makes an unreadable store a failed read instead.
        await self._config.list_keys_in_directory(MCP_INSTANCES_PREFIX)
        found: list[Document] = []
        for instance in await self._mcp_instances(self._config_service, org_id):
            instance_id = _text(instance, "_id")
            if not instance_id:
                continue
            manifest = await self._read_manifest(self._config_service, instance_id)
            found.append(
                {
                    "instanceId": instance_id,
                    "name": _text(instance, "name")[:256],
                    "typeId": _text(instance, "typeId") or None,
                    "authMode": _text(instance, "authMode"),
                    "createdBy": _text(instance, "createdBy") or None,
                    "updatedAt": _millis(instance, "updatedAt", "createdAt"),
                    "manifest": None
                    if manifest is None
                    else {
                        "tools": _approved_tools(manifest),
                        "approvedBy": _text(manifest, "approvedBy") or None,
                        "approvedAt": _millis(manifest, "approvedAt"),
                    },
                }
            )
        return found

    async def _agent_page(self, org_id: str, skip: int) -> tuple[list[Document], str | None]:
        documents = await self._graph.get_documents_paginated(
            _AGENTS, skip=skip, limit=AGENT_PAGE, sort_field="_key", raise_on_error=True
        )
        page = [await self._facts(document) for document in documents if await self._in_org(document, org_id)]
        return page, (str(skip + len(documents)) if len(documents) == AGENT_PAGE else None)

    async def _agent(self, org_id: str, agent_key: str) -> Document | None:
        document = await self._graph.get_document(agent_key, _AGENTS, raise_on_error=True)
        if document is None or not await self._in_org(document, org_id):
            return None
        return await self._facts(document)

    async def _user(self, key: str) -> Document | None:
        if key not in self._users:
            self._users[key] = await self._graph.get_document(key, _USERS, raise_on_error=True) if key else None
        return self._users[key]

    async def _person(self, key: str) -> str | None:
        """The platform user id (Node's actor id) of a graph user key; an id that is no graph key is one already.

        The agent routes stamp ``createdBy`` with the graph key, the runtime with the user id (CPS SK-11);
        installations and grants name people the way runs do, by user id.
        """
        if not key:
            return None
        user = await self._user(key)
        return _text(user, "userId") or key

    async def _in_org(self, agent: Document, org_id: str) -> bool:
        if agent.get("isDeleted"):
            return False
        return _text(await self._user(_text(agent, "createdBy")), "orgId") == org_id

    async def _facts(self, agent: Document) -> Document:
        key = _text(agent, "_key") or _text(agent, "id")
        node = f"{_AGENTS}/{key}"
        toolsets: list[Document] = []
        for edge in await self._graph.get_edges_from_node(node, CollectionNames.AGENT_HAS_TOOLSET.value):
            toolset = await self._graph.get_document(_target_key(edge), _TOOLSETS, raise_on_error=True)
            if toolset is None:
                continue
            kind = _text(toolset, "type") or _text(toolset, "name")
            if self._provider_of(kind) == "platform":
                continue
            toolsets.append(
                {
                    "nodeKey": _text(toolset, "_key"),
                    "instanceId": _text(toolset, "instanceId") or None,
                    "toolsetType": kind,
                    "tools": await self._selected(f"{_TOOLSETS}/{_text(toolset, '_key')}", CollectionNames.TOOLSET_HAS_TOOL.value),
                }
            )
        servers: list[Document] = []
        for edge in await self._graph.get_edges_from_node(node, CollectionNames.AGENT_HAS_MCP_SERVER.value):
            server = await self._graph.get_document(_target_key(edge), _MCP_SERVERS, raise_on_error=True)
            if server is None or not _text(server, "instanceId"):
                continue
            servers.append(
                {
                    "instanceId": _text(server, "instanceId"),
                    "tools": await self._selected(f"{_MCP_SERVERS}/{_text(server, '_key')}", CollectionNames.MCP_SERVER_HAS_TOOL.value),
                }
            )
        return {
            "agentKey": key,
            "createdBy": await self._person(_text(agent, "createdBy")),
            "toolsets": toolsets,
            "mcpServers": servers,
            "skills": await self._skills(node),
        }

    async def _selected(self, owner: str, edge_collection: str) -> list[str] | None:
        names: list[str] = []
        for edge in await self._graph.get_edges_from_node(owner, edge_collection):
            tool = await self._graph.get_document(_target_key(edge), _TOOLS, raise_on_error=True)
            if tool is not None and _text(tool, "name"):
                names.append(_text(tool, "name"))
        return sorted(set(names)) or None

    async def _skills(self, node: str) -> list[Document]:
        skills: list[Document] = []
        for edge in await self._graph.get_edges_from_node(node, CollectionNames.AGENT_HAS_SKILL.value):
            skill = await self._graph.get_document(_target_key(edge), _SKILLS, raise_on_error=True)
            if skill is None or skill.get("status") not in (None, "active"):
                continue
            name, version, tenant = _text(skill, "name"), _text(skill, "version"), _text(skill, "orgId")
            digest = await self._skill_digest(tenant, name, version) if name and version else None
            if digest is None:
                continue
            skills.append(
                {
                    "name": name,
                    "version": version,
                    "builtin": _text(skill, "source") == "builtin",
                    "createdBy": None if _text(skill, "source") == "builtin" else await self._person(_text(skill, "createdBy")),
                    "bundleDigest": digest,
                }
            )
        return skills


__all__ = ["AGENT_PAGE", "LegacyCapabilityReader"]
