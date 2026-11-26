"""Who may learn that a skill exists, and who uses it: every skills read route over HTTP.

The real router runs over the in-memory graph, with the scope dependency and a role lookup
that answers from the caller. Principals: the creator, a co-worker, an org admin, a member of
another org, an app acting as the co-worker, an OAuth token without `skill:read`, and a
service token. Skills: a private skill, the same private skill assigned to an agent shared
with the org (skills themselves cannot be shared), a seeded builtin (the org's), and a skill
that only says `builtin` in its frontmatter. Surfaces: usage, get, export, versions, one
version, a resource, the listing and search; and the in-use refusal of a delete.

A skill the caller cannot see must answer as a missing one: the same status, the same body,
and the same reads of the store. When the caller can see it, the usage names only the agents
the caller has access to and the skills it can see; the rest are counts.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes import skills as skills_routes
from tests.support.bound_org import bound_like_auth
from tests.support.skill_stores import skill_store
from tests.unit.api.routes.test_skills_custom_lifecycle import _LifecycleGraphProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response

ORG, OTHER_ORG = "org-1", "org-2"
USERS = {
    "owner": {"userId": "user-a", "orgId": ORG},
    "co_worker": {"userId": "user-b", "orgId": ORG},
    "admin": {"userId": "admin-1", "orgId": ORG},
    "other_org": {"userId": "user-x", "orgId": OTHER_ORG},
    "app_as_co_worker": {"userId": "user-b", "orgId": ORG, "isOAuth": True, "oauthScopes": ["skill:read"]},
    "token_without_scope": {"userId": "user-a", "orgId": ORG, "isOAuth": True, "oauthScopes": ["agent:read"]},
    "service_token": {"userId": "user-a", "orgId": ORG, "token_type": "scoped", "scopes": ["conversation:create"]},
}
ADMINS = {"admin-1"}
REFUSED_BY_SCOPE = {"token_without_scope", "service_token"}

PRIVATE, ON_SHARED_AGENT, BUILTIN, CLAIMED = "a-private", "a-on-shared-agent", "org-pack", "a-claimed"
SKILLS = (PRIVATE, ON_SHARED_AGENT, BUILTIN, CLAIMED)
_SECRET = "Secret instructions of user a"


def _md(name: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: Use when {name}\n{extra}---\n\n{_SECRET} for {name}.\n"


_REQUIRES_PACK = f"metadata:\n  agent-loop:\n    requires:\n      - {BUILTIN}\n"
_BUILTIN_FM = "metadata:\n  agent-loop:\n    source: builtin\n    pack_name: org-pack\n    pack_version: '1'\n"


async def _world(graph: _LifecycleGraphProvider) -> None:
    a = skill_store(graph, ORG, "user-a")
    b = skill_store(graph, ORG, "user-b")
    for name in (PRIVATE, ON_SHARED_AGENT, CLAIMED):
        await a.create_skill(name, _md(name), resources={"r.md": f"{_SECRET} resource"})
        await a.update_skill(name, _md(name).replace("Secret", "Secret v2"))
    await skill_store(graph, ORG, SEED_IDENTITY).create_skill(BUILTIN, _md(BUILTIN, _BUILTIN_FM), resources={"r.md": "pack"})
    await skill_store(graph, ORG, SEED_IDENTITY).update_skill(BUILTIN, _md(BUILTIN, _BUILTIN_FM).replace("Secret", "Secret v2"))
    graph._col("agentSkills")[f"{ORG}_{CLAIMED}"]["source"] = "builtin"
    await a.create_skill("a-wrapper", _md("a-wrapper", _REQUIRES_PACK))
    await b.create_skill("b-wrapper", _md("b-wrapper", _REQUIRES_PACK))
    graph.add_agent("agA", "Payroll agent of a", owner="user-a")
    graph.add_agent("agB", "Agent of b", owner="user-b")
    graph.add_agent("agC", "Agent of c", owner="user-c")
    graph.add_agent("agOrg", "Org agent", owner="user-c", org=ORG)
    for agent, skill in (
        ("agA", PRIVATE), ("agC", PRIVATE), ("agOrg", ON_SHARED_AGENT),
        ("agA", BUILTIN), ("agB", BUILTIN), ("agOrg", BUILTIN),
    ):
        await graph.batch_create_edges([{
            "_from": f"agentInstances/{agent}", "_to": f"agentSkills/{ORG}_{skill}", "createdAtTimestamp": 1,
        }], "agentHasSkill")


class _Recording:
    """The provider, with a log of every method the request called on it."""

    def __init__(self, inner: _LifecycleGraphProvider) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        def call(*args: object, **kwargs: object) -> object:
            self.calls.append(name)
            return target(*args, **kwargs)
        return call


def _app(graph: object) -> FastAPI:
    app = FastAPI()
    app.container = SimpleNamespace(
        graph_provider=AsyncMock(return_value=graph),
        retrieval_service=AsyncMock(return_value=None),
        logger=MagicMock(return_value=MagicMock()),
        config_service=MagicMock(return_value=MagicMock()),
    )

    @app.middleware("http")
    async def _auth(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        principal = request.headers.get("x-principal", "")
        request.state.user = dict(USERS[principal])
        with bound_like_auth(USERS[principal]):
            return await call_next(request)

    app.include_router(skills_routes.router, prefix="/api/v1/skills")
    return app


async def _role(request: Request, _config: object) -> CallerRole:
    admin = request.state.user.get("userId") in ADMINS
    return CallerRole(CallerRoleStatus.VALID, "admin" if admin else "member")


@pytest.fixture
async def world() -> AsyncIterator[SimpleNamespace]:
    graph = _LifecycleGraphProvider()
    await _world(graph)
    recording = _Recording(graph)
    with patch.object(skills_routes, "is_skills_enabled", new=AsyncMock(return_value=True)), \
         patch.object(skills_routes, "_get_user_key", new=AsyncMock(side_effect=lambda uid, *_: uid)), \
         patch.object(skills_routes, "sync_builtin_skills", new=AsyncMock()), \
         patch.object(skills_routes, "fetch_caller_role", new=AsyncMock(side_effect=_role)):
        transport = httpx.ASGITransport(app=_app(recording))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield SimpleNamespace(graph=graph, recording=recording, client=client)


async def _get(world: SimpleNamespace, principal: str, url: str, **kw: object) -> httpx.Response:
    return await world.client.get(url, headers={"x-principal": principal}, **kw)


def _may_see(principal: str, skill: str) -> bool:
    """The rule (`SkillVisibility`): a seeded builtin is the org's, anything else its creator's."""
    who = USERS[principal]
    if who["orgId"] != ORG:
        return False
    return skill == BUILTIN or who["userId"] == "user-a"


NAME_ROUTES = {
    "usage": "/api/v1/skills/{n}/usage",
    "get": "/api/v1/skills/{n}",
    "export": "/api/v1/skills/{n}/export",
    "versions": "/api/v1/skills/{n}/versions",
    "version": "/api/v1/skills/{n}/versions/1.0.0",
    "resource": "/api/v1/skills/{n}/resource?path=r.md",
}


@pytest.mark.parametrize("surface", list(NAME_ROUTES))
@pytest.mark.parametrize("skill", SKILLS)
@pytest.mark.parametrize("principal", list(USERS))
async def test_a_skill_the_caller_cannot_see_answers_as_a_missing_one(world, principal, skill, surface) -> None:
    url = NAME_ROUTES[surface]
    world.recording.calls.clear()
    seen = await _get(world, principal, url.format(n=skill))
    seen_calls = list(world.recording.calls)
    world.recording.calls.clear()
    missing = await _get(world, principal, url.format(n="no-such-skill"))
    missing_calls = list(world.recording.calls)

    if principal in REFUSED_BY_SCOPE:
        assert seen.status_code == missing.status_code == 403
        assert seen.json() == missing.json()
        assert seen_calls == missing_calls == []
        return
    if _may_see(principal, skill):
        assert seen.status_code == 200, (seen.status_code, seen.text)
        return
    # `versions` answers an empty history for a missing name; every other route is a 404.
    assert missing.status_code == (200 if surface == "versions" else 404)
    assert seen.status_code == missing.status_code, (seen.status_code, seen.text)
    assert seen.json() == json.loads(missing.text.replace("no-such-skill", skill))
    assert seen_calls == missing_calls, "an invisible skill costs other reads than a missing one"
    assert _SECRET not in seen.text and "agA" not in seen.text and "agC" not in seen.text


@pytest.mark.parametrize("principal", [p for p in USERS if p not in REFUSED_BY_SCOPE])
async def test_the_listing_and_search_show_only_what_the_caller_may_see(world, principal) -> None:
    listed = await _get(world, principal, "/api/v1/skills/")
    found = await _get(world, principal, "/api/v1/skills/search", params={"q": "Use when", "limit": 100})
    assert listed.status_code == found.status_code == 200
    listed_names = {s["name"] for s in listed.json()["skills"]}
    found_names = {r["skill"]["name"] for r in found.json()["results"]}
    for skill in SKILLS:
        assert (skill in listed_names) == _may_see(principal, skill), (skill, listed_names)
        assert skill not in found_names or _may_see(principal, skill), (skill, found_names)


def _agents(body: dict) -> set[str]:
    return {a["id"] for a in body["usedByAgents"]}


@pytest.mark.parametrize(("principal", "skill", "agents", "other_agents", "skills", "other_skills"), [
    # The creator: their own agent and the org's, not user c's private agent.
    ("owner", PRIVATE, {"agA"}, 1, [], 0),
    ("owner", ON_SHARED_AGENT, {"agOrg"}, 0, [], 0),
    # The org's builtin: each member sees their own agent and the org's, and their own dependant.
    ("owner", BUILTIN, {"agA", "agOrg"}, 1, ["a-wrapper"], 1),
    ("co_worker", BUILTIN, {"agB", "agOrg"}, 1, ["b-wrapper"], 1),
    ("app_as_co_worker", BUILTIN, {"agB", "agOrg"}, 1, ["b-wrapper"], 1),
    # An admin reads as a member here (as GET /{name}): no grant on a member's private agent.
    ("admin", BUILTIN, {"agOrg"}, 2, [], 2),
])
async def test_the_usage_names_only_agents_and_skills_the_caller_may_see(
    world, principal, skill, agents, other_agents, skills, other_skills,
) -> None:
    r = await _get(world, principal, f"/api/v1/skills/{skill}/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert _agents(body) == agents
    assert body["otherAgentCount"] == other_agents
    assert body["requiredBySkills"] == skills
    assert body["otherSkillCount"] == other_skills
    named = {a["name"] for a in body["usedByAgents"]}
    for hidden in {"agA", "agB", "agC", "agOrg"} - agents:
        assert hidden not in r.text
        assert world.graph._col("agentInstances")[hidden]["name"] not in named


async def test_a_co_worker_reads_nothing_of_a_private_skill_on_an_agent_they_can_open(world) -> None:
    """The agent is shared with the org; the skill on it is still its creator's."""
    r = await _get(world, "co_worker", f"/api/v1/skills/{ON_SHARED_AGENT}/usage")
    assert r.status_code == 404
    assert r.json() == {"detail": "Skill not found."}


