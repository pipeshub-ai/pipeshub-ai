"""CPS SK-1..SK-5: who sees a custom skill at run time, on both loops.

A custom skill is visible to its owner and to whoever runs an agent it is assigned to; builtins are org-wide.
The catalog is read through the real ``GraphSkillStore`` over the in-memory graph fake, and built by the same
factory each loop uses (today's loop: ``build_skill_manager``; the durable loop: ``SkillsSource``).
"""

from __future__ import annotations

import itertools
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillSource
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility
from app.agents.agent_loop.skills.manager_factory import (
    get_builtin_seeder,
    reconcile_builtin_skills,
    reset_builtin_reconcile_state,
)
from app.agents.agent_loop.skills_wiring import build_skill_manager
from app.api.routes.agent import _create_skill_edges
from app.config.constants.arangodb import CollectionNames
from app.harness.assembly.tool_sources import SkillsSource
from harness_core.tools.registry import ToolRegistry
from tests.support.config_service import FakeConfigurationService
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"
# One person, two ids: the runtime stamps the user id, the REST API and agent assignment the graph key.
ALICE = SimpleNamespace(user_id="user-alice", key="key-alice")
BOB = SimpleNamespace(user_id="user-bob", key="key-bob")


def _skill_md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nDo the {name} steps.\n"


async def _seed(graph: FakeGraphProvider) -> None:
    alice_rest = GraphSkillStore(graph, ORG, ALICE.key)  # type: ignore[arg-type]
    await alice_rest.create_skill("alice-report", _skill_md("alice-report", "Alice's quarterly report recipe"))
    await alice_rest.create_skill("alice-secret", _skill_md("alice-secret", "Alice's private negotiation notes"))
    alice_runtime = GraphSkillStore(graph, ORG, ALICE.user_id)  # type: ignore[arg-type]
    await alice_runtime.create_skill("alice-learned", _skill_md("alice-learned", "A skill Alice's agent wrote"))
    bob_rest = GraphSkillStore(graph, ORG, BOB.key)  # type: ignore[arg-type]
    await bob_rest.create_skill("bob-notes", _skill_md("bob-notes", "Bob's meeting notes recipe"))


async def _seed_at_startup(graph: FakeGraphProvider) -> None:
    """Everyone's skills, and the builtins as service startup seeds them (main #3597): a request no longer does."""
    await _seed(graph)
    reset_builtin_reconcile_state()
    await reconcile_builtin_skills(graph, ORG)  # type: ignore[arg-type]


def _context(graph: FakeGraphProvider, who: SimpleNamespace, agent_skills: list[str] | None) -> AgentContext:
    return AgentContext(
        org_id=ORG,
        user_id=who.user_id,
        user_email=f"{who.user_id}@example.com",
        user_info={"userId": who.user_id, "orgId": ORG, "_key": who.key},
        graph_provider=graph,
        config_service=FakeConfigurationService(),
        agent_skills=agent_skills,
    )


def _builtin_names() -> set[str]:
    seeder = get_builtin_seeder()
    assert seeder is not None
    return set(seeder.pack_versions)


async def _catalog_names(manager: Any) -> set[str]:  # noqa: ANN401 - SkillManager or ScopedSkillManager
    return {m.name for m in await manager.list_skills()}


class TestTodaysLoop:
    async def test_sk1_an_unassigned_agent_never_sees_a_coworkers_custom_skill(self) -> None:
        graph = FakeGraphProvider()
        await _seed_at_startup(graph)

        manager = await build_skill_manager(_context(graph, BOB, None), TransportRegistry())

        assert manager is not None
        names = await _catalog_names(manager)
        assert names == {"bob-notes"} | _builtin_names()
        with pytest.raises(RegistryError):
            await manager.activate_skill("alice-report")
        assert await manager.get_resources("alice-report") == {}
        found = {match.skill.name for match in await manager.search("quarterly report recipe", limit=50)}
        assert "alice-report" not in found

    async def test_sk1_the_owner_sees_skills_stamped_under_either_of_their_ids(self) -> None:
        graph = FakeGraphProvider()
        await _seed(graph)

        manager = await build_skill_manager(_context(graph, ALICE, None), TransportRegistry())

        assert manager is not None
        assert {"alice-report", "alice-secret", "alice-learned"} <= await _catalog_names(manager)
        assert "bob-notes" not in await _catalog_names(manager)

    async def test_sk2_a_skill_assigned_to_the_agent_is_shared_with_whoever_runs_it(self) -> None:
        graph = FakeGraphProvider()
        await _seed(graph)

        manager = await build_skill_manager(_context(graph, BOB, ["alice-report"]), TransportRegistry())

        assert manager is not None
        assert await _catalog_names(manager) == {"alice-report"}
        assert (await manager.activate_skill("alice-report")).metadata.name == "alice-report"
        with pytest.raises(RegistryError):
            await manager.activate_skill("alice-secret")


