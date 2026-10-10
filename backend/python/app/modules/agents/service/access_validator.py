"""Least privilege for what an agent is created with.

The knowledge sources and toolsets a new agent names must already be usable by the person
creating it. From a chat (`origin='chat'`) a violation is refused. From the builder
(`origin='ui'`) it is only logged as `agent.access_violation` until the log shows the rule
would not break existing flows.
"""

import asyncio
import json
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

VIOLATION_EVENT = "agent.access_violation"


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
        """Returns the spec to create. From a chat it is private and never a service account."""
        if origin == "chat":
            if spec.is_service_account:
                raise ServiceAccountNotAllowedError()
            spec = spec.model_copy(update={"share_with_org": False})
        await self._enforce(
            actor,
            origin,
            "create",
            knowledge=spec.knowledge,
            toolsets=spec.toolsets,
            mcp_servers=spec.mcp_servers if origin == "chat" else [],
            is_service_account=spec.is_service_account,
        )
        return spec

    async def enforce_update(self, actor: AgentActor, body: Mapping[str, Any], agent: Mapping[str, Any]) -> None:
        await self._enforce(
            actor,
            "ui",
            "update",
            knowledge=body.get("knowledge"),
            toolsets=body.get("toolsets"),
            mcp_servers=[],
            is_service_account=bool(body.get("isServiceAccount", agent.get("isServiceAccount", False))),
        )

    async def _enforce(
        self,
        actor: AgentActor,
        origin: AgentOrigin,
        action: str,
        *,
        knowledge: object,
        toolsets: object,
        mcp_servers: Sequence[Any],
        is_service_account: bool,
    ) -> None:
        try:
            report = await self.check(
                actor,
                knowledge=knowledge if isinstance(knowledge, list) else [],
                toolsets=toolsets if isinstance(toolsets, list) else [],
                mcp_servers=mcp_servers,
                is_service_account=is_service_account,
            )
        except Exception:
            if origin == "chat":
                raise
            self._logger.warning("%s check failed; not blocking a builder %s", VIOLATION_EVENT, action, exc_info=True)
            return
        self._apply(report, actor, origin, action)

    def _apply(self, report: AccessReport, actor: AgentActor, origin: AgentOrigin, action: str) -> None:
        if not report:
            return
        if origin == "ui":
            self._logger.warning(
                "%s %s",
                VIOLATION_EVENT,
                json.dumps({
                    "action": action,
                    "actor": actor.user_id,
                    "orgId": actor.org_id,
                    "knowledge": report.knowledge,
                    "toolsets": report.toolsets,
                }),
            )
            return
        if report.knowledge:
            raise InvalidKnowledgeError(report.knowledge)
        raise InvalidToolsetError(report.toolsets)

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
        # An MCP server is not offered from a chat, so naming one there is a request for more than the card allowed.
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
