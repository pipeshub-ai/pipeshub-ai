"""`PrerequisiteValidator`: Part A2/E of the plan -- checks that a task's
declared capabilities (connectors, knowledge-base collections, MCP servers)
are actually usable before letting the agent create, or the executor run, a
task doomed to fail for a missing/unauthenticated dependency.

Called from two places against the SAME logic, never duplicated:
  - `application/engine.py::TaskEngine.create()` -- creation-time check, so
    the chat agent can tell the user what's missing instead of creating a
    task that will fail at its first scheduled fire (Part A2).
  - `runtime/executor.py::TaskExecutor` -- run-time re-check immediately
    before `TaskSpecAssembler.assemble()`, since tokens expire and
    collections get un-shared between creation and any later fire (Part E:
    "Prerequisites re-validated per run, never replayed from a
    creation-time snapshot").

`toolset_ids` IS checked here (authentication state, from etcd), because an
unauthenticated toolset is dropped during assembly and leaves the agent
silently short of the capability it was created for.

`tool_names` is deliberately NOT checked here: this validator has no
`ToolRegistry` and building one would be far more expensive than the two
places that already do it exactly. `task_manage`/`workflow_manage` reject
unknown names at creation against the live session registry
(`_shared.tool_name_issues`), and `TaskSpecAssembler.assemble()` raises
`ToolResolutionError` at run time against the run's own registry -- which
the executor surfaces through this module's `PrerequisiteCheckResult`, so
the user sees one consistent "prerequisites not met" message either way.

`mcp_server_ids`: PipesHub has no MCP server registry today (no graph
schema, no service -- confirmed by search). A non-empty list can never be
proven missing OR present here, so it is never treated as blocking; it
surfaces as a non-blocking, informational `PrerequisiteIssue` instead, so a
caller that wants to warn the user still can, without this validator
inventing a fake registry just to satisfy a check that has nothing real to
check against yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.tasks.domain.models import TaskDefinition

__all__ = ["PrerequisiteCheckResult", "PrerequisiteIssue", "PrerequisiteValidator"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrerequisiteIssue:
    kind: str
    """One of: "connector", "collection", "mcp_server", "toolset"."""

    id: str
    reason: str
    blocking: bool = True


@dataclass
class PrerequisiteCheckResult:
    issues: list[PrerequisiteIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff there is no BLOCKING issue -- a non-blocking (e.g. MCP
        server) issue never fails a create/run on its own."""
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_issues(self) -> list[PrerequisiteIssue]:
        return [issue for issue in self.issues if issue.blocking]

    def summary(self) -> str:
        if not self.issues:
            return "All prerequisites satisfied."
        return "; ".join(f"{issue.kind} {issue.id!r}: {issue.reason}" for issue in self.issues)


