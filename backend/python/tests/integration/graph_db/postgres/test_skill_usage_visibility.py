"""A skill's usage answers for the caller, on the PostgreSQL graph provider with schema validation on.

`GraphSkillStore.get_referential_usage` is where every surface reads usage: the usage route, the
in-use refusal of a delete, and the `skill_manage` tool. A skill the caller cannot see has no
usage. Only agents the caller can open (the provider's `check_agent_permission`) are named, and
only dependant skills the caller can see; the rest are counted, and the counts still refuse a
delete. A `requires` counts only against the skill it was written for, and an agent the caller can
open shows a skill it cannot see by name, description, status and replacement only (`GET /agent/{id}`).
Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillInUseError,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes import agent as agent_routes
from app.core.org_context import OrgContext, bind
from tests.support.skill_stores import skill_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response

    from app.agents.agent_loop.skills.graph_store import GraphSkillStore

pytestmark = pytest.mark.integration

_FM = "metadata:\n  agent-loop:\n    source: builtin\n    pack_name: it-pack\n    pack_version: '1'\n"
_REQUIRES_PACK = "metadata:\n  agent-loop:\n    requires:\n      - it-pack\n"
HIDDEN_SKILL_FIELDS = {"name", "description", "status", "replacedBy"}


def _requires(*names: str) -> str:
    return "metadata:\n  agent-loop:\n    requires:\n" + "".join(f"      - {n}\n" for n in names)


def _md(name: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: Use when doing {name}\n{extra}---\n\nBody of {name}.\n"


class _NoIndex:
    async def remove_entry(self, name: str) -> None:
        return None


@pytest.fixture
async def org(pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


def _store(p, org: str, user: str, *, scoped: bool = True, admin: bool = False) -> GraphSkillStore:
    return skill_store(p, org, user, visibility_scope=user if scoped else None, is_admin=admin)


async def _edge(p, coll: str, frm: tuple[str, str], to: tuple[str, str], **doc: object) -> None:
    assert await p.batch_create_edges([{
        "from_collection": frm[0], "from_id": frm[1], "to_collection": to[0], "to_id": to[1], "createdAtTimestamp": 1, **doc,
    }], coll)


async def _world(p, org: str) -> None:
    assert await p.batch_upsert_nodes([{"_key": org, "name": org, "accountType": "individual", "isActive": True}], "organizations")
    assert await p.batch_upsert_nodes([
        {"_key": u, "orgId": org, "userId": u, "email": f"{u}@example.com"} for u in ("user-a", "user-b", "user-c", "admin-1")
    ], "users")
    await _store(p, org, "user-a").create_skill("a-private", _md("a-private"))
    await skill_store(p, org, SEED_IDENTITY).create_skill("it-pack", _md("it-pack", _FM))
    await _store(p, org, "user-a").create_skill("a-wrapper", _md("a-wrapper", _REQUIRES_PACK))
    await _store(p, org, "user-b").create_skill("b-wrapper", _md("b-wrapper", _REQUIRES_PACK))
    for agent, owner, org_shared in (("agA", "user-a", False), ("agB", "user-b", False), ("agC", "user-c", False), ("agOrg", "user-c", True)):
        assert await p.batch_upsert_nodes([{
            "_key": agent, "name": f"name-of-{agent}", "description": "d", "startMessage": "s", "systemPrompt": "p",
            "models": [], "createdBy": owner, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "isDeleted": False,
        }], "agentInstances")
        await _edge(p, "permission", ("users", owner), ("agentInstances", agent), type="USER", role="OWNER")
        if org_shared:
            await _edge(p, "permission", ("organizations", org), ("agentInstances", agent), type="ORG", role="READER")
    for agent, skill in (("agA", "a-private"), ("agC", "a-private"), ("agOrg", "a-private"), ("agA", "it-pack"), ("agB", "it-pack"), ("agOrg", "it-pack")):
        await _edge(p, "agentHasSkill", ("agentInstances", agent), ("agentSkills", f"{org}_{skill}"))


def _shape(usage) -> tuple[set[str], int, list[str], int]:
    return {a["id"] for a in usage.used_by_agents}, usage.other_agent_count, usage.required_by_skills, usage.other_skill_count


@pytest.mark.parametrize(("who", "skill", "expected"), [
    ("creator", "a-private", ({"agA", "agOrg"}, 1, [], 0)),
    ("co-worker", "a-private", (set(), 0, [], 0)),
    ("admin", "a-private", ({"agOrg"}, 2, [], 0)),
    ("runtime", "a-private", ({"agOrg"}, 2, [], 0)),
    ("other-org", "a-private", (set(), 0, [], 0)),
    ("creator", "it-pack", ({"agA", "agOrg"}, 1, ["a-wrapper"], 1)),
    ("co-worker", "it-pack", ({"agB", "agOrg"}, 1, ["b-wrapper"], 1)),
    ("admin", "it-pack", ({"agOrg"}, 2, ["a-wrapper", "b-wrapper"], 0)),
])
async def test_usage_names_only_what_the_caller_may_see(pg_provider, org, org_ctx, who, skill, expected) -> None:
    with org_ctx(org):
        await _world(pg_provider, org)
        store = {
            "creator": lambda: _store(pg_provider, org, "user-a"),
            "co-worker": lambda: _store(pg_provider, org, "user-b"),
            "admin": lambda: _store(pg_provider, org, "admin-1", scoped=False, admin=True),
            "runtime": lambda: _store(pg_provider, org, "user-z", scoped=False),
            "other-org": lambda: _store(pg_provider, uuid.uuid4().hex[:24], "user-x"),
        }[who]()
        usage = await store.get_referential_usage(skill)
    assert _shape(usage) == expected
    assert all(a["name"] == f"name-of-{a['id']}" for a in usage.used_by_agents)


async def test_the_delete_refusal_counts_and_never_names_what_the_caller_cannot_open(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await _world(pg_provider, org)
        manager = SkillManager(store=_store(pg_provider, org, "user-a"), index=_NoIndex(), tracker=None)  # type: ignore[arg-type]
        with pytest.raises(SkillInUseError) as exc:
            await manager.delete("a-private")
        assert {a["id"] for a in exc.value.used_by_agents} == {"agA", "agOrg"} and exc.value.other_agent_count == 1
        assert "agC" not in str(exc.value) and "3 agent(s)" in str(exc.value)
        co_worker = SkillManager(store=_store(pg_provider, org, "user-b"), index=_NoIndex(), tracker=None)  # type: ignore[arg-type]
        assert await co_worker.delete("a-private") is False
        assert await manager.delete("a-private", detach=True)
        assert await pg_provider.get_document(f"{org}_a-private", "agentSkills") is None


async def test_a_requires_on_a_name_nobody_holds_claims_no_skill_created_later(pg_provider, org, org_ctx) -> None:
    """PostgreSQL keeps an edge to a key nobody holds; it must not claim the skill created there later."""
    with org_ctx(org):
        await _world(pg_provider, org)
        b = _store(pg_provider, org, "user-b")
        await b.create_skill("helper-4", _md("helper-4"))
        await _store(pg_provider, org, "user-a").create_skill("a-secret-dep", _md("a-secret-dep", _requires("helper-3", "helper-4")))
        await b.create_skill("helper-3", _md("helper-3"))
        h3, h4 = await b.get_referential_usage("helper-3"), await b.get_referential_usage("helper-4")
        manager = SkillManager(store=b, index=_NoIndex(), tracker=None)  # type: ignore[arg-type]
        with pytest.raises(SkillInUseError):
            await manager.delete("helper-4", detach=True)
        assert await manager.delete("helper-3", detach=True)
    assert (h3.required_by_skills, h3.other_skill_count) == ([], 0)
    assert (h4.required_by_skills, h4.other_skill_count) == ([], 1)


def _agent_app(p, org: str, user: str) -> FastAPI:
    app = FastAPI()
    config = MagicMock()
    config.get_config = AsyncMock(return_value={})
    app.container = SimpleNamespace(
        graph_provider=AsyncMock(return_value=p), retrieval_service=AsyncMock(return_value=AsyncMock()),
        logger=MagicMock(), config_service=MagicMock(return_value=config),
    )

    @app.middleware("http")
    async def _auth(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = {"userId": user, "orgId": org, "email": f"{user}@example.com"}
        # As the real auth middleware does: the verified token's org is bound for the request.
        with bind(OrgContext(org_id=org, user_id=user, kind="user", source="jwt", request_id="-")):
            return await call_next(request)

    app.include_router(agent_routes.router, prefix="/api/v1/agent")
    return app


async def test_an_agent_shows_a_skill_the_caller_cannot_see_by_name_description_and_status_only(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await _world(pg_provider, org)
        assert await _store(pg_provider, org, "user-a").write_resource("a-private", "notes.md", "resource text of a")
        assert await pg_provider.update_node(f"{org}_a-private", "agentSkills", {"usageTotalActivations": 7})
        got = {}
        for user in ("user-b", "user-a"):
            transport = httpx.ASGITransport(app=_agent_app(pg_provider, org, user), raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                got[user] = await c.get("/api/v1/agent/agOrg")
        assert await _store(pg_provider, org, "user-b").get_skill("a-private") is None
    agent, owner = got["user-b"], got["user-a"]
    assert agent.status_code == owner.status_code == 200, (agent.text, owner.text)
    skills = {s["name"]: s for s in agent.json()["agent"]["skills"]}
    assert set(skills) == {"a-private", "it-pack"}
    assert skills["a-private"] == {"name": "a-private", "description": "Use when doing a-private", "status": "active", "replacedBy": None}
    assert {"version", "category"} <= set(skills["it-pack"])
    for leaked in ("Body of a-private", "resource text of a", "notes.md", "usageTotalActivations", "user-a"):
        assert leaked not in agent.text, leaked
    assert {"version", "category"} <= set({s["name"]: s for s in owner.json()["agent"]["skills"]}["a-private"])


async def test_the_agent_builder_links_only_skills_the_caller_may_see_inside_the_agents_write(pg_provider, org, org_ctx) -> None:
    """`_create_skill_edges` inside a real transaction, in the bound org: a's own skill and the
    org's builtin are linked; b's skill, a's disabled skill and a missing name are not."""
    with org_ctx(org):
        await _world(pg_provider, org)
        a = _store(pg_provider, org, "user-a")
        await a.create_skill("a-off", _md("a-off"))
        assert await a.set_skill_status("a-off", SkillStatus.DISABLED)
        assert await pg_provider.batch_upsert_nodes([{
            "_key": "agBuild", "name": "builder", "description": "d", "startMessage": "s", "systemPrompt": "p",
            "models": [], "createdBy": "user-a", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "isDeleted": False,
        }], "agentInstances")
        await _edge(pg_provider, "permission", ("users", "user-a"), ("agentInstances", "agBuild"), type="USER", role="OWNER")
        names = ["a-private", "it-pack", "b-wrapper", "a-off", "no-such-skill"]
        # The route begins the agent's write and links inside it under the request's binding.
        with bind(OrgContext(org_id=org, user_id="user-a", kind="user", source="jwt", request_id="-")):
            t = await pg_provider.begin_transaction(read=["agentSkills"], write=["agentHasSkill"])
            try:
                linked = await agent_routes._create_skill_edges(
                    "agBuild", names, "user-a", pg_provider, logging.getLogger("pg-skill-links"), transaction=t,
                )
                await pg_provider.commit_transaction(t)
            except BaseException:
                await pg_provider.rollback_transaction(t)
                raise
        edges = await pg_provider.get_edges_from_node("agentInstances/agBuild", "agentHasSkill")
        usage = {s: await a.get_referential_usage(s) for s in ("a-private", "it-pack")}
    assert linked == ["a-private", "it-pack"]
    assert sorted(e.get("skillName") for e in edges) == ["a-private", "it-pack"]
    for skill, found in usage.items():
        assert "agBuild" in {u["id"] for u in found.used_by_agents}, skill
