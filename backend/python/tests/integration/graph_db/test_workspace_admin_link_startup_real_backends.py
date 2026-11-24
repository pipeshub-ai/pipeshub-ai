"""The startup removal of a wrong creator-to-account link on a real ArangoDB and a real Neo4j.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d
Run: pytest tests/integration/graph_db/test_workspace_admin_link_startup_real_backends.py -m integration

A creator the connector lists as one of the source's own users has their own account there, so the account the credentials act
as (the delegated admin, the team token's admin) is never theirs: the Gmail Workspace, Drive Workspace and Dropbox team
connectors drop that link at startup. A creator the connector does not list, a link to oneself that has no meaning, and every other
connector type are decided as follows: unlisted creators and other types keep their link, a self-link goes.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.migrations.workspace_admin_link_migration import (
    WorkspaceAdminLinkMigrationService,
)

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401  (fixtures)
from .test_workspace_admin_link_store_real_backends import (
    LOGIN_USER_ID,
    _arango_links_of,
    _arango_random_key_link,
    _arango_users,
    _Config,
    _ensure_arango_collections,
    _neo4j_links_of,
    _neo4j_users,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# key: (app type, whether the connector lists the creator, link target: "admin" or "self", relation is an external collaborator's)
WORLD = {
    "gm": ("Gmail Workspace", True, "admin", False),
    "dr": ("Drive Workspace", True, "admin", False),
    "db": ("Dropbox", True, "admin", False),
    "selfl": ("Drive Workspace", False, "self", False),
    "gmo": ("Gmail Workspace", False, "admin", False),
    "ext": ("Gmail Workspace", True, "admin", True),
    "dbp": ("Dropbox Personal", True, "admin", False),
    "jira": ("Jira", True, "admin", False),
}
REMOVED = {"gm", "dr", "db", "selfl"}


async def _seed_arango(p, t: str) -> str:
    await _ensure_arango_collections(p)
    import aiohttp

    client = p.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as s:
        async with s.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": "userAppRelation", "type": 3}) as r:
            assert r.status in (200, 201, 409)
    org = f"org-{t}"
    q = p.execute_query
    await q("INSERT {_key: @o, isActive: true} INTO organizations", bind_vars={"o": org})
    for u in ("creator", "admin"):
        await q("INSERT {_key: @k, orgId: @o} INTO users", bind_vars={"k": f"{u}-{t}", "o": org})
    for k, (typ, listed, target, external) in WORLD.items():
        await q("INSERT {_key: @a, type: @t, orgId: @o} INTO apps", bind_vars={"a": f"{k}-{t}", "t": typ, "o": org})
        if listed:
            await q("INSERT {_from: @f, _to: @a, sourceUserId: 'src-1', isExternalUser: @e} INTO userAppRelation",
                    bind_vars={"f": f"users/creator-{t}", "a": f"apps/{k}-{t}", "e": external})
        await p.upsert_authenticated_as(f"creator-{t}", f"{'creator' if target == 'self' else 'admin'}-{t}", f"{k}-{t}", org)
    return org


async def _seed_neo4j(p, t: str) -> str:
    org = f"org-{t}"
    q = p.client.execute_query
    await q("CREATE (:Organization {id: $o, isActive: true})", parameters={"o": org})
    for u in ("creator", "admin"):
        await q("CREATE (:User {id: $k, orgId: $o})", parameters={"k": f"{u}-{t}", "o": org})
    for k, (typ, listed, target, external) in WORLD.items():
        await q("CREATE (:App {id: $a, type: $t, orgId: $o})", parameters={"a": f"{k}-{t}", "t": typ, "o": org})
        if listed:
            await q("MATCH (u:User {id: $u}), (a:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src-1', isExternalUser: $e}]->(a)",
                    parameters={"u": f"creator-{t}", "a": f"{k}-{t}", "e": external})
        await p.upsert_authenticated_as(f"creator-{t}", f"{'creator' if target == 'self' else 'admin'}-{t}", f"{k}-{t}", org)
    return org


async def _arango_left(p, t: str) -> set[str]:
    rows = await p.execute_query("FOR l IN authenticatedAs FILTER l.connectorId IN @c RETURN l.connectorId",
                                 bind_vars={"c": [f"{k}-{t}" for k in WORLD]})
    return {r.removesuffix(f"-{t}") for r in rows}


async def _neo4j_left(p, t: str) -> set[str]:
    rows = await p.client.execute_query("MATCH ()-[r:AUTHENTICATED_AS]->() WHERE r.connectorId IN $c RETURN r.connectorId AS c",
                                        parameters={"c": [f"{k}-{t}" for k in WORLD]})
    return {r["c"].removesuffix(f"-{t}") for r in rows}


async def _check(p, seed, left) -> None:
    t = uuid.uuid4().hex[:8]
    await seed(p, t)
    assert await left(p, t) == set(WORLD)

    service = WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it"))
    result = await service.migrate()

    assert result["success"] is True and result["removed"] >= len(REMOVED)
    assert await left(p, t) == set(WORLD) - REMOVED, (
        "a listed creator's link of the three types and a self-link go; an unlisted creator, an external collaborator, "
        "a personal Dropbox and any other connector keep theirs"
    )
    # a replica still on an older version links the creator again: the next startup removes it
    await p.upsert_authenticated_as(f"creator-{t}", f"admin-{t}", f"gm-{t}", f"org-{t}")
    again = await service.migrate()
    assert again["removed"] >= 1 and "gm" not in await left(p, t)


async def test_arango_the_startup_removes_the_wrong_links_only(arango_provider) -> None:  # noqa: F811
    await _check(arango_provider, _seed_arango, _arango_left)


async def test_neo4j_the_startup_removes_the_wrong_links_only(neo4j_provider) -> None:  # noqa: F811
    await _check(neo4j_provider, _seed_neo4j, _neo4j_left)


async def test_arango_a_moved_link_and_a_random_key_link_of_a_listed_creator_go_at_startup(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    t = uuid.uuid4().hex[:8]
    org, conn = f"org-{t}", f"gm-{t}"
    await _arango_users(p, org, t, f"admin-{t}@corp.example")
    await p.execute_query("INSERT {_from: @f, _to: @a, sourceUserId: 'src-1'} INTO userAppRelation",
                          bind_vars={"f": f"users/creator-{t}", "a": f"apps/{conn}"})
    await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
    await p.apply_verified_user_email(LOGIN_USER_ID[:-8] + t, org, f"admin-{t}@corp.example")
    assert await _arango_links_of(p, conn) == [f"adminlogin-{t}"]
    assert (await WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it")).migrate())["success"] is True
    assert await _arango_links_of(p, conn) == [], "a link a verified email change moved is removed like any other"

    conn2 = f"dr-{t}"
    await p.execute_query("INSERT {_key: @c, type: 'Drive Workspace', orgId: @o} INTO apps", bind_vars={"c": conn2, "o": org})
    await p.execute_query("INSERT {_from: @f, _to: @a, sourceUserId: 'src-1'} INTO userAppRelation",
                          bind_vars={"f": f"users/creator-{t}", "a": f"apps/{conn2}"})
    await _arango_random_key_link(p, t, f"adminlogin-{t}", conn2, org)
    assert (await WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it")).migrate())["success"] is True
    assert await _arango_links_of(p, conn2) == [], "a link that already has a random key is removed"


async def test_neo4j_a_moved_link_of_a_listed_creator_goes_at_startup(monkeypatch) -> None:
    from tests.integration.real_graph import connect_neo4j

    p = await connect_neo4j(logging.getLogger("wsleak-it"), monkeypatch)
    try:
        t = uuid.uuid4().hex[:8]
        org, conn = f"org-{t}", f"gm-{t}"
        await _neo4j_users(p, org, t, f"admin-{t}@corp.example")
        await p.client.execute_query(
            "MATCH (u:User {id: $u}), (a:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src-1'}]->(a)",
            parameters={"u": f"creator-{t}", "a": conn},
        )
        await p.upsert_authenticated_as(f"creator-{t}", f"adminstub-{t}", conn, org)
        await p.apply_verified_user_email(LOGIN_USER_ID[:-8] + t, org, f"admin-{t}@corp.example")
        assert await _neo4j_links_of(p, conn) == [f"adminlogin-{t}"]
        assert (await WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it")).migrate())["success"] is True
        assert await _neo4j_links_of(p, conn) == []
    finally:
        await p.disconnect()
