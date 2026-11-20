"""The run's skill manager tells the request's ledger about every skill it hands back, with
where the skill came from, which the store records as it reads or creates it."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.modules.providers.skills.base import SkillInUseError
from app.agent_loop_lib.modules.providers.skills.governor import AutoApproveGovernor
from app.agent_loop_lib.modules.providers.skills.manager import (
    SkillManager,
    SkillManagerConfig,
)
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.agent_loop.skills.graph_tracker import GraphUsageTracker
from app.agents.agent_loop.skills.ledger_manager import (
    LedgerSkillManager,
    SkillOrigins,
)
from app.agents.agent_loop.skills.semantic_index import SemanticSkillIndex
from app.agents.agent_loop.skills_wiring import catalog_size
from app.modules.agents.privacy.ledger import LabelLedger, SkillOrigin, bind_ledger
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

ORG = "org-1"


class _Recorder(LabelLedger):
    labels = True

    def __init__(self) -> None:
        self.skills: list[tuple[str, SkillOrigin, str | None]] = []

    def skill_shown(self, name: str, origin: SkillOrigin, agent_key: str | None = None) -> None:
        self.skills.append((name, origin, agent_key))


def _md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use {name} when needed\n---\n\nSteps for {name}.\n"


async def _graph() -> FakeGraphProvider:
    graph = FakeGraphProvider()
    await GraphSkillStore(graph, ORG, SEED_IDENTITY).create_skill("pdf-tools", _md("pdf-tools"))
    graph._col("agentSkills")[f"{ORG}_pdf-tools"]["source"] = "builtin"
    await GraphSkillStore(graph, ORG, "user-bob").create_skill("team-playbook", _md("team-playbook"))
    await GraphSkillStore(graph, ORG, "user-dana").create_skill(
        "comp-bands", _md("comp-bands"), resources={"refs/a.md": "a"},
    )
    return graph


async def _run_manager(graph: FakeGraphProvider, **store: Any) -> tuple[LedgerSkillManager, SkillOrigins]:  # noqa: ANN401
    origins = SkillOrigins()
    store_ = GraphSkillStore(graph, ORG, "user-dana", visibility_scope="user-dana", origins=origins, **store)
    manager = SkillManager(
        store=store_, index=SemanticSkillIndex(None), tracker=GraphUsageTracker(graph, ORG, "user-dana"),
        governor=AutoApproveGovernor(), config=SkillManagerConfig(),
    )
    await manager.start()
    return LedgerSkillManager(manager, origins, agent_key="agent-1"), origins


class TestWhereASkillCameFrom:
    async def test_the_store_records_each_skills_origin_as_it_reads_it(self) -> None:
        _manager, origins = await _run_manager(await _graph(), also_visible={"team-playbook"})

        assert origins.of("pdf-tools") is SkillOrigin.BUILTIN
        assert origins.of("team-playbook") is SkillOrigin.AGENT
        assert origins.of("comp-bands") is SkillOrigin.PERSON

    async def test_a_name_it_never_read_counts_as_a_persons_own(self) -> None:
        assert SkillOrigins().of("anything") is SkillOrigin.PERSON

    async def test_a_skill_the_run_created_is_its_own(self) -> None:
        manager, origins = await _run_manager(await _graph())

        await manager.create("new-notes", _md("new-notes"))

        assert origins.of("new-notes") is SkillOrigin.RUN

    async def test_a_name_whose_skill_changed_keeps_the_most_restrictive_origin_it_had(self) -> None:
        origins = SkillOrigins()

        origins.record("x", SkillOrigin.PERSON)
        origins.record("x", SkillOrigin.BUILTIN)
        origins.record("y", SkillOrigin.AGENT)
        origins.record("y", SkillOrigin.PERSON)

        assert origins.of("x") is SkillOrigin.PERSON and origins.of("y") is SkillOrigin.PERSON

    async def test_a_builtin_whose_source_is_claimed_by_its_text_alone_is_a_persons(self) -> None:
        graph = await _graph()
        graph._col("agentSkills")[f"{ORG}_comp-bands"]["source"] = "builtin"

        _manager, origins = await _run_manager(graph)

        assert origins.of("comp-bands") is SkillOrigin.PERSON


class TestWhatTheLedgerIsTold:
    @pytest.mark.parametrize(
        ("read", "named"),
        [
            (lambda m: m.catalog_snapshot(), {"pdf-tools", "comp-bands"}),
            (lambda m: m.list_skills(), {"pdf-tools", "comp-bands"}),
            (lambda m: m.search("comp bands"), {"comp-bands"}),
            (lambda m: m.get_skill("comp-bands"), {"comp-bands"}),
            (lambda m: m.activate_skill("comp-bands"), {"comp-bands"}),
            (lambda m: m.load_resource("comp-bands", "refs/a.md"), {"comp-bands"}),
            (lambda m: m.get_resources("comp-bands"), {"comp-bands"}),
            (lambda m: m.evaluate_skill_health("comp-bands"), {"comp-bands"}),
        ],
    )
    async def test_every_read_names_the_skills_it_returned(self, read: Any, named: set[str]) -> None:  # noqa: ANN401
        manager, _origins = await _run_manager(await _graph())
        recorder = _Recorder()

        with bind_ledger(recorder):
            result = read(manager)
            if hasattr(result, "__await__"):
                await result

        assert {name for name, _origin, _agent in recorder.skills} >= named
        assert {name for name, _origin, _agent in recorder.skills} <= {"pdf-tools", "comp-bands"}
        assert all(agent == "agent-1" for _name, _origin, agent in recorder.skills)

    async def test_a_delete_refused_for_its_users_names_them_so_counts_as_the_askers(self) -> None:
        class _InUse:
            async def delete(self, name: str, *, detach: bool = False) -> bool:
                raise SkillInUseError(name, used_by_agents=[{"id": "a1", "name": "Support Bot"}], required_by_skills=[])

        recorder = _Recorder()
        with bind_ledger(recorder), pytest.raises(SkillInUseError):
            await LedgerSkillManager(_InUse(), SkillOrigins()).delete("pdf-tools")

        assert recorder.skills == [("pdf-tools", SkillOrigin.PERSON, None)]

    async def test_counting_the_catalog_for_a_log_tells_the_ledger_nothing(self) -> None:
        manager, _origins = await _run_manager(await _graph())
        recorder = _Recorder()

        with bind_ledger(recorder):
            size = catalog_size(manager)

        assert size == 2 and recorder.skills == []

    async def test_writes_pass_straight_through(self) -> None:
        manager, _origins = await _run_manager(await _graph())
        recorder = _Recorder()

        with bind_ledger(recorder):
            await manager.patch("comp-bands", "Steps", "Updated steps")

        assert recorder.skills == []
