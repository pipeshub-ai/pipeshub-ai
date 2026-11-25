"""The startup removal (of a listed creator's link) costs about the same however many orgs there are, on a real ArangoDB and a real Neo4j.

It runs on every start of the connectors service, before syncs resume, so its cost is paid on every restart. A read per
org of every org's apps made it grow with the square of the orgs (13 minutes at 2,000 orgs on Neo4j). The one pass over
the links does not. The bound is generous, for a loaded CI host; the unit test counts the reads exactly.

WSLEAK_ORGS sets the number of orgs (default 2000: 4 apps and 3 links each).
"""

from __future__ import annotations

import logging
import os
import time
import uuid

import pytest

from app.migrations.workspace_admin_link_migration import (
    WorkspaceAdminLinkMigrationService,
)

from .test_connector_entities import arango_provider, neo4j_provider  # noqa: F401  (fixtures)
from .test_workspace_admin_link_store_real_backends import (
    _Config,
    _ensure_arango_collections,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(600)]

ORGS = int(os.environ.get("WSLEAK_ORGS", "2000"))
BOUND_SECONDS = 30.0
TYPES = ("Gmail Workspace", "Drive Workspace", "Dropbox", "Jira")


def _world(t: str) -> tuple[list[str], list[dict]]:
    orgs = [f"o{i}-{t}" for i in range(ORGS)]
    return orgs, [{"k": f"a{j}-{o}", "t": ty, "o": o} for o in orgs for j, ty in enumerate(TYPES)]


async def _start(p, label: str) -> tuple[dict, float, dict, float]:
    service = WorkspaceAdminLinkMigrationService(p, _Config(), logging.getLogger("wsleak-it"))
    t0 = time.monotonic()
    first = await service.migrate()
    t1 = time.monotonic()
    again = await service.migrate()
    t2 = time.monotonic()
    print(f"\nWSLEAK-SCALE {label} orgs={ORGS} apps={ORGS * len(TYPES)} first_start={t1 - t0:.2f}s removed={first['removed']} "
          f"steady_start={t2 - t1:.2f}s removed={again['removed']}")
    return first, t1 - t0, again, t2 - t1


async def test_arango_startup_cost_at_scale(arango_provider) -> None:  # noqa: F811
    p = arango_provider
    await _ensure_arango_collections(p)
    t = uuid.uuid4().hex[:6]
    orgs, apps = _world(t)
    await _ensure_arango_collections(p)
    import aiohttp

    client = p.http_client
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth("root", client.password)) as sess:
        async with sess.post(f"{client.base_url}/_db/{client.database}/_api/collection", json={"name": "userAppRelation", "type": 3}) as r:
            assert r.status in (200, 201, 409)
    await p.execute_query("FOR o IN @o INSERT {_key: o, isActive: true} INTO organizations", bind_vars={"o": orgs})
    await p.execute_query("FOR o IN @o FOR pre IN ['c-', 's-'] INSERT {_key: CONCAT(pre, o), orgId: o} INTO users", bind_vars={"o": orgs})
    await p.execute_query("FOR a IN @a INSERT {_key: a.k, type: a.t, orgId: a.o} INTO apps", bind_vars={"a": apps})
    await p.execute_query(
        "FOR a IN @a INSERT {_from: CONCAT('users/c-', a.o), _to: CONCAT('apps/', a.k), sourceUserId: 'src'} INTO userAppRelation",
        bind_vars={"a": apps},
    )
    await p.execute_query(
        "FOR a IN @a INSERT {_key: a.k, _from: CONCAT('users/c-', a.o), _to: CONCAT('users/s-', a.o), connectorId: a.k, orgId: a.o} INTO authenticatedAs",
        bind_vars={"a": apps},
    )
    first, s1, again, s2 = await _start(p, "arango")
    left = await p.execute_query("FOR l IN authenticatedAs FILTER l.orgId IN @o RETURN l.connectorId", bind_vars={"o": orgs})
    assert first["removed"] >= 3 * ORGS and again["removed"] == 0
    assert sorted(left) == sorted(a["k"] for a in apps if a["t"] == "Jira"), "only the three types lose their link"
    assert s1 < BOUND_SECONDS and s2 < BOUND_SECONDS


async def test_neo4j_startup_cost_at_scale(neo4j_provider) -> None:  # noqa: F811
    p = neo4j_provider
    t = uuid.uuid4().hex[:6]
    orgs, apps = _world(t)
    q = p.client.execute_query
    await q("CREATE INDEX wsleak_app_id IF NOT EXISTS FOR (a:App) ON (a.id)")
    await q("CREATE INDEX wsleak_link_connector IF NOT EXISTS FOR ()-[r:AUTHENTICATED_AS]-() ON (r.connectorId)")
    await q("UNWIND $o AS o CREATE (:Organization {id: o, isActive: true}) CREATE (:User {id: 'c-' + o, orgId: o}) CREATE (:User {id: 's-' + o, orgId: o})", parameters={"o": orgs})
    await q("UNWIND $a AS a CREATE (:App {id: a.k, type: a.t, orgId: a.o})", parameters={"a": apps})
    await q("CALL db.awaitIndexes(300)")
    await q(
        "UNWIND $a AS a MATCH (c:User {id: 'c-' + a.o}), (x:App {id: a.k}) CREATE (c)-[:USER_APP_RELATION {sourceUserId: 'src'}]->(x)",
        parameters={"a": apps},
    )
    await q(
        "UNWIND $a AS a MATCH (c:User {id: 'c-' + a.o}), (s:User {id: 's-' + a.o}) "
        "CREATE (c)-[:AUTHENTICATED_AS {connectorId: a.k, orgId: a.o}]->(s)",
        parameters={"a": apps},
    )
    first, s1, again, s2 = await _start(p, "neo4j")
    left = await q("MATCH ()-[r:AUTHENTICATED_AS]->() WHERE r.orgId IN $o RETURN r.connectorId AS c", parameters={"o": orgs})
    assert first["removed"] >= 3 * ORGS and again["removed"] == 0
    assert sorted(r["c"] for r in left) == sorted(a["k"] for a in apps if a["t"] == "Jira")
    assert s1 < BOUND_SECONDS and s2 < BOUND_SECONDS
