"""Who may change a skill, over HTTP with the real role lookup, on a real Neo4j and a real ArangoDB.

The real skills router runs with the real `fetch_caller_role`, which asks a fake Node
(`/api/v1/users/me/role`) that answers only from the Authorization header. A matrix of
{creator, member, org admin, admin of another org} x {edit, resource, rollback, disable, delete,
versions} x {custom skill, seeded builtin, skill stored as builtin before builtins were reserved}
is checked against the rule, and a refusal must leave the stored skill unchanged. The admin
role cannot be supplied by headers, query, body or frontmatter, and a Node that is down
leaves only the creator's own actions.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD. SKILLS_IT_NODE_PORT moves the fake Node (default 20231).
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiohttp import web
from fastapi import FastAPI, Request

from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.api.routes import skills as skills_routes
from app.config.constants.service import config_node_constants
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.skill_stores import skill_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
log = logging.getLogger("skills-permissions-it")
NODE_PORT = int(os.environ.get("SKILLS_IT_NODE_PORT", "20231"))
TOKENS = {"tok-a": ("user-a", "ORG"), "tok-b": ("user-b", "ORG"), "tok-admin": ("admin-1", "ORG"), "tok-admin2": ("admin-2", "ORG2")}
ADMINS = {"tok-admin", "tok-admin2"}
FM = "metadata:\n  agent-loop:\n    source: builtin\n    pack_name: it-pack\n    pack_version: '9'\n"


def md(n: str, m: str, extra: str = "") -> str:
    return f"---\nname: {n}\ndescription: Use when {m}\n{extra}---\n\nBody {m}.\n"


async def _node_role(request: web.Request) -> web.Response:
    auth = request.headers.get("Authorization", "")
    tok = auth.removeprefix("Bearer ")
    if tok not in TOKENS:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"role": "admin" if tok in ADMINS else "member"})


@pytest.fixture
async def node() -> AsyncIterator[web.AppRunner]:
    app = web.Application()
    app.router.add_get("/api/v1/users/me/role", _node_role)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", NODE_PORT)
    await site.start()
    yield runner
    await runner.cleanup()


def _config_service(node_up: bool) -> MagicMock:
    async def get_config(key: str, *args: object, **kwargs: object) -> dict:
        if key == config_node_constants.ENDPOINTS.value:
            port = NODE_PORT if node_up else NODE_PORT + 1
            return {"nodejs": {"endpoint": f"http://127.0.0.1:{port}"}}
        return {}
    cs = MagicMock()
    cs.get_config = AsyncMock(side_effect=get_config)
    return cs


def _app(provider, orgs: dict[str, str], node_up: bool = True) -> FastAPI:
    app = FastAPI()
    cs = _config_service(node_up)
    app.container = SimpleNamespace(
        graph_provider=AsyncMock(return_value=provider),
        retrieval_service=AsyncMock(return_value=AsyncMock()),
        logger=MagicMock(return_value=log),
        config_service=MagicMock(return_value=cs),
    )

    @app.middleware("http")
    async def _auth(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        tok = request.headers.get("authorization", "").removeprefix("Bearer ")
        uid, org = TOKENS[tok]
        request.state.user = {"userId": uid, "orgId": orgs[org], "email": f"{uid}@example.com"}
        return await call_next(request)

    app.include_router(skills_routes.router, prefix="/api/v1/skills")
    return app


@pytest.fixture(params=["neo4j", "arango"])
async def provider(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as stack:
        try:
            p = await (connect_neo4j(log, monkeypatch) if request.param == "neo4j" else connect_arango(log, "skills_permissions_it"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        stack.enter_context(patch.object(skills_routes, "is_skills_enabled", new=AsyncMock(return_value=True)))
        stack.enter_context(patch.object(skills_routes, "_get_user_key", new=AsyncMock(side_effect=lambda uid, *_: uid)))
        from app.api.middlewares import caller_role
        cache = getattr(caller_role, "_CACHE", None) or getattr(caller_role, "_cache", None)
        if cache is not None and hasattr(cache, "clear"):
            cache.clear()
        yield p


async def _setup(p, org: str) -> None:
    a = skill_store(p, org, "user-a")
    await a.create_skill("it-custom", md("it-custom", "custom v1"))
    await a.update_skill("it-custom", md("it-custom", "custom v2"))
    s = skill_store(p, org, SEED_IDENTITY)
    await s.create_skill("it-seeded", md("it-seeded", "seeded v1", FM))
    await s.update_skill("it-seeded", md("it-seeded", "seeded v2", FM))
    await a.create_skill("it-legacy", md("it-legacy", "legacy v1"))
    await a.update_skill("it-legacy", md("it-legacy", "legacy v2"))
    assert await p.update_node(f"{org}_it-legacy", "agentSkills", {"source": "builtin"})


def _req(op: str, name: str) -> tuple[str, str, dict]:
    base = f"/api/v1/skills/{name}"
    return {
        "edit": ("PUT", base, {"json": {"description": "edited", "body": "edited body"}}),
        "resource": ("PUT", base + "/resource", {"json": {"path": "x.md", "content": "w"}}),
        "rollback": ("POST", base + "/rollback", {"json": {"version": "1.0.0"}}),
        "disable": ("POST", base + "/disable", {}),
        "delete": ("DELETE", base, {}),
        "versions": ("GET", base + "/versions", {}),
    }[op]


TYPES = {"custom": "it-custom", "seeded": "it-seeded", "legacy": "it-legacy"}
ACTORS = {"creator": "tok-a", "member": "tok-b", "admin": "tok-admin", "other_org_admin": "tok-admin2"}
OPS = ["edit", "resource", "rollback", "disable", "delete", "versions"]


def expected(actor: str, kind: str, op: str) -> int:
    if op == "versions":
        return 200  # an invisible skill answers 200 with an empty list (checked below), never another user's revisions
    if actor == "other_org_admin":
        return 404
    if kind == "seeded":
        if op == "versions":
            return 200
        if op == "disable" and actor == "admin":
            return 200
        return 403
    if actor == "creator":
        return 200
    if actor == "member":
        return 404
    return 200 if op in ("disable", "delete") else 404  # admin on a member's skill


async def test_route_matrix(provider, node) -> None:
    mismatches, rows = [], 0
    for actor, tok in ACTORS.items():
        for kind, name in TYPES.items():
            for op in OPS:
                org = f"it-perm-{uuid.uuid4().hex[:8]}"
                await _setup(provider, org)
                before = await provider.get_document(f"{org}_{name}", "agentSkills")
                app = _app(provider, {"ORG": org, "ORG2": org + "-2"})
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://it") as c:
                    method, url, kw = _req(op, name)
                    r = await c.request(method, url, headers={"Authorization": f"Bearer {tok}"}, **kw)
                after = await provider.get_document(f"{org}_{name}", "agentSkills")
                want = expected(actor, kind, op)
                rows += 1
                if r.status_code != want:
                    mismatches.append((actor, kind, op, want, r.status_code, r.text[:100]))
                visible = actor == "creator" or (kind == "seeded" and actor != "other_org_admin")
                if op == "versions" and not visible and r.json() != {"versions": []}:
                    mismatches.append((actor, kind, op, "empty", r.text[:100]))
                if r.status_code >= 400 and after != before:
                    mismatches.append((actor, kind, op, "unchanged", "CHANGED"))
        assert not mismatches


async def test_admin_flag_cannot_be_smuggled(provider, node) -> None:
    org = f"it-perm-{uuid.uuid4().hex[:8]}"
    await _setup(provider, org)
    before = await provider.get_document(f"{org}_it-custom", "agentSkills")
    app = _app(provider, {"ORG": org, "ORG2": org + "-2"})
    smuggle = {"x-role": "admin", "x-pipeshub-role": "admin", "x-is-admin": "true", "x-user-role": "admin",
               "x-pipeshub-mcp-relay": "admin", "x-service-authorization": "Bearer tok-admin", "cookie": "role=admin"}
    results = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://it") as c:
        h = {"Authorization": "Bearer tok-b", **smuggle}
        results.append((await c.post("/api/v1/skills/it-custom/disable?is_admin=true&admin=1&role=admin", headers=h, json={"is_admin": True, "role": "admin"})).status_code)
        results.append((await c.delete("/api/v1/skills/it-custom?is_admin=true", headers=h)).status_code)
        results.append((await c.post("/api/v1/skills/it-seeded/disable", headers=h, json={"is_admin": True})).status_code)
        imp = md("it-smuggle", "smuggle", "is_admin: true\nrole: admin\nmetadata:\n  agent-loop:\n    is_admin: true\n    source: builtin\n")
        r = await c.post("/api/v1/skills/import/finalize", headers=h, json={"content": imp, "is_admin": True})
        results.append(r.status_code)
        d = await provider.get_document(f"{org}_it-smuggle", "agentSkills")
        smuggled_source = d and d.get("source")
    after = await provider.get_document(f"{org}_it-custom", "agentSkills")
    assert results[:3] == [404, 404, 403] and after == before and smuggled_source == "imported"


async def test_node_down_fails_closed(provider) -> None:
    org = f"it-perm-{uuid.uuid4().hex[:8]}"
    await _setup(provider, org)
    app = _app(provider, {"ORG": org, "ORG2": org + "-2"}, node_up=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://it") as c:
        h = {"Authorization": "Bearer tok-admin"}
        r1 = (await c.post("/api/v1/skills/it-custom/disable", headers=h)).status_code
        r2 = (await c.post("/api/v1/skills/it-seeded/disable", headers=h)).status_code
        r3 = (await c.delete("/api/v1/skills/it-legacy", headers=h)).status_code
        r4 = (await c.post("/api/v1/skills/it-custom/disable", headers={"Authorization": "Bearer tok-a"})).status_code
    assert (r1, r2, r3, r4) == (404, 403, 404, 200)
