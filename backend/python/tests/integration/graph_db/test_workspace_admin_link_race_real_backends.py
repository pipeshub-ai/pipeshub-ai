"""Concurrency of the removal: the startup migration racing the syncs' own unlink (and a second replica's migration) on the same
links never raises out of migrate and leaves no link, on Neo4j and ArangoDB."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import pytest

from app.migrations.workspace_admin_link_migration import WorkspaceAdminLinkMigrationService

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401
from .test_workspace_admin_link_store_real_backends import _ensure_arango_collections

async def _ensure_relation_collection(p) -> None:
    import aiohttp

    client = p.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as sess:
        async with sess.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": "userAppRelation", "type": 3}) as r:
            assert r.status in (200, 201, 409)


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
N = 30


class _Config:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, **_: object) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> None:  # noqa: ANN401
        self.values[key] = value


async def _race(p, org, apps, links, monkeypatch) -> None:
    async def one(**_kw):
        return [{"_key": org}]
    monkeypatch.setattr(p, "get_all_orgs", one)
    jobs = [WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it")).migrate() for _ in range(3)]
    jobs += [p.remove_authenticated_as(a) for a in apps]
    out = await asyncio.gather(*jobs, return_exceptions=True)
    unlink_errors = [o for o in out[3:] if isinstance(o, Exception)]
    for o in out[:3]:
        assert not isinstance(o, Exception), o
    assert await links() == [], "a link survived the race"
    assert not unlink_errors or all("not found" in str(e).lower() or "1202" in str(e) for e in unlink_errors), unlink_errors


async def test_arango_race(arango_provider, monkeypatch) -> None:  # noqa: F811
    p = arango_provider
    await _ensure_arango_collections(p)
    await _ensure_relation_collection(p)
    t = uuid.uuid4().hex[:8]
    org = f"org-{t}"
    await p.execute_query("INSERT {_key: @o, isActive: true} INTO organizations", bind_vars={"o": org})
    for u in ("c", "a"):
        await p.execute_query("INSERT {_key: @u, orgId: @o} INTO users", bind_vars={"u": f"{u}-{t}", "o": org})
    apps = [f"gm{i}-{t}" for i in range(N)]
    for a in apps:
        await p.execute_query("INSERT {_key: @a, type: 'Gmail Workspace', orgId: @o} INTO apps", bind_vars={"a": a, "o": org})
        await p.execute_query("INSERT {_from: @f, _to: @t, sourceUserId: 'src'} INTO userAppRelation", bind_vars={"f": f"users/c-{t}", "t": f"apps/{a}"})
        await p.upsert_authenticated_as(f"c-{t}", f"a-{t}", a, org)

    async def links():
        return await p.execute_query("FOR l IN authenticatedAs FILTER l.connectorId IN @a RETURN l._key", bind_vars={"a": apps})
    await _race(p, org, apps, links, monkeypatch)


async def test_neo4j_race(neo4j_provider, monkeypatch) -> None:  # noqa: F811
    p = neo4j_provider
    t = uuid.uuid4().hex[:8]
    org = f"org-{t}"
    q = p.client.execute_query
    await q("CREATE (:Organization {id: $o, isActive: true})", parameters={"o": org})
    for u in ("c", "a"):
        await q("CREATE (:User {id: $u, orgId: $o})", parameters={"u": f"{u}-{t}", "o": org})
    apps = [f"gm{i}-{t}" for i in range(N)]
    for a in apps:
        await q("CREATE (:App {id: $a, type: 'Drive Workspace', orgId: $o})", parameters={"a": a, "o": org})
        await q("MATCH (u:User {id: $u}), (x:App {id: $a}) CREATE (u)-[:USER_APP_RELATION {sourceUserId: 'src'}]->(x)", parameters={"u": f"c-{t}", "a": a})
        await p.upsert_authenticated_as(f"c-{t}", f"a-{t}", a, org)

    async def links():
        return await q("MATCH ()-[r:AUTHENTICATED_AS]->() WHERE r.connectorId IN $a RETURN r.connectorId AS c", parameters={"a": apps})
    await _race(p, org, apps, links, monkeypatch)