class TestDurableLoop:
    async def test_sk3_a_run_pinning_no_skills_reads_the_same_scoped_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        graph = FakeGraphProvider()
        await _seed(graph)
        source = SkillsSource()
        # The durable worker sets ``agent_skills`` from the pin: none pinned is ``None`` (tool_environment).
        env = SimpleNamespace(context=_context(graph, BOB, None))

        tools = await source.load(env, SimpleNamespace(skills=(), skill_digests={}, run_id="run-1", session_id="ses-1"), ToolRegistry())  # type: ignore[arg-type]

        assert tools, "the skill tools are registered"
        assert source.missing(SimpleNamespace(skills=("alice-report",))) == ["skill:alice-report"]  # type: ignore[arg-type]
        assert source.missing(SimpleNamespace(skills=("bob-notes",))) == []  # type: ignore[arg-type]


def _doc(created_by: str, name: str, source: str = SkillSource.MANUAL.value) -> dict[str, str]:
    return {"createdBy": created_by, "name": name, "source": source}


class TestTheRule:
    @pytest.mark.parametrize(
        ("viewers", "shared", "doc", "visible"),
        [
            ({"a"}, set(), _doc("a", "x"), True),
            ({"b"}, set(), _doc("a", "x"), False),
            ({"b"}, {"x"}, _doc("a", "x"), True),
            ({"b"}, {"y"}, _doc("a", "x"), False),
            ({"b"}, set(), _doc("seed", "x", SkillSource.BUILTIN.value), True),
            (set(), set(), _doc("a", "x"), False),
            (set(), set(), _doc("seed", "x", SkillSource.BUILTIN.value), True),
        ],
    )
    def test_sk4_visible_iff_builtin_owned_or_shared(
        self, viewers: set[str], shared: set[str], doc: dict[str, str], visible: bool
    ) -> None:
        assert SkillVisibility.of(viewers, shared=shared).allows(doc) is visible

    def test_sk4_over_every_combination_a_custom_skill_needs_ownership_or_sharing(self) -> None:
        people, names = ("a", "b", "c"), ("x", "y")
        for owner, viewer, name in itertools.product(people, people, names):
            for shared in (set(), {"x"}, {"y"}, {"x", "y"}):
                allowed = SkillVisibility.of([viewer], shared=shared).allows(_doc(owner, name))
                assert allowed is (owner == viewer or name in shared), (owner, viewer, name, shared)

    def test_sk4_only_the_seeder_and_pinned_content_read_everything(self) -> None:
        assert SkillVisibility.everyone().allows(_doc("anyone", "x"))
        assert not SkillVisibility.of([None]).allows(_doc("anyone", "x"))

    async def test_sk4_a_store_reads_as_its_acting_user_unless_told_otherwise(self) -> None:
        graph = FakeGraphProvider()
        await _seed(graph)

        assert {m.name for m in await GraphSkillStore(graph, ORG, BOB.key).list_skills()} == {"bob-notes"}  # type: ignore[arg-type]


class TestAssignment:
    async def test_sk5_an_editor_can_assign_only_their_own_skills_or_builtins(self) -> None:
        graph = FakeGraphProvider()
        await _seed(graph)
        logger = logging.getLogger(__name__)

        linked = await _create_skill_edges(
            "agent-1", ["bob-notes", "alice-report"], ORG, BOB.key, graph, logger  # type: ignore[arg-type]
        )

        assert linked == ["bob-notes"]
        edges = graph._edges.get(CollectionNames.AGENT_HAS_SKILL.value, [])
        assert [edge["skillName"] for edge in edges] == ["bob-notes"]