class PrerequisiteValidator:
    """Stateless -- every call takes the ids to check and the stores to check
    them against; no instance state survives between calls."""

    async def validate_task(
        self,
        task: "TaskDefinition",
        *,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService | None" = None,
    ) -> PrerequisiteCheckResult:
        return await self.validate(
            org_id=task.principal.org_id,
            user_id=task.principal.user_id,
            is_service_account=task.principal.is_service_account,
            task_id=task.task_id,
            connector_ids=task.connector_ids,
            collection_ids=task.collection_ids,
            mcp_server_ids=task.mcp_server_ids,
            toolset_ids=task.toolset_ids,
            graph_provider=graph_provider,
            config_service=config_service,
        )

    async def validate(
        self,
        *,
        org_id: str,
        user_id: str,
        connector_ids: list[str],
        collection_ids: list[str],
        mcp_server_ids: list[str],
        toolset_ids: list[str] | None = None,
        is_service_account: bool = False,
        task_id: str | None = None,
        graph_provider: "IGraphDBProvider",
        config_service: "ConfigurationService | None" = None,
    ) -> PrerequisiteCheckResult:
        issues: list[PrerequisiteIssue] = []
        issues.extend(await self._check_connectors(org_id, user_id, connector_ids, graph_provider))
        issues.extend(await self._check_collections(user_id, collection_ids, graph_provider))
        issues.extend(self._check_mcp_servers(mcp_server_ids))
        # A service account's credentials are keyed by task_id, which doesn't
        # exist yet at creation time -- checking then would look up an empty
        # key and report every toolset as unauthenticated. The run-time call
        # (which always has a task_id) still covers that case.
        credential_lookup_id = task_id if is_service_account else user_id
        if toolset_ids and config_service is not None and credential_lookup_id:
            # For scheduled headless runs the spec_assembler silently drops
            # unauthenticated toolsets rather than failing the run, which
            # means a workflow that depends on Slack gets zero Slack tools
            # and fails with a cryptic "no tools" error.  Check here instead
            # so the error is surfaced at prerequisite time with a clear
            # message that the user can act on (re-authenticate the toolset).
            issues.extend(await self._check_toolsets(
                toolset_ids, credential_lookup_id, config_service,
            ))
        return PrerequisiteCheckResult(issues=issues)

    async def _check_connectors(
        self, org_id: str, user_id: str, connector_ids: list[str], graph_provider: "IGraphDBProvider",
    ) -> list[PrerequisiteIssue]:
        if not connector_ids:
            return []
        try:
            instances = await graph_provider.get_user_connector_instances(
                collection=CollectionNames.APPS.value,
                user_id=user_id,
                org_id=org_id,
                team_scope=ConnectorScope.TEAM.value,
                personal_scope=ConnectorScope.PERSONAL.value,
            )
        except Exception as exc:
            logger.warning("Connector prerequisite check failed for org %s: %s", org_id, exc)
            return [
                PrerequisiteIssue(kind="connector", id=cid, reason=f"could not verify connector status: {exc}")
                for cid in connector_ids
            ]
        by_id = {str(doc.get("_key") or doc.get("id")): doc for doc in (instances or [])}
        issues: list[PrerequisiteIssue] = []
        for connector_id in connector_ids:
            doc = by_id.get(connector_id)
            if doc is None:
                issues.append(PrerequisiteIssue(
                    kind="connector", id=connector_id,
                    reason="not connected, or not accessible to this user",
                ))
            elif not doc.get("isConfigured"):
                issues.append(PrerequisiteIssue(
                    kind="connector", id=connector_id, reason="connected but not yet configured",
                ))
            elif not doc.get("isAuthenticated", True):
                issues.append(PrerequisiteIssue(
                    kind="connector", id=connector_id,
                    reason="authentication expired or revoked -- reconnect required",
                ))
        return issues

    async def _check_collections(
        self, user_id: str, collection_ids: list[str], graph_provider: "IGraphDBProvider",
    ) -> list[PrerequisiteIssue]:
        issues: list[PrerequisiteIssue] = []
        for kb_id in collection_ids:
            try:
                role = await graph_provider.get_user_kb_permission(kb_id, user_id)
            except Exception as exc:
                logger.warning("Collection prerequisite check failed for kb %s: %s", kb_id, exc)
                issues.append(PrerequisiteIssue(kind="collection", id=kb_id, reason=f"could not verify access: {exc}"))
                continue
            if not role:
                issues.append(PrerequisiteIssue(
                    kind="collection", id=kb_id, reason="not found, or not accessible to this user",
                ))
        return issues

    def _check_mcp_servers(self, mcp_server_ids: list[str]) -> list[PrerequisiteIssue]:
        return [
            PrerequisiteIssue(
                kind="mcp_server", id=mcp_id,
                reason="MCP server availability cannot be verified -- no MCP server registry exists yet",
                blocking=False,
            )
            for mcp_id in mcp_server_ids
        ]

    async def _check_toolsets(
        self,
        toolset_ids: list[str],
        credential_lookup_id: str,
        config_service: "ConfigurationService",
    ) -> list[PrerequisiteIssue]:
        """Check that every declared toolset instance is authenticated for this
        run's credential_lookup_id.  A non-authenticated toolset is BLOCKING
        for scheduled runs because `TaskSpecAssembler` silently drops it,
        leaving the agent with no tools for that capability."""
        from app.agents.constants.toolset_constants import get_toolset_config_path

        if not toolset_ids:
            return []
        issues: list[PrerequisiteIssue] = []
        for instance_id in toolset_ids:
            try:
                etcd_path = get_toolset_config_path(instance_id, credential_lookup_id)
                config = await config_service.get_config(etcd_path)
            except Exception as exc:
                logger.warning("Could not verify toolset %s credentials: %s", instance_id, exc)
                issues.append(PrerequisiteIssue(
                    kind="toolset", id=instance_id,
                    reason=f"could not verify authentication: {exc}",
                ))
                continue
            if not config:
                issues.append(PrerequisiteIssue(
                    kind="toolset", id=instance_id,
                    reason="not authenticated -- user must authenticate this toolset before scheduling",
                ))
            elif not config.get("isAuthenticated", False):
                issues.append(PrerequisiteIssue(
                    kind="toolset", id=instance_id,
                    reason="authentication expired or revoked -- re-authenticate required",
                ))
        return issues
