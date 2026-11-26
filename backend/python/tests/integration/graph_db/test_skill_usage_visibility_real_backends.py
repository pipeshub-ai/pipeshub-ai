"""Who may read a skill's usage, over HTTP with the real role lookup, on a real Neo4j and a real ArangoDB.

The real skills router runs over the real provider, whose `check_agent_permission` decides which
agents a caller may open. World: user a's private skill, assigned to a's agent, to an agent of
user c that a cannot open, and to an agent shared with the org; the org's seeded builtin,
assigned to agents of a, of b and of the org, and required by a skill of a and a skill of b.

A member reading the usage of a co-worker's private skill gets the 404 a missing skill gets,
with the same body; so do an org admin (as `GET /{name}`) and another org. A caller who may see
the skill reads only the agents it may open and the skills it can see; the rest are counts,
and the in-use refusal of a delete says the same. A `requires` counts only against the skill
it was written for, never against one created later under a name nobody held. An agent the
caller can open shows a skill it cannot see by name, description, status and replacement only
(`GET /agent/{id}`).

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD. SKILLS_IT_NODE_PORT moves the fake Node (default 20231).
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes import agent as agent_routes
from app.config.constants.arangodb import CollectionNames
from tests.integration.graph_db.test_skills_rest_permissions_real_backends import (  # noqa: F401 (fixtures)
    FM,
    _app,
    md,
    node,
    provider,
)
from tests.support.skill_stores import skill_store

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

_USERS = CollectionNames.USERS.value
_ORGS = CollectionNames.ORGS.value
_AGENTS = CollectionNames.AGENT_INSTANCES.value
_SKILLS = CollectionNames.AGENT_SKILLS.value
_PERMISSION = CollectionNames.PERMISSION.value
_HAS_SKILL = CollectionNames.AGENT_HAS_SKILL.value
_REQUIRES_PACK = "metadata:\n  agent-loop:\n    requires:\n      - it-pack\n"
# What `GET /agent/{id}` shows of a skill on the agent that the caller cannot see: which skill it
# runs and whether it still works, never its body, resources, usage or the rest of its metadata.
HIDDEN_SKILL_FIELDS = {"name", "description", "status", "replacedBy"}


def _requires(*names: str) -> str:
    return "metadata:\n  agent-loop:\n    requires:\n" + "".join(f"      - {n}\n" for n in names)


async def _grant(p, frm: str, from_coll: str, agent: str, kind: str, role: str) -> None:
    assert await p.batch_create_edges([{
        "from_id": frm, "from_collection": from_coll, "to_id": agent, "to_collection": _AGENTS,
        "type": kind, "role": role, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }], _PERMISSION)


async def _agent(p, org: str, name: str, owner: str, *, shared_with_org: bool = False) -> str:
    key = f"{org}-{name}"
    assert await p.batch_upsert_nodes([{
        "_key": key, "name": name, "description": "d", "startMessage": "s", "systemPrompt": "p",
        "models": [], "createdBy": owner, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "isDeleted": False,
    }], _AGENTS)
    await _grant(p, owner, _USERS, key, "USER", "OWNER")
    if shared_with_org:
        await _grant(p, org, _ORGS, key, "ORG", "READER")
    return key


async def _link(p, agent_key: str, org: str, skill: str) -> None:
    assert await p.batch_create_edges([{
        "from_id": agent_key, "from_collection": _AGENTS, "to_id": f"{org}_{skill}", "to_collection": _SKILLS,
        "createdAtTimestamp": 1,
    }], _HAS_SKILL)


async def _world(p, org: str) -> dict[str, str]:
    assert await p.batch_upsert_nodes([{"_key": org, "name": org, "accountType": "individual", "isActive": True}], _ORGS)
    assert await p.batch_upsert_nodes([
        {"_key": u, "userId": u, "email": f"{u}@example.com", "orgId": org, "isActive": True}
        for u in ("user-a", "user-b", "user-c", "admin-1")
    ], _USERS)
    a = skill_store(p, org, "user-a")
    await a.create_skill("a-private", md("a-private", "private of a"))
    seeder = skill_store(p, org, SEED_IDENTITY)
    await seeder.create_skill("it-pack", md("it-pack", "the org pack", FM))
    await a.create_skill("a-wrapper", md("a-wrapper", "wrapper of a", _REQUIRES_PACK))
    await skill_store(p, org, "user-b").create_skill("b-wrapper", md("b-wrapper", "wrapper of b", _REQUIRES_PACK))
    agents = {
        "agA": await _agent(p, org, "PayrollAgentOfA", "user-a"),
        "agB": await _agent(p, org, "AgentOfB", "user-b"),
        "agC": await _agent(p, org, "AgentOfC", "user-c"),
        "agOrg": await _agent(p, org, "OrgAgent", "user-c", shared_with_org=True),
    }
    for agent, skill in (
        ("agA", "a-private"), ("agC", "a-private"), ("agOrg", "a-private"),
        ("agA", "it-pack"), ("agB", "it-pack"), ("agOrg", "it-pack"),
    ):
        await _link(p, agents[agent], org, skill)
    return agents


async def _client(p, org: str) -> httpx.AsyncClient:
    app = _app(p, {"ORG": org, "ORG2": f"{org}-other"})
    app.include_router(agent_routes.router, prefix="/api/v1/agent")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


def _auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


async def test_member_reads_usage_of_a_co_workers_private_skill(provider, node) -> None:  # noqa: F811
    """A member gets the 404 of a missing skill; no agent of the creator is named."""
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    await _world(provider, org)
    async with await _client(provider, org) as c:
        seen = await c.get("/api/v1/skills/a-private", headers=_auth("tok-b"))
        usage = await c.get("/api/v1/skills/a-private/usage", headers=_auth("tok-b"))
        missing = await c.get("/api/v1/skills/no-such-skill/usage", headers=_auth("tok-b"))
        other_org = await c.get("/api/v1/skills/a-private/usage", headers=_auth("tok-admin2"))
    assert seen.status_code == 404
    assert usage.status_code == missing.status_code == other_org.status_code == 404
    assert usage.json() == missing.json() == other_org.json() == {"detail": "Skill not found."}
    assert "PayrollAgentOfA" not in usage.text and org not in usage.text


@pytest.mark.parametrize(("tok", "skill", "status"), [
    ("tok-a", "a-private", 200), ("tok-b", "a-private", 404), ("tok-admin", "a-private", 404), ("tok-admin2", "a-private", 404),
    ("tok-a", "it-pack", 200), ("tok-b", "it-pack", 200), ("tok-admin", "it-pack", 200), ("tok-admin2", "it-pack", 404),
])
async def test_usage_is_answered_as_get_is(provider, node, tok, skill, status) -> None:  # noqa: F811
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    await _world(provider, org)
    async with await _client(provider, org) as c:
        got = await c.get(f"/api/v1/skills/{skill}", headers=_auth(tok))
        usage = await c.get(f"/api/v1/skills/{skill}/usage", headers=_auth(tok))
    assert got.status_code == usage.status_code == status, (got.text, usage.text)


@pytest.mark.parametrize(("tok", "skill", "agents", "other_agents", "skills", "other_skills"), [
    ("tok-a", "a-private", {"agA", "agOrg"}, 1, [], 0),
    ("tok-a", "it-pack", {"agA", "agOrg"}, 1, ["a-wrapper"], 1),
    ("tok-b", "it-pack", {"agB", "agOrg"}, 1, ["b-wrapper"], 1),
    ("tok-admin", "it-pack", {"agOrg"}, 2, [], 2),
])
async def test_usage_names_only_what_the_caller_may_see(
    provider, node, tok, skill, agents, other_agents, skills, other_skills,  # noqa: F811
) -> None:
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    keys = await _world(provider, org)
    async with await _client(provider, org) as c:
        r = await c.get(f"/api/v1/skills/{skill}/usage", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {a["id"] for a in body["usedByAgents"]} == {keys[a] for a in agents}
    assert body["otherAgentCount"] == other_agents
    assert body["requiredBySkills"] == skills
    assert body["otherSkillCount"] == other_skills
    for hidden in set(keys) - agents:
        assert keys[hidden] not in r.text


async def test_the_in_use_refusal_names_only_agents_the_caller_may_open(provider, node) -> None:  # noqa: F811
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    keys = await _world(provider, org)
    async with await _client(provider, org) as c:
        refused = await c.delete("/api/v1/skills/a-private", headers=_auth("tok-a"))
        deleted = await c.delete("/api/v1/skills/a-private", params={"detach": "true"}, headers=_auth("tok-a"))
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert {a["id"] for a in detail["usedByAgents"]} == {keys["agA"], keys["agOrg"]}
    assert detail["otherAgentCount"] == 1
    assert keys["agC"] not in refused.text and "AgentOfC" not in refused.text
    assert deleted.status_code == 200, deleted.text
    assert await provider.get_document(f"{org}_a-private", _SKILLS) is None


async def test_a_requires_on_a_name_nobody_holds_claims_no_skill_created_later(provider, node) -> None:  # noqa: F811
    """a's private skill requires `helper-3` before anyone holds it and `helper-4` after b created it.
    b's `helper-3` has no dependant (b could otherwise never delete it, and would learn a hidden
    skill requires the name); `helper-4` has one, counted."""
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    await _world(provider, org)
    b = skill_store(provider, org, "user-b")
    await b.create_skill("helper-4", md("helper-4", "helper four"))
    await skill_store(provider, org, "user-a").create_skill(
        "a-secret-dep", md("a-secret-dep", "secret dependant", _requires("helper-3", "helper-4")),
    )
    await b.create_skill("helper-3", md("helper-3", "helper three"))
    async with await _client(provider, org) as c:
        h3 = await c.get("/api/v1/skills/helper-3/usage", headers=_auth("tok-b"))
        h4 = await c.get("/api/v1/skills/helper-4/usage", headers=_auth("tok-b"))
        refused = await c.delete("/api/v1/skills/helper-4", params={"detach": "true"}, headers=_auth("tok-b"))
        deleted = await c.delete("/api/v1/skills/helper-3", params={"detach": "true"}, headers=_auth("tok-b"))
    assert (h3.status_code, h3.json()["requiredBySkills"], h3.json()["otherSkillCount"]) == (200, [], 0), h3.text
    assert (h4.status_code, h4.json()["requiredBySkills"], h4.json()["otherSkillCount"]) == (200, [], 1), h4.text
    assert refused.status_code == 409, refused.text
    assert deleted.status_code == 200, deleted.text
    assert "a-secret-dep" not in h3.text + h4.text + refused.text


async def test_an_agent_shows_a_skill_the_caller_cannot_see_by_name_description_and_status_only(provider, node) -> None:  # noqa: F811
    """b may open the org-shared agent carrying a's private skill. The agent says which skills
    it runs and whether they still work, never what is in them; the skill's own routes still
    answer b as for a missing name. a, who owns the skill, sees its whole entry."""
    org = f"it-usage-{uuid.uuid4().hex[:10]}"
    keys = await _world(provider, org)
    a = skill_store(provider, org, "user-a")
    assert await a.write_resource("a-private", "notes.md", "resource text of a")
    assert await provider.update_node(f"{org}_a-private", _SKILLS, {"usageTotalActivations": 7})
    async with await _client(provider, org) as c:
        agent = await c.get(f"/api/v1/agent/{keys['agOrg']}", headers=_auth("tok-b"))
        owner = await c.get(f"/api/v1/agent/{keys['agOrg']}", headers=_auth("tok-a"))
        routes = ("", "/usage", "/versions", "/export", "/resource?path=notes.md")
        hidden = [await c.get(f"/api/v1/skills/a-private{r}", headers=_auth("tok-b")) for r in routes]
        missing = [await c.get(f"/api/v1/skills/no-such-skill{r}", headers=_auth("tok-b")) for r in routes]
    assert agent.status_code == owner.status_code == 200, (agent.text, owner.text)
    skills = {s["name"]: s for s in agent.json()["agent"]["skills"]}
    assert set(skills) == {"a-private", "it-pack"}
    assert skills["a-private"] == {"name": "a-private", "description": "Use when private of a", "status": "active", "replacedBy": None}
    assert {"version", "category"} <= set(skills["it-pack"])
    for leaked in ("Body private of a", "resource text of a", "notes.md", "usageTotalActivations", "user-a"):
        assert leaked not in agent.text, leaked
    owned = {s["name"]: s for s in owner.json()["agent"]["skills"]}
    assert {"version", "category"} <= set(owned["a-private"])
    assert [(r.status_code, r.text.replace("a-private", "<n>")) for r in hidden] == \
        [(r.status_code, r.text.replace("no-such-skill", "<n>")) for r in missing]
