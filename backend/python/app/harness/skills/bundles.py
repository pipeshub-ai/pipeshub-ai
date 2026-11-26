"""PipesHub's ``SkillBundleBuilder``: bundles live in the session's workspace, sealed and erased with it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.skills import StoredSkillBundleBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.logs import HarnessLog
    from harness_core.skills import SkillBundleBuilder, SkillContentSource, SkillRef
    from harness_core.workspace import ObjectScope
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore

    SkillBundlesFor = Callable[[ObjectScope, BinaryObjectStore, WorkspaceStore, str], SkillBundleBuilder]


def skill_bundle_workspace_prefix(session_id: str) -> str:
    """Inside the session workspace, which Node's ``workspaceInSession`` accepts for the session's runs."""
    return f"ws:{session_id}:skills"


class SkillBundleService(StoredSkillBundleBuilder):
    """The org's skills (``GraphSkillSource``, injected) as bundles of one session."""

    def __init__(
        self,
        *,
        source: SkillContentSource,
        objects: BinaryObjectStore,
        workspaces: WorkspaceStore,
        session_id: str,
        log: HarnessLog | None = None,
    ) -> None:
        super().__init__(
            source=source,
            objects=objects,
            workspaces=workspaces,
            workspace_prefix=skill_bundle_workspace_prefix(session_id),
            log=log,
        )


class GraphProviderSkillSource:
    """``GraphSkillSource`` over the deployment's graph store, built on first use: an idle worker imports nothing
    of the skill stack (D7), and the graph connection is the one the worker's other runs share."""

    def __init__(self, graph_provider: Callable[[], Awaitable[object]]) -> None:
        self._graph_provider = graph_provider
        self._source: SkillContentSource | None = None

    async def _resolved(self) -> SkillContentSource:
        if self._source is None:
            from app.agents.agent_loop.skills.bundle_source import GraphSkillSource

            self._source = GraphSkillSource(await self._graph_provider())  # type: ignore[arg-type]
        return self._source

    async def files(self, tenant_id: str, skill: SkillRef) -> Mapping[str, bytes] | None:
        return await (await self._resolved()).files(tenant_id, skill)

    async def versions(self, tenant_id: str, name: str) -> Sequence[str]:
        return await (await self._resolved()).versions(tenant_id, name)


def session_skill_bundles(source: SkillContentSource) -> SkillBundlesFor:
    """The engine controller's bundles for one attempt: the run's pins (its agent's assignment, which is how a
    skill is shared) resolved from ``source``, stored in the run's session."""

    def bundles_for(
        _scope: ObjectScope, objects: BinaryObjectStore, workspaces: WorkspaceStore, session_id: str
    ) -> SkillBundleBuilder:
        return SkillBundleService(source=source, objects=objects, workspaces=workspaces, session_id=session_id)

    return bundles_for


__all__ = ["GraphProviderSkillSource", "SkillBundleService", "session_skill_bundles", "skill_bundle_workspace_prefix"]
