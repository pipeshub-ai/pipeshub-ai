"""`GraphSkillStore.get_referential_usage` answers for the store's caller, not for the org.

The store is where every surface reads usage (the REST usage route, the in-use refusal of a
delete, the `skill_manage` tool), so the visibility rule is applied there once: a skill the
caller cannot see has no usage, and agents and skills the caller cannot see are counted, never
named. The counts keep the delete guard whole.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.modules.providers.skills.base import SkillInUseError
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.graph_store import (
    GraphSkillStore,
    SkillVisibility,
    is_seeded_builtin,
)
from tests.support.skill_stores import skill_store
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider
from tests.unit.agents.adapter.test_skills_no_overwrite import _NoIndex

ORG = "org-1"


def _md(name: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: Use when {name}\n{extra}---\n\nBody of {name}.\n"


def _requires(name: str) -> str:
    return f"metadata:\n  agent-loop:\n    requires:\n      - {name}\n"


def _scoped(graph: FakeGraphProvider, user: str) -> GraphSkillStore:
    return skill_store(graph, ORG, user, visibility_scope=user)


def _manager(store: GraphSkillStore) -> SkillManager:
    return SkillManager(store=store, index=_NoIndex(), tracker=None)  # type: ignore[arg-type]


def _link(graph: FakeGraphProvider, agent: str, skill: str) -> None:
    graph._edges.setdefault("agentHasSkill", []).append({
        "from_id": agent, "from_collection": "agentInstances", "to_id": f"{ORG}_{skill}", "to_collection": "agentSkills",
    })


@pytest.fixture
async def graph() -> FakeGraphProvider:
    g = FakeGraphProvider()
    await _scoped(g, "user-a").create_skill("a-private", _md("a-private"))
    await _scoped(g, "user-b").create_skill("b-needs-a", _md("b-needs-a", _requires("a-private")))
    g.add_agent("agA", "Agent of a", owner="user-a")
    g.add_agent("agB", "Agent of b", owner="user-b")
    g.add_agent("agOrg", "Org agent", owner="user-c", org=ORG)
    for agent in ("agA", "agB", "agOrg"):
        _link(g, agent, "a-private")
    return g


def test_the_rule() -> None:
    seeded = {"source": "builtin", "createdBy": SEED_IDENTITY}
    claimed = {"source": "builtin", "createdBy": "user-a"}
    own = {"source": "manual", "createdBy": "user-a"}
    assert is_seeded_builtin(seeded) and not is_seeded_builtin(claimed)
    assert SkillVisibility.of(["user-b"]).allows(seeded)
    assert SkillVisibility.of(["user-a"]).allows(own) and not SkillVisibility.of(["user-b"]).allows(own)
    assert SkillVisibility.of(["user-a"]).allows(claimed) and not SkillVisibility.of(["user-b"]).allows(claimed)
    assert SkillVisibility.everyone().allows(own)


async def test_a_co_worker_reads_no_usage_of_a_private_skill(graph) -> None:
    usage = await _scoped(graph, "user-b").get_referential_usage("a-private")
    assert usage.model_dump() == {"used_by_agents": [], "required_by_skills": [], "other_agent_count": 0, "other_skill_count": 0}


async def test_another_org_reads_no_usage(graph) -> None:
    for store in (skill_store(graph, "org-2", "user-x", visibility_scope="user-x"), skill_store(graph, "org-2", "user-x")):
        assert (await store.get_referential_usage("a-private")).model_dump()["other_agent_count"] == 0


async def test_the_creator_reads_their_agents_and_the_orgs_and_counts_the_rest(graph) -> None:
    usage = await _scoped(graph, "user-a").get_referential_usage("a-private")
    assert {a["id"] for a in usage.used_by_agents} == {"agA", "agOrg"}
    assert usage.other_agent_count == 1
    assert usage.required_by_skills == [] and usage.other_skill_count == 1


@pytest.mark.parametrize("store_kind", ["admin", "runtime"])
async def test_an_org_wide_store_still_names_only_agents_its_user_may_open(graph, store_kind) -> None:
    """An admin (vetted by the route) and the agent runtime see every skill of the org, but
    neither has a grant on a member's private agent."""
    store = skill_store(graph, ORG, "admin-1", is_admin=True) if store_kind == "admin" else skill_store(graph, ORG, "user-z")
    usage = await store.get_referential_usage("a-private")
    assert [a["id"] for a in usage.used_by_agents] == ["agOrg"]
    assert usage.other_agent_count == 2
    assert usage.required_by_skills == ["b-needs-a"] and usage.other_skill_count == 0


async def test_a_deleted_agent_is_counted_not_named(graph) -> None:
    graph._col("agentInstances")["agA"]["isDeleted"] = True
    usage = await _scoped(graph, "user-a").get_referential_usage("a-private")
    assert [a["id"] for a in usage.used_by_agents] == ["agOrg"] and usage.other_agent_count == 2


