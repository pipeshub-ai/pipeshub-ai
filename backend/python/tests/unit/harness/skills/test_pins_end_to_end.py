"""AD-4: a run pinned at admission sees the admission-time skill, whatever happens to the skill afterwards."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.run_definition import (
    AgentRunDefinition,
    RunDefinitionSkill,
    pin_skill_content,
)
from app.harness.skills.bundles import SkillBundleService
from harness_core.sandbox.types import LeaseRef
from harness_core.skills import (
    SkillBundleUnavailable,
    SkillPin,
    SkillRef,
    TarSkillMaterializer,
    legacy_skill_pin_digest,
    skills_mount,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeContractEnv, FakeSandboxBroker
from harness_core.testing.sandbox.contract import run, with_mounts
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG, SESSION = "org-1", "ses-1"
SKILL_MD = "---\nname: deploy\ndescription: Use when deploying ({marker})\n---\n\nStep 1. {marker}.\n"


class World:
    def __init__(self) -> None:
        clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
        self.graph = FakeGraphProvider()
        self.skills = GraphSkillStore(self.graph, ORG, "user-1")  # type: ignore[arg-type]
        self.source = GraphSkillSource(self.graph)  # type: ignore[arg-type]
        self.scope = ObjectScope(ORG, f"ws:{SESSION}")
        objects, workspaces = InMemoryBinaryObjectStore(), InMemoryWorkspaceStore(clock=clock)
        self.bundles = SkillBundleService(source=self.source, objects=objects, workspaces=workspaces, session_id=SESSION)
        self.materializer = TarSkillMaterializer(objects=objects, workspaces=workspaces)
        self.broker = FakeSandboxBroker(clock=clock)
        self.env = FakeContractEnv(clock)

    async def admit(self) -> SkillPin:
        definition = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name="deploy"),))
        skill = (await pin_skill_content(definition, self.source, ORG)).skills[0]
        assert skill.bundle_digest is not None
        return SkillPin(skill.name, skill.bundle_digest)

    async def skill_md_seen_by_the_run(self, pin: SkillPin) -> bytes:
        bundle = await self.bundles.resolve(self.scope, pin)
        sandbox = await self.broker.acquire(
            with_mounts(self.env.spec(), skills_mount("/skills")), lease=LeaseRef("w", 1), idempotency_key=pin.bundle_digest
        )
        await self.materializer.materialize(self.scope, sandbox, [bundle], skills_dir="/skills")
        out, _, code = await run(await sandbox.exec(self.env.argv_read_file("/skills/deploy/SKILL.md"), env={}))
        assert code == 0
        return out


@pytest.fixture
async def world() -> World:
    w = World()
    _ = await w.skills.create_skill("deploy", SKILL_MD.format(marker="admitted"), resources={"scripts/run.py": "v1"})
    return w


async def test_ad4_a_content_edit_and_a_delete_after_admission_do_not_reach_the_run(world: World) -> None:
    pin = await world.admit()
    admitted = (await world.source.files(ORG, SkillRef("deploy")) or {})["SKILL.md"]
    _ = await world.skills.update_skill("deploy", SKILL_MD.format(marker="edited"))
    assert await world.skills.delete_skill("deploy")
    assert await world.skill_md_seen_by_the_run(pin) == admitted


async def test_ad4_once_materialized_the_session_holds_the_bundle(world: World) -> None:
    pin = await world.admit()
    admitted = await world.skill_md_seen_by_the_run(pin)
    _ = await world.skills.write_resource("deploy", "scripts/run.py", "v2")
    assert await world.skill_md_seen_by_the_run(pin) == admitted


async def test_ad4_a_resource_edit_before_the_first_materialization_is_refused_not_served(world: World) -> None:
    # Resource edits keep the version and leave no snapshot (open item): the pin cannot be found, never replaced.
    pin = await world.admit()
    _ = await world.skills.write_resource("deploy", "scripts/run.py", "v2")
    with pytest.raises(SkillBundleUnavailable):
        _ = await world.bundles.resolve(world.scope, pin)


async def test_ad4_a_legacy_pin_still_resolves_to_the_version_it_named(world: World) -> None:
    v1 = (await world.source.files(ORG, SkillRef("deploy")) or {})["SKILL.md"]
    _ = await world.skills.update_skill("deploy", SKILL_MD.format(marker="edited"))
    assert await world.skill_md_seen_by_the_run(SkillPin("deploy", legacy_skill_pin_digest("deploy", "1.0.0"))) == v1
