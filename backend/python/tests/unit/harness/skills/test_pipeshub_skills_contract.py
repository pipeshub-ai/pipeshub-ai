"""SS and BB contracts on PipesHub's adapters: ``GraphSkillSource`` over the graph skill store, and
``SkillBundleService`` storing bundles in the session workspace, in memory and through Node (sealed)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.harness.skills.bundles import (
    GraphProviderSkillSource,
    SkillBundleService,
    skill_bundle_workspace_prefix,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.skills import (
    SkillBundleBuilderContract,
    SkillContentSourceContract,
)
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider
from tests.unit.harness.test_node_journal_contract import node_server
from tests.unit.harness.test_node_workspace_contract import (
    ORG,
    OTHER_ORG,
    SESSION,
    _World,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.skills import SkillBundleRef, SkillPin, SkillRef
    from tests.unit.harness.test_node_journal_contract import _NodeServer

__all__ = ["node_server"]


def graph_content(name: str, marker: str) -> dict[str, bytes]:
    return {
        "SKILL.md": f"---\nname: {name}\ndescription: Use when testing {marker}\n---\n\nStep 1. {marker}.\n".encode(),
        "scripts/run.py": f"print({marker!r})\n".encode(),
        "references/notes.md": f"# Notes ✓ {marker}\n".encode(),
    }


class GraphSourceHarness:
    def __init__(self) -> None:
        self.graph = FakeGraphProvider()
        self.source = GraphSkillSource(self.graph)  # type: ignore[arg-type]
        self.tenant, self.other_tenant = ORG, OTHER_ORG

    def content(self, name: str, marker: str) -> dict[str, bytes]:
        return graph_content(name, marker)

    async def publish(self, tenant: str, name: str, files: Mapping[str, bytes]) -> str:
        store = GraphSkillStore(self.graph, tenant, "user-1")  # type: ignore[arg-type]
        texts = {path: content.decode() for path, content in files.items()}
        skill_md = texts.pop("SKILL.md")
        if await store.exists(name):
            metadata = await store.update_skill(name, skill_md, resources=texts)
        else:
            metadata = await store.create_skill(name, skill_md, resources=texts)
        return metadata.version

    async def delete(self, tenant: str, name: str) -> None:
        assert await GraphSkillStore(self.graph, tenant, "user-1").delete_skill(name)  # type: ignore[arg-type]


class TestGraphSkillSource(SkillContentSourceContract):
    @pytest.fixture
    def harness(self) -> GraphSourceHarness:  # type: ignore[override]
        return GraphSourceHarness()


class LazyGraphSourceHarness(GraphSourceHarness):
    """QA19-9: what the engine worker wires, the graph source built on first use from the shared provider."""

    def __init__(self) -> None:
        super().__init__()

        async def provider() -> object:
            return self.graph

        self.source = GraphProviderSkillSource(provider)  # type: ignore[assignment]


class TestGraphProviderSkillSource(SkillContentSourceContract):
    @pytest.fixture
    def harness(self) -> LazyGraphSourceHarness:  # type: ignore[override]
        return LazyGraphSourceHarness()


class GraphBuilderHarness(GraphSourceHarness):
    def __init__(self) -> None:
        super().__init__()
        self.scope = ObjectScope(ORG, f"ws:{SESSION}")
        self.other_scope = ObjectScope(OTHER_ORG, f"ws:{SESSION}")
        self.objects = InMemoryBinaryObjectStore()
        self.workspaces = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 3, tzinfo=UTC)))
        self.builder = SkillBundleService(
            source=self.source, objects=self.objects, workspaces=self.workspaces, session_id=SESSION
        )


class TestSkillBundleServiceInMemory(SkillBundleBuilderContract):
    @pytest.fixture
    def harness(self) -> GraphBuilderHarness:  # type: ignore[override]
        return GraphBuilderHarness()

    async def test_bundles_live_inside_the_session_workspace(self, harness: GraphBuilderHarness) -> None:
        _ = await harness.publish(ORG, "in-session", graph_content("in-session", "x"))
        from harness_core.skills import SkillRef

        bundle = await harness.builder.build(harness.scope, SkillRef("in-session"))
        assert bundle.workspace_id.startswith(f"{skill_bundle_workspace_prefix(SESSION)}:")


class _ByTenant:
    """One Node attempt serves one org; the contract also asks another org, through its own attempt."""

    def __init__(self, builders: dict[str, SkillBundleService]) -> None:
        self._builders = builders

    async def build(self, scope: ObjectScope, skill: SkillRef) -> SkillBundleRef:
        return await self._builders[scope.tenant_id].build(scope, skill)

    async def resolve(self, scope: ObjectScope, pin: SkillPin) -> SkillBundleRef:
        return await self._builders[scope.tenant_id].resolve(scope, pin)


class NodeBuilderHarness(GraphSourceHarness):
    def __init__(self, world: _World) -> None:
        super().__init__()
        self._world = world

    async def start(self) -> NodeBuilderHarness:
        main = await self._world.attempt("run-skills-main", ORG)
        other = await self._world.attempt("run-skills-other", OTHER_ORG)
        self.scope = ObjectScope(ORG, f"ws:{SESSION}")
        self.other_scope = ObjectScope(OTHER_ORG, f"ws:{SESSION}")
        self.workspaces = main.store
        self.objects = SealedObjectStore(InMemoryBinaryObjectStore(), main.keys)
        builders = {
            ORG: SkillBundleService(source=self.source, objects=self.objects, workspaces=main.store, session_id=SESSION),
            OTHER_ORG: SkillBundleService(
                source=self.source,
                objects=SealedObjectStore(InMemoryBinaryObjectStore(), other.keys),
                workspaces=other.store,
                session_id=SESSION,
            ),
        }
        self.builder = _ByTenant(builders)
        return self


@pytest.fixture
async def world(node_server: _NodeServer) -> _World:
    return _World(node_server)


class TestSkillBundleServiceThroughNode(SkillBundleBuilderContract):
    @pytest.fixture
    async def harness(self, world: _World) -> NodeBuilderHarness:  # type: ignore[override]
        return await NodeBuilderHarness(world).start()