async def test_a_name_created_again_during_the_read_reports_nothing(graph) -> None:
    """The edges are read by name: once the checked skill is gone, they are another skill's."""
    store = _scoped(graph, "user-a")
    inner = graph.get_edges_to_node

    async def recreated(node_id: str, edge_collection: str, transaction: str | None = None) -> list[dict]:
        if edge_collection == "agentHasSkill" and f"{ORG}_a-private" in graph._col("agentSkills") and \
                graph._col("agentSkills")[f"{ORG}_a-private"]["createdBy"] == "user-a":
            assert await skill_store(graph, ORG, "user-a").delete_skill("a-private")
            await _scoped(graph, "user-a").create_skill("a-private", _md("a-private"))
            graph.add_agent("agNew", "Agent made after the check", owner="user-a")
            _link(graph, "agNew", "a-private")
        return await inner(node_id, edge_collection, transaction)

    graph.get_edges_to_node = recreated  # type: ignore[method-assign]
    usage = await store.get_referential_usage("a-private")
    assert usage.model_dump() == {"used_by_agents": [], "required_by_skills": [], "other_agent_count": 0, "other_skill_count": 0}


async def test_the_delete_guard_counts_what_the_caller_cannot_see(graph) -> None:
    graph._edges["agentHasSkill"] = [e for e in graph._edges["agentHasSkill"] if e["from_id"] == "agB"]
    graph._edges["agentSkillRelation"] = []
    manager = _manager(_scoped(graph, "user-a"))
    with pytest.raises(SkillInUseError) as exc:
        await manager.delete("a-private")
    assert exc.value.used_by_agents == [] and exc.value.other_agent_count == 1
    assert "agB" not in str(exc.value) and "Agent of b" not in str(exc.value)
    assert await manager.delete("a-private", detach=True)


async def test_a_dependant_the_caller_cannot_see_still_blocks_the_delete(graph) -> None:
    manager = _manager(_scoped(graph, "user-a"))
    with pytest.raises(SkillInUseError) as exc:
        await manager.delete("a-private", detach=True)
    assert exc.value.required_by_skills == [] and exc.value.other_skill_count == 1
    assert "b-needs-a" not in str(exc.value)
    assert f"{ORG}_a-private" in graph._col("agentSkills")


async def test_a_requires_on_a_name_nobody_holds_claims_no_skill_created_later(graph) -> None:
    """A hidden skill that requires a name before anyone holds it must not count against the
    skill a co-worker creates under that name: the creator would see a dependant and could
    never delete their own skill. Once the dependant is saved again, the name exists and it counts."""
    a, b = _scoped(graph, "user-a"), _scoped(graph, "user-b")
    await a.create_skill("a-secret-dep", _md("a-secret-dep", _requires("helper-3")))
    await b.create_skill("helper-3", _md("helper-3"))
    usage = await b.get_referential_usage("helper-3")
    assert (usage.required_by_skills, usage.other_skill_count) == ([], 0)

    await a.update_skill("a-secret-dep", _md("a-secret-dep", _requires("helper-3")))
    assert (await b.get_referential_usage("helper-3")).other_skill_count == 1
    await a.update_skill("a-secret-dep", _md("a-secret-dep"))
    assert await _manager(b).delete("helper-3", detach=True)


async def test_a_requires_counts_only_against_the_creation_it_was_written_for(graph) -> None:
    await _scoped(graph, "user-b").create_skill("helper", _md("helper"))
    await _scoped(graph, "user-a").create_skill("a-dep", _md("a-dep", _requires("helper")))
    store = _scoped(graph, "user-b")
    assert (await store.get_referential_usage("helper")).other_skill_count == 1
    # The name held by another creation that kept the edge (ArangoDB and PostgreSQL keep an
    # edge whatever node its end names).
    graph._col("agentSkills")[f"{ORG}_helper"]["generation"] = "a-later-creation"
    assert (await store.get_referential_usage("helper")).other_skill_count == 0


@pytest.mark.parametrize(("edge_at", "counts"), [(5, False), (10, True), (11, True)])
async def test_a_requires_edge_from_before_generations_counts_from_the_creation_on(graph, edge_at, counts) -> None:
    await _scoped(graph, "user-b").create_skill("helper", _md("helper"))
    graph._col("agentSkills")[f"{ORG}_helper"]["createdAtTimestamp"] = 10
    graph._edges.setdefault("agentSkillRelation", []).append({
        "from_id": f"{ORG}_a-private", "from_collection": "agentSkills", "to_id": f"{ORG}_helper",
        "to_collection": "agentSkills", "type": "requires", "createdAtTimestamp": edge_at,
    })
    assert (await _scoped(graph, "user-b").get_referential_usage("helper")).other_skill_count == int(counts)


async def test_a_co_workers_delete_through_the_manager_learns_nothing(graph) -> None:
    """The guard reads usage before the store's visibility check; it must not refuse with
    the creator's usage (that refusal would say the skill exists and who uses it)."""
    manager = _manager(_scoped(graph, "user-b"))
    assert await manager.delete("a-private") is False
    assert f"{ORG}_a-private" in graph._col("agentSkills")
