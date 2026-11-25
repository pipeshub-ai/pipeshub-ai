"""CPS SK-8/SK-9: production engine runs get the skills they pinned.

The worker's own ``engine_services`` is built over the in-memory graph fake and in-memory object and workspace
stores; a skill pinned at admission (``pin_skill_content``, as the run definition route pins it) resolves to the
bundle the controller seeds into the sandbox, whatever the skill became since.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.run_definition import (
    AgentRunDefinition,
    RunDefinitionSkill,
    pin_skill_content,
)
from app.harness.engines.worker import EnginePlan, engine_services
from harness_core.skills import SkillPin
from harness_core.telemetry import NOOP_TRACER
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from harness_engines.release import release_allowlist
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG, SESSION = "org-1", "ses-1"
SKILL_MD = "---\nname: deploy\ndescription: Use when deploying ({marker})\n---\n\nStep 1. {marker}.\n"


class _Services:
    """The ``RunServices`` part the engine services read: the deployment's graph store."""

    def __init__(self, graph: FakeGraphProvider) -> None:
        self._graph = graph
        self.graph_reads = 0

    async def graph_provider(self) -> FakeGraphProvider:
        self.graph_reads += 1
        return self._graph


def _services(services: _Services) -> object:
    clock = ManualClock(datetime(2026, 10, 9, tzinfo=UTC))
    plan = EnginePlan(broker=FakeSandboxBroker(clock=clock), releases=release_allowlist(), port=0)
    return engine_services(
        plan,
        services=services,  # type: ignore[arg-type]
        tool_host=SimpleNamespace(),  # type: ignore[arg-type]
        route_for=lambda token: SimpleNamespace(),  # type: ignore[arg-type]
        clock=clock,
        objects=InMemoryBinaryObjectStore(),
        tracer=NOOP_TRACER,
    )


async def test_sk8_the_production_engine_services_build_skill_bundles() -> None:
    services = _Services(FakeGraphProvider())

    engines = _services(services)

    assert engines.skill_bundles_for is not None  # type: ignore[attr-defined]
    assert services.graph_reads == 0, "the skill stack is reached on first use only (D7)"


async def test_sk9_a_pinned_skill_resolves_to_the_content_admitted() -> None:
    graph = FakeGraphProvider()
    store = GraphSkillStore(graph, ORG, "author")  # type: ignore[arg-type]
    _ = await store.create_skill("deploy", SKILL_MD.format(marker="admitted"))
    definition = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name="deploy"),))
    pinned = (await pin_skill_content(definition, GraphSkillSource(graph), ORG)).skills[0]  # type: ignore[arg-type]
    assert pinned.bundle_digest is not None
    _ = await store.update_skill("deploy", SKILL_MD.format(marker="edited"))
    engines = _services(_Services(graph))
    objects = InMemoryBinaryObjectStore()
    workspaces = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 9, tzinfo=UTC)))
    scope = ObjectScope(ORG, f"ws:{SESSION}")

    bundles = engines.skill_bundles_for(scope, objects, workspaces, SESSION)  # type: ignore[attr-defined]
    bundle = await bundles.resolve(scope, SkillPin("deploy", pinned.bundle_digest))

    assert bundle.name == "deploy" and bundle.digest == pinned.bundle_digest
