"""Entity search honours a connector's access limit on PostgreSQL, for every access-limit scenario and every generated world
(``tests/permissions/limit_reach.py``). The team connector list's limit is checked by hand in ``test_connectors_teams.py``: it
reads the org's app edges, which the conformance worlds do not write."""
from __future__ import annotations

import pytest

from app.core.org_context import OrgContext, bind
from tests.permissions import gen
from tests.permissions.graph import Namespace, compile_world
from tests.permissions.limit_reach import reach_findings
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
