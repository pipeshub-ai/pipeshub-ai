"""The default skill store (`graph_store.GraphSkillStore`, which `edition_services.skill_store` opens by
default) and its rule (`graph_store.skill_visible_to`): a builtin-sourced skill is the whole org's, any
other skill its creator's. A run's store also sees the skills its agent was given and those it created,
and tells the answer's label where each skill came from."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.skills import graph_store
from app.agents.agent_loop.skills.graph_store import (
    GraphSkillStore,
    SkillVisibility,
    skill_visible_to,
)
from app.agents.agent_loop.skills.ledger_manager import SkillOrigins
from app.modules.agents.privacy.ledger import SkillOrigin
from app.modules.agents.service.builders import _create_skill_edges
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"


def _md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use {name} when needed\n---\n\nSteps for {name}.\n"


class TestTheRule:
    @pytest.mark.parametrize(
        ("doc", "viewer", "account_id", "visible"),
        [
            ({"source": "builtin", "createdBy": "someone"}, "key-dana", None, True),
            ({"source": "manual", "createdBy": "key-dana"}, "key-dana", None, True),
            ({"source": "manual", "createdBy": "acct-dana"}, "key-dana", "acct-dana", True),
            ({"source": "manual", "createdBy": "key-carol"}, "key-dana", "acct-dana", False),
            ({"source": "manual", "createdBy": "key-carol"}, None, None, True),
        ],
        ids=["a builtin", "their own", "their own, from an older run", "a co-worker's", "the org-wide view"],
    )
    def test_who_may_see_a_skill(self, doc: dict, viewer: str | None, account_id: str | None, visible: bool) -> None:
        assert skill_visible_to(doc, viewer, account_id) is visible

    def test_the_store_reads_with_the_rule(self) -> None:
        # The store asks its rule with the reader's visibility; for one viewer it is the module's rule.
        for doc in ({"source": "builtin", "createdBy": "x"}, {"createdBy": "key-dana"}, {"createdBy": "key-carol"}):
            assert GraphSkillStore.visible_to(doc, SkillVisibility.of(["key-dana"])) == skill_visible_to(doc, "key-dana")
        assert GraphSkillStore.is_builtin is graph_store.is_builtin


class TestARunsStore:
    @staticmethod
    async def _graph() -> FakeGraphProvider:
        graph = FakeGraphProvider()
        await GraphSkillStore(graph, ORG, "key-carol").create_skill("team-playbook", _md("team-playbook"))
        await GraphSkillStore(graph, ORG, "key-carol").create_skill("carol-notes", _md("carol-notes"))
        await GraphSkillStore(graph, ORG, "key-dana").create_skill("dana-notes", _md("dana-notes"))
        await GraphSkillStore(graph, ORG, "seeder").create_skill("pdf-tools", _md("pdf-tools"))
        graph._col("agentSkills")[f"{ORG}_pdf-tools"]["source"] = "builtin"
        return graph

    async def test_it_sees_the_builtins_its_own_and_its_agents_skills(self) -> None:
        origins = SkillOrigins()
        store = GraphSkillStore(
            await self._graph(), ORG, "key-dana", visibility=SkillVisibility.of(["key-dana"]),
            also_visible={"team-playbook"}, origins=origins,
        )

        assert {m.name for m in await store.list_skills()} == {"pdf-tools", "dana-notes", "team-playbook"}
        assert await store.get_skill("carol-notes") is None
        assert (origins.of("pdf-tools"), origins.of("dana-notes"), origins.of("team-playbook")) == (
            SkillOrigin.BUILTIN, SkillOrigin.PERSON, SkillOrigin.AGENT,
        )

    async def test_a_run_others_read_leaves_out_the_persons_own_but_keeps_what_it_created(self) -> None:
        origins = SkillOrigins()
        graph = await self._graph()
        store = GraphSkillStore(
            graph, ORG, "key-dana", visibility=SkillVisibility.of(["key-dana"]), also_visible={"team-playbook"},
            own_skills=False, origins=origins,
        )
        await store.create_skill("fresh", _md("fresh"))

        assert {m.name for m in await store.list_skills()} == {"pdf-tools", "team-playbook", "fresh"}
        assert origins.of("fresh") is SkillOrigin.RUN

    async def test_a_name_created_again_by_someone_else_is_not_the_runs(self) -> None:
        graph = await self._graph()
        store = GraphSkillStore(graph, ORG, "key-dana", visibility=SkillVisibility.of(["key-dana"]), own_skills=False)
        await store.create_skill("fresh", _md("fresh"))
        graph._col("agentSkills")[f"{ORG}_fresh"].update(createdBy="key-carol", createdAtTimestamp=1)

        assert await store.get_skill("fresh") is None


class TestAnAgentsSkills:
    @staticmethod
    def _graph(docs: dict[str, dict[str, Any]]) -> MagicMock:
        graph = MagicMock()
        graph.get_document = AsyncMock(side_effect=lambda key, *_a, **_k: docs.get(key))
        graph.batch_create_edges = AsyncMock(return_value=True)
        return graph

    async def test_the_builder_links_what_the_editions_rule_lets_the_editor_see(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.edition_services.skill_visible_to", skill_visible_to)
        docs = {
            f"{ORG}_shared-pack": {"orgId": ORG, "source": "builtin", "createdBy": "key-carol", "status": "active"},
            f"{ORG}_carol-notes": {"orgId": ORG, "source": "manual", "createdBy": "key-carol", "status": "active"},
            f"{ORG}_dana-notes": {"orgId": ORG, "source": "manual", "createdBy": "key-dana", "status": "active"},
        }
        linked = await _create_skill_edges(
            "agent-1", ["shared-pack", "carol-notes", "dana-notes"], ORG, "key-dana", self._graph(docs), MagicMock(),
        )

        assert linked == ["shared-pack", "dana-notes"]
