"""Least privilege for what an agent drafted in a chat is created with.

With `origin='chat'` the knowledge sources and toolsets a new agent names must already be
usable by the person creating it, or the create is refused. A builder create (`origin='ui'`)
is not checked here.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from logging import Logger
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.modules.agents.service.builders import (
    _parse_knowledge_sources,
    _parse_toolsets,
)
from app.modules.agents.service.errors import (
    InvalidKnowledgeError,
    InvalidToolsetError,
    ServiceAccountNotAllowedError,
)
from app.modules.agents.service.models import AgentActor, AgentOrigin, AgentSpec
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


@dataclass
class AccessReport:
    """What the actor may not attach, by rule."""

    knowledge: list[str] = field(default_factory=list)
    toolsets: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.knowledge or self.toolsets)


class AccessValidator:
    def __init__(self, graph: IGraphDBProvider, config: ConfigurationService | None, logger: Logger) -> None:
        self._graph = graph
        self._config = config
        self._logger = logger

    async def enforce_create(self, actor: AgentActor, spec: AgentSpec, origin: AgentOrigin) -> AgentSpec:
        """Returns the spec to create. With `origin='chat'` it is private, never a service account,
        and names only what the actor can already use; a builder spec is returned as is."""
        if origin != "chat":
            return spec
        if spec.is_service_account:
            raise ServiceAccountNotAllowedError()
        spec = spec.model_copy(update={"share_with_org": False})
        report = await self.check(
            actor,
            knowledge=spec.knowledge if isinstance(spec.knowledge, list) else [],
            toolsets=spec.toolsets if isinstance(spec.toolsets, list) else [],
            mcp_servers=spec.mcp_servers,
            is_service_account=spec.is_service_account,
        )
        if report.knowledge:
            raise InvalidKnowledgeError(report.knowledge)
        if report.toolsets:
            raise InvalidToolsetError(report.toolsets)
        return spec

    async def check(
        self,
        actor: AgentActor,
        *,
        knowledge: Sequence[Any],
        toolsets: Sequence[Any],
        mcp_servers: Sequence[Any],
        is_service_account: bool,
    ) -> AccessReport:
        knowledge_ids = list(_parse_knowledge_sources(list(knowledge)))
        toolset_entries = _parse_toolsets(list(toolsets))
        knowledge_bad, toolsets_bad = await asyncio.gather(
            self._inaccessible_knowledge(actor, knowledge_ids),
            self._unusable_toolsets(actor, toolset_entries, is_service_account=is_service_account),
        )
        # `origin='chat'` accepts no MCP servers, so naming one is a request for more than allowed.
        extra = [
            str(entry.get("instanceId") or entry.get("name") or "")
            for entry in mcp_servers
            if isinstance(entry, dict)
        ]
        return AccessReport(knowledge=knowledge_bad, toolsets=[*toolsets_bad, *[e for e in extra if e]])

    async def _inaccessible_knowledge(self, actor: AgentActor, ids: list[str]) -> list[str]:
        """Connector instances and collections both arrive in `app_ids`; a collection also passes on a
        direct role. Anything that cannot be verified counts as inaccessible."""
        if not ids:
            return []
        graph = self._graph
        try:
            containers = await graph.get_accessible_containers(actor.user_id, actor.org_id)
            reachable = frozenset() if containers.fallback_reason else containers.app_ids
            bad: list[str] = []
            for source_id in ids:
                if source_id in reachable:
                    continue
                if await graph.get_user_kb_permission(source_id, actor.user_key) is None:
                    bad.append(source_id)
            return bad
        except Exception:
            self._logger.warning("Could not verify knowledge access for agent create", exc_info=True)
            return list(ids)

    async def _unusable_toolsets(
        self, actor: AgentActor, parsed: Mapping[str, Mapping[str, Any]], *, is_service_account: bool,
    ) -> list[str]:
        """A toolset must name an instance of this org that the actor has signed in to.
        A service-account agent signs in later under its own key, so only the instance is checked."""
        from app.agents.constants.toolset_constants import get_toolset_config_path
        from app.edition_config import (
            get_toolset_by_id,  # the edition seam imports the routes, which import this module
        )

        async def usable(name: str, entry: Mapping[str, Any]) -> str | None:
            instance_id = entry.get("instanceId")
            if not instance_id or self._config is None:
                return name
            try:
                instance = await get_toolset_by_id(str(instance_id), self._config, org_id=actor.org_id)
                if not instance:
                    return str(instance_id)
                if is_service_account:
                    return None
                auth = await self._config.get_config(get_toolset_config_path(str(instance_id), actor.user_id))
            except Exception:
                self._logger.warning("Could not verify toolset %s for agent create", instance_id, exc_info=True)
                return str(instance_id)
            return None if isinstance(auth, dict) and auth.get("isAuthenticated") else str(instance_id)

        results = await asyncio.gather(*(usable(n, e) for n, e in parsed.items()))
        return [r for r in results if r]
