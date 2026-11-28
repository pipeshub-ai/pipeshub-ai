"""Entity search and the connectors page lists honour a connector's access limit on PostgreSQL, for every access-limit scenario and
every generated world (``tests/permissions/limit_reach.py``). The lists read the org's app edges, which the conformance worlds do not
write, so the test adds them. The team connector list's limit is checked by hand in ``test_connectors_teams.py``."""
from __future__ import annotations

import pytest

from app.core.org_context import OrgContext, bind
from tests.permissions import gen
from tests.permissions.graph import Namespace, compile_world
from tests.permissions.limit_reach import list_findings, org_apps, reach_findings
from tests.permissions.test_access_limit_reach import generated_worlds, limit_worlds

from .test_conformance import PgLayer, _hex

pytestmark = [pytest.mark.integration, pytest.mark.timeout(3600)]

CHUNK = 25


@pytest.mark.parametrize("kind", ["scenarios", *sorted(gen.GENERATORS)])
async def test_entity_search_reaches_a_limited_connector_exactly_as_its_limit_says(pg_provider, pg_db, kind: str) -> None:
    worlds = limit_worlds() if kind == "scenarios" else generated_worlds(kind)
    layer = PgLayer(pg_provider, pg_db)
    found: list[str] = []
    try:
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                ns: Namespace = await layer._ns(world)
                org = _hex(ns.org)

                async def entity(user: str, org: str = org) -> dict | None:
                    with bind(OrgContext(org, None, "job", "job:test", "t")):
                        return await pg_provider.get_entity_access_context(user, org)

                found += [f"postgres {kind}-{start + offset}: {b}"
                          for b in await reach_findings(world, compile_world(world, layer._prefix[world]), ns, entity)]
            await layer.release(chunk)
    finally:
        await layer.release(list(layer._prefix))
    assert not found, "\n".join(found[:40])


@pytest.mark.parametrize("kind", ["scenarios", *sorted(gen.GENERATORS)])
async def test_the_connector_lists_show_a_limited_connector_exactly_as_its_limit_says(pg_provider, pg_db, kind: str) -> None:
    worlds = limit_worlds() if kind == "scenarios" else generated_worlds(kind)
    layer = PgLayer(pg_provider, pg_db)
    found: list[str] = []
    try:
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                ns: Namespace = await layer._ns(world)
                org = _hex(ns.org)
                graph = compile_world(world, layer._prefix[world])

                async def listed(user: str, limited: bool, org: str = org) -> dict[str, set[str]]:
                    with bind(OrgContext(org, None, "job", "job:test", "t")):
                        mine = await pg_provider.get_user_connector_instances("apps", user, org, "team", "personal", apply_access_limit=limited)
                        page, _ = await pg_provider.get_filtered_connector_instances("apps", "orgAppRelation", org, user, limit=10000,
                                                                                     apply_access_limit=limited)
                    return {"instances": {a["_key"] for a in mine}, "filtered": {a["_key"] for a in page}}

                with bind(OrgContext(org, None, "job", "job:test", "t")):
                    if apps := org_apps(graph, ns):
                        await pg_provider.batch_create_edges([{"from_id": org, "from_collection": "organizations", "to_id": a, "to_collection": "apps"}
                                                              for a in apps], "orgAppRelation")
                found += [f"postgres {kind}-{start + offset}: {b}" for b in await list_findings(world, graph, ns, listed)]
            await layer.release(chunk)
    finally:
        await layer.release(list(layer._prefix))
    assert not found, "\n".join(found[:40])
