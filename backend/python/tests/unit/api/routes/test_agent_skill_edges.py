"""Which skills an agent can be linked to: the caller's own, and builtins the seeder wrote.
And what `GET /agent/{id}` shows of a skill on it that the caller cannot see."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes.agent import _create_skill_edges, _skills_as_the_caller_may_see_them

_LOG = logging.getLogger("test_agent_skill_edges")


def _doc(name: str, *, source: str = "manual", created_by: str = "user-a-key") -> dict:
    return {"orgId": "org-1", "name": name, "source": source, "createdBy": created_by, "status": "active"}


async def _linked(docs: dict[str, dict], user_key: str, names: list[str]) -> list[str]:
    provider = MagicMock()
    provider.get_document = AsyncMock(side_effect=lambda key, _collection, transaction=None: docs.get(key))
    provider.batch_create_edges = AsyncMock(return_value=True)
    return await _create_skill_edges("agent-1", names, "org-1", user_key, provider, _LOG)


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


_ENTRY = {
    "name": "", "description": "d", "category": "c", "subcategory": "s", "version": "1.0.3",
    "status": "deprecated", "deprecatedReason": "old", "replacedBy": "newer",
}


async def _shown(docs: dict[str, dict], user_key: str, names: list[str], org: str = "org-1") -> dict[str, dict]:
    provider = MagicMock()
    provider.get_document = AsyncMock(side_effect=lambda key, _collection, transaction=None: docs.get(key))
    provider.get_user_team_roles = AsyncMock(return_value=[{"teamId": "team-1", "role": "READER"}])
    entries = [{**_ENTRY, "name": n} for n in names]
    return {s["name"]: s for s in await _skills_as_the_caller_may_see_them(entries, org, user_key, provider)}


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


def test_the_agent_routes_that_show_skills_cut_them() -> None:
    import inspect

    from app.api.routes import agent

    for route in (agent.get_agent, agent.get_agent_version):
        assert "_skills_as_the_caller_may_see_them(" in inspect.getsource(route)
