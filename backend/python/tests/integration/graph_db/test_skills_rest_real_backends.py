"""The skills routes over HTTP on a real Neo4j and a real ArangoDB.

The real router and store run against the shipped providers. Only identity is stubbed: a header
names the caller, the user-key lookup and the organization-role call are patched. A create with a
name another member already uses is a 409 that leaves that skill as it was; an import that claims
to be a builtin is stored as an ordinary skill; an admin can disable and delete any skill.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes import skills as skills_routes
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.bound_org import bound_like_auth

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from fastapi import Response

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "skills_rest_it"
logger = logging.getLogger("skills-rest-it")

_CLAIMING_MD = (
    "---\nname: imported-skill\ndescription: Use when importing\nmetadata:\n  agent-loop:\n"
    "    source: builtin\n    pack_name: imported-skill\n    pack_version: 9.9.9\n---\n\nImported body.\n"
)


def _app(provider: IGraphDBProvider, org: str) -> FastAPI:
    app = FastAPI()
    app.container = SimpleNamespace(
        graph_provider=AsyncMock(return_value=provider),
        retrieval_service=AsyncMock(return_value=AsyncMock()),
        logger=MagicMock(return_value=logger),
        config_service=MagicMock(return_value=MagicMock()),
    )

    @app.middleware("http")
    async def _who(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.user = {"userId": request.headers["x-it-user"], "orgId": org, "email": "it@example.com"}
        with bound_like_auth(request.state.user):
            return await call_next(request)

    app.include_router(skills_routes.router, prefix="/api/v1/skills")
    return app


async def _fetch_role(request: Request, _config: object) -> CallerRole:
    return CallerRole(CallerRoleStatus.VALID, "admin" if request.headers.get("x-it-user") == "admin-1" else "member")


@pytest.fixture(params=["neo4j", "arango"])
async def client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as stack:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            stack.push_async_callback(disconnect)
        org = f"it-org-{uuid.uuid4().hex[:10]}"
        stack.enter_context(patch.object(skills_routes, "is_skills_enabled", new=AsyncMock(return_value=True)))
        stack.enter_context(patch.object(skills_routes, "_get_user_key", new=AsyncMock(side_effect=lambda uid, *_: f"{uid}-key")))
        stack.enter_context(patch.object(skills_routes, "fetch_caller_role", new=_fetch_role))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(provider, org)), base_url="http://it") as http:
            yield http, provider, org
        for collection in ("agentSkills", "agentSkillVersions"):
            if isinstance(provider, Neo4jProvider):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(collection)}) WHERE n.orgId = $o DETACH DELETE n", parameters={"o": org},
                )
            else:
                await provider.http_client.execute_aql(
                    f"FOR n IN {collection} FILTER n.orgId == @o REMOVE n IN {collection}", {"o": org},
                )


def H(user: str) -> dict[str, str]:
    return {"x-it-user": user}


async def _stored(provider: IGraphDBProvider, org: str, name: str) -> dict:
    doc = await provider.get_document(f"{org}_{name}", "agentSkills")
    versions = await provider.get_nodes_by_filters("agentSkillVersions", {"orgId": org, "name": name})
    return {"doc": doc, "versions": sorted(v["version"] for v in versions)}


async def test_a_create_or_import_with_a_taken_name_is_409_and_changes_nothing(client) -> None:
    http, provider, org = client
    created = await http.post("/api/v1/skills/", headers=H("user-a"), json={"name": "base-skill", "description": "b", "body": "b"})
    assert created.status_code == 201, created.text
    created = await http.post("/api/v1/skills/", headers=H("user-a"), json={
        "name": "deploy-runbook", "description": "first", "body": "Private first body", "requires": ["base-skill"],
    })
    assert created.status_code == 201, created.text
    assert (await http.put("/api/v1/skills/deploy-runbook", headers=H("user-a"), json={
        "description": "first", "body": "Private first body v2", "requires": ["base-skill"],
    })).status_code == 200
    assert (await http.put("/api/v1/skills/deploy-runbook/resource", headers=H("user-a"), json={"path": "notes.md", "content": "n"})).status_code == 200
    before = await _stored(provider, org, "deploy-runbook")

    again = await http.post("/api/v1/skills/", headers=H("user-b"), json={"name": "deploy-runbook", "description": "second", "body": "x"})
    assert again.status_code == 409
    assert "user-a" not in again.text and "Private" not in again.text
    imported = await http.post("/api/v1/skills/import/finalize", headers=H("user-b"), json={
        "content": "---\nname: deploy-runbook\ndescription: second\n---\n\nx\n", "resources": {"notes.md": "other"},
    })
    assert imported.status_code == 409
    assert (await http.get("/api/v1/skills/deploy-runbook", headers=H("user-b"))).status_code == 404

    assert await _stored(provider, org, "deploy-runbook") == before


async def test_an_import_that_claims_to_be_a_builtin_is_an_ordinary_skill(client) -> None:
    http, provider, org = client
    imported = await http.post("/api/v1/skills/import/finalize", headers=H("user-a"), json={"content": _CLAIMING_MD})
    assert imported.status_code == 201, imported.text

    doc = (await _stored(provider, org, "imported-skill"))["doc"]
    assert doc["source"] == "imported" and not doc.get("packName") and not doc.get("packVersion")
    listed = await http.get("/api/v1/skills/", headers=H("user-b"))
    assert "imported-skill" not in listed.text


async def test_an_admin_can_disable_and_delete_a_members_skill_and_a_member_cannot(client) -> None:
    http, provider, org = client
    assert (await http.post("/api/v1/skills/", headers=H("user-a"), json={"name": "moderated", "description": "m", "body": "m"})).status_code == 201

    assert (await http.post("/api/v1/skills/moderated/disable", headers=H("user-b"))).status_code == 404
    assert (await http.delete("/api/v1/skills/moderated", headers=H("user-b"))).status_code == 404
    assert (await http.post("/api/v1/skills/moderated/disable", headers=H("admin-1"))).status_code == 200
    assert (await _stored(provider, org, "moderated"))["doc"]["status"] == "disabled"
    assert (await http.delete("/api/v1/skills/moderated", headers=H("admin-1"))).status_code == 200
    assert (await _stored(provider, org, "moderated"))["doc"] is None
