"""Which skills an agent can be linked to: the caller's own, and builtins the seeder wrote.
And what `GET /agent/{id}` shows of a skill on it that the caller cannot see."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes.agent import _create_skill_edges, _skills_as_the_caller_may_see_them
from app.core.org_context import MissingOrgContext, OrgContext, bind

_LOG = logging.getLogger("test_agent_skill_edges")


def _doc(name: str, *, source: str = "manual", created_by: str = "user-a-key") -> dict:
    return {"orgId": "org-1", "name": name, "source": source, "createdBy": created_by, "status": "active"}


async def _linked(docs: dict[str, dict], user_key: str, names: list[str], org: str = "org-1") -> list[str]:
    provider = _provider(docs)
    provider.batch_create_edges = AsyncMock(return_value=True)
    with bind(_caller_in(org)):
        return await _create_skill_edges("agent-1", names, user_key, provider, _LOG)


async def test_the_caller_links_their_own_skill() -> None:
    docs = {"org-1_mine": _doc("mine")}
    assert await _linked(docs, "user-a-key", ["mine"]) == ["mine"]


async def test_a_co_workers_skill_is_not_linked() -> None:
    docs = {"org-1_theirs": _doc("theirs", created_by="user-b-key")}
    assert await _linked(docs, "user-a-key", ["theirs"]) == []


async def test_a_builtin_the_seeder_wrote_is_linked_for_everyone() -> None:
    docs = {"org-1_pack": _doc("pack", source="builtin", created_by=SEED_IDENTITY)}
    assert await _linked(docs, "user-a-key", ["pack"]) == ["pack"]


async def test_a_skill_that_only_says_builtin_is_not_linked_for_a_co_worker() -> None:
    docs = {"org-1_claimed": _doc("claimed", source="builtin", created_by="user-b-key")}
    assert await _linked(docs, "user-a-key", ["claimed"]) == []
    assert await _linked(docs, "user-b-key", ["claimed"]) == ["claimed"]


async def test_a_disabled_or_deprecated_skill_is_not_newly_linked() -> None:
    docs = {
        "org-1_off": {**_doc("off"), "status": "disabled"},
        "org-1_old": {**_doc("old"), "status": "deprecated"},
        "org-1_legacy": {k: v for k, v in _doc("legacy").items() if k != "status"},
    }
    assert await _linked(docs, "user-a-key", ["off", "old", "legacy"]) == ["legacy"]


async def test_skills_are_linked_as_they_stand_in_the_bound_org_inside_the_agents_write() -> None:
    docs = {"org-1_mine": _doc("mine"), "org-2_mine": {**_doc("mine"), "orgId": "org-2"}}
    provider = _provider(docs)
    provider.batch_create_edges = AsyncMock(return_value=True)
    with bind(_caller_in("org-2")):
        linked = await _create_skill_edges("agent-1", ["mine"], "user-a-key", provider, _LOG, transaction="tx-1")
    assert linked == ["mine"]
    assert [(c.args[0], c.kwargs.get("transaction")) for c in provider.get_document.await_args_list] == [("org-2_mine", "tx-1")]
    (edges, _collection), kwargs = provider.batch_create_edges.await_args
    assert [e["_to"] for e in edges] == ["agentSkills/org-2_mine"] and kwargs["transaction"] == "tx-1"
    assert await _linked(docs, "user-a-key", ["mine"], org="org-3") == []


async def test_skills_are_not_linked_without_a_bound_org() -> None:
    provider = _provider({"org-1_mine": _doc("mine")})
    provider.batch_create_edges = AsyncMock(return_value=True)
    with pytest.raises(MissingOrgContext):
        await _create_skill_edges("agent-1", ["mine"], "user-a-key", provider, _LOG)
    provider.get_document.assert_not_awaited()
    provider.batch_create_edges.assert_not_awaited()


def test_the_route_links_with_the_skill_stores_scoped_read() -> None:
    import inspect

    from app.api.routes import agent

    source = inspect.getsource(agent._create_skill_edges)
    assert "GraphSkillStore(graph_provider, org_id, user_key)" in source
    assert "org_id = current_org_id()" in source
    assert "await store.get_status(name, transaction=transaction)" in source
    assert "get_document" not in source


_ENTRY = {
    "name": "", "description": "d", "category": "c", "subcategory": "s", "version": "1.0.3",
    "status": "deprecated", "deprecatedReason": "old", "replacedBy": "newer",
}


def _provider(docs: dict[str, dict]) -> MagicMock:
    provider = MagicMock()
    provider.get_document = AsyncMock(side_effect=lambda key, _collection, transaction=None: docs.get(key))
    provider.get_user_team_roles = AsyncMock(return_value=[{"teamId": "team-1", "role": "READER"}])
    return provider


def _caller_in(org: str) -> OrgContext:
    return OrgContext(org_id=org, user_id="user-a", kind="user", source="jwt", request_id="-")


async def _shown(docs: dict[str, dict], user_key: str, names: list[str], org: str = "org-1") -> dict[str, dict]:
    entries = [{**_ENTRY, "name": n} for n in names]
    with bind(_caller_in(org)):
        return {s["name"]: s for s in await _skills_as_the_caller_may_see_them(entries, user_key, _provider(docs))}


async def test_an_agent_shows_the_callers_own_skills_and_builtins_whole() -> None:
    docs = {"org-1_mine": _doc("mine"), "org-1_pack": _doc("pack", source="builtin", created_by=SEED_IDENTITY)}
    shown = await _shown(docs, "user-a-key", ["mine", "pack"])
    assert shown == {"mine": {**_ENTRY, "name": "mine"}, "pack": {**_ENTRY, "name": "pack"}}


async def test_an_agent_shows_a_skill_the_caller_cannot_see_by_name_description_status_and_replacement() -> None:
    docs = {
        "org-1_theirs": _doc("theirs", created_by="user-b-key"),
        "org-1_claimed": _doc("claimed", source="builtin", created_by="user-b-key"),
        "org-1_elsewhere": {**_doc("elsewhere"), "orgId": "org-2"},
    }
    shown = await _shown(docs, "user-a-key", ["theirs", "claimed", "elsewhere", "gone"])
    for name in ("theirs", "claimed", "elsewhere", "gone"):
        assert shown[name] == {"name": name, "description": "d", "status": "deprecated", "replacedBy": "newer"}


async def test_an_agent_shows_a_teams_skill_whole_to_its_members() -> None:
    docs = {
        "org-1_ours": {**_doc("ours", created_by="user-b-key"), "scope": "team", "teamId": "team-1"},
        "org-1_others": {**_doc("others", created_by="user-b-key"), "scope": "team", "teamId": "team-2"},
    }
    shown = await _shown(docs, "user-a-key", ["ours", "others"])
    assert shown["ours"] == {**_ENTRY, "name": "ours"}
    assert set(shown["others"]) == {"name", "description", "status", "replacedBy"}


async def test_an_agent_shows_skills_as_they_stand_in_the_bound_org() -> None:
    docs = {"org-1_mine": _doc("mine"), "org-2_mine": {**_doc("mine"), "orgId": "org-2"}}
    shown = await _shown(docs, "user-a-key", ["mine"], org="org-2")
    assert shown == {"mine": {**_ENTRY, "name": "mine"}}
    provider = _provider(docs)
    with bind(_caller_in("org-3")):
        cut = await _skills_as_the_caller_may_see_them([{**_ENTRY, "name": "mine"}], "user-a-key", provider)
    assert cut == [{"name": "mine", "description": "d", "status": "deprecated", "replacedBy": "newer"}]
    assert [c.args[0] for c in provider.get_document.await_args_list] == ["org-3_mine"]


async def test_skills_on_an_agent_are_not_read_without_a_bound_org() -> None:
    provider = _provider({"org-1_mine": _doc("mine")})
    with pytest.raises(MissingOrgContext):
        await _skills_as_the_caller_may_see_them([{**_ENTRY, "name": "mine"}], "user-a-key", provider)
    provider.get_document.assert_not_awaited()


def test_the_agent_routes_that_show_skills_cut_them_with_the_skill_stores_scoped_read() -> None:
    import inspect

    from app.api.routes import agent

    source = inspect.getsource(agent._skills_as_the_caller_may_see_them)
    assert "SkillVisibility.of([user_key], teams=teams)" in source
    assert "await store.exists(name)" in source
    for route in (agent.get_agent, agent.get_agent_version):
        assert "_skills_as_the_caller_may_see_them(" in inspect.getsource(route)
