"""A run whose answer others read leaves out the asker's own hand-written skills, when the edition
says it is such a turn: the seeded builtins and the skills assigned to the run's agent stay."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills_wiring import build_skill_manager
from app.modules.agents.chat_extension import ChatExtension
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"


def _md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use {name} when needed\n---\n\nSteps for {name}.\n"


async def _graph() -> FakeGraphProvider:
    graph = FakeGraphProvider()
    await GraphSkillStore(graph, ORG, SEED_IDENTITY).create_skill("pdf-tools", _md("pdf-tools"))
    graph._col("agentSkills")[f"{ORG}_pdf-tools"]["source"] = "builtin"
    await GraphSkillStore(graph, ORG, "user-bob").create_skill("team-playbook", _md("team-playbook"))
    await GraphSkillStore(graph, ORG, "user-dana").create_skill("comp-bands", _md("comp-bands"), resources={"refs/a.md": "a"})
    return graph


class TestTheStore:
    async def test_without_own_skills_it_reads_only_builtins_and_the_agents_skills(self) -> None:
        store = GraphSkillStore(
            await _graph(), ORG, "user-dana", visibility_scope="user-dana", also_visible={"team-playbook"},
            own_skills=False,
        )

        assert {m.name for m in await store.list_skills()} == {"pdf-tools", "team-playbook"}
        assert await store.get_skill("comp-bands") is None
        assert await store.get_resource("comp-bands", "refs/a.md") is None

    async def test_a_skill_the_run_created_stays_readable_to_it(self) -> None:
        store = GraphSkillStore(await _graph(), ORG, "user-dana", visibility_scope="user-dana", own_skills=False)

        await store.create_skill("new-notes", _md("new-notes"))

        assert await store.get_skill("new-notes") is not None
        assert await store.get_skill("comp-bands") is None

    async def test_by_default_it_reads_the_persons_own_skills(self) -> None:
        store = GraphSkillStore(await _graph(), ORG, "user-dana", visibility_scope="user-dana")

        assert {m.name for m in await store.list_skills()} == {"pdf-tools", "comp-bands"}


class TestTheRunsManager:
    async def _built_with(self, reads_own: bool) -> dict:
        extension = MagicMock()
        extension.reads_own_skills = MagicMock(return_value=reads_own)
        context = SimpleNamespace(
            graph_provider=MagicMock(), org_id=ORG, user_id="user-dana", user_key="key-dana", retrieval_service=None,
            agent_skills=None, agent_key=None, tool_state={},
        )
        with patch(
            "app.agents.agent_loop.skills.manager_factory._build_manager", new_callable=AsyncMock,
        ) as build, patch("app.agents.agent_loop.skills.manager_factory.schedule_builtin_skill_reconcile"), patch(
            "app.modules.agents.chat_extension.get_chat_extension", return_value=extension,
        ):
            await build_skill_manager(context, MagicMock())
        extension.reads_own_skills.assert_called_once_with(context)
        return build.call_args.kwargs

    async def test_a_turn_others_read_builds_the_store_without_the_askers_own_skills(self) -> None:
        assert (await self._built_with(False))["own_skills"] is False

    async def test_any_other_turn_keeps_them(self) -> None:
        assert (await self._built_with(True))["own_skills"] is True

    def test_the_default_edition_keeps_a_runs_own_skills(self) -> None:
        assert ChatExtension().reads_own_skills(MagicMock()) is True