async def test_the_in_use_refusal_counts_agents_the_caller_cannot_open(world) -> None:
    r = await world.client.delete(f"/api/v1/skills/{PRIVATE}", headers={"x-principal": "owner"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["usedByAgents"] == [{"id": "agA", "name": "Payroll agent of a"}]
    assert detail["otherAgentCount"] == 1
    assert detail["requiredBySkills"] == [] and detail["otherSkillCount"] == 0
    assert "agC" not in r.text and "Agent of c" not in r.text
    assert "2 agent(s)" in detail["message"]


async def test_a_skill_used_only_by_agents_the_caller_cannot_open_is_still_in_use(world) -> None:
    """The delete page reads the usage to decide on detach: the count must say it is in use."""
    graph = world.graph
    graph._edges["agentHasSkill"] = [e for e in graph._edges["agentHasSkill"] if "agA" not in e["_from"]]
    usage = (await _get(world, "owner", f"/api/v1/skills/{PRIVATE}/usage")).json()
    assert usage["usedByAgents"] == [] and usage["otherAgentCount"] == 1

    refused = await world.client.delete(f"/api/v1/skills/{PRIVATE}", headers={"x-principal": "owner"})
    assert refused.status_code == 409 and refused.json()["detail"]["otherAgentCount"] == 1
    deleted = await world.client.delete(
        f"/api/v1/skills/{PRIVATE}", params={"detach": "true"}, headers={"x-principal": "owner"},
    )
    assert deleted.status_code == 200
    assert f"{ORG}_{PRIVATE}" not in graph._col("agentSkills")
    assert not [e for e in graph._edges["agentHasSkill"] if e["_to"].endswith(f"_{PRIVATE}")]


async def test_a_skill_required_only_by_skills_the_caller_cannot_see_cannot_be_deleted(world) -> None:
    """Admin moderation of the org's dependency graph: a hidden dependant still blocks the delete."""
    store = skill_store(world.graph, ORG, "user-b")
    await store.create_skill("b-needs-a", _md("b-needs-a", f"metadata:\n  agent-loop:\n    requires:\n      - {CLAIMED}\n"))
    usage = (await _get(world, "owner", f"/api/v1/skills/{CLAIMED}/usage")).json()
    assert usage["requiredBySkills"] == [] and usage["otherSkillCount"] == 1
    r = await world.client.delete(f"/api/v1/skills/{CLAIMED}", params={"detach": "true"}, headers={"x-principal": "owner"})
    assert r.status_code == 409
    assert r.json()["detail"]["otherSkillCount"] == 1 and "b-needs-a" not in r.text
    assert f"{ORG}_{CLAIMED}" in world.graph._col("agentSkills")
