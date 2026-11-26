"""G-49 / C13.01: the seeded fixture tenant on a real Neo4j 5.26 and a real ArangoDB 3.12. Each user reads exactly
what the tenant declares (and the in-memory fake decides), through main's one access check, the single check and the
search filter, so the offline P0 journeys' access answers are the ones a real graph gives.

Needs the graph services; fails when ``NEO4J_IT_URI`` / ``ARANGO_IT_URL`` is set and the server is unusable, skips
otherwise (``tests/integration/real_graph.py``):

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d --wait neo4j-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_g49_seeded_tenant_real_backends.py -m integration

The store lane (``python -m tests.integration.store_lane``) starts its own Neo4j and Qdrant and runs this.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.seeded_tenant import SeededGraph, load_tenant

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

LOG = logging.getLogger("tests.g49_tenant")
ARANGO_DB = "g49_tenant_it"


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (connect_neo4j(LOG, monkeypatch) if request.param == "neo4j" else connect_arango(LOG, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


async def _remove(graph: IGraphDBProvider, seeded: SeededGraph) -> None:
    suffix = f"-{seeded.tag}"
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id ENDS WITH $suffix DETACH DELETE n", parameters={"suffix": suffix}
        )
        return
    http = graph.http_client  # type: ignore[attr-defined]
    for name in (CollectionNames.PERMISSION, CollectionNames.BELONGS_TO, CollectionNames.USER_APP_RELATION,
                 CollectionNames.ORG_APP_RELATION, CollectionNames.IS_OF_TYPE):
        await http.execute_aql(
            f"FOR e IN {name.value} FILTER LIKE(e._from, @s) OR LIKE(e._to, @s) REMOVE e IN {name.value}",
            {"s": f"%{suffix}"},
        )
    for name in (CollectionNames.RECORDS, CollectionNames.FILES, CollectionNames.USERS, CollectionNames.GROUPS,
                 CollectionNames.APPS, CollectionNames.ORGS):
        await http.execute_aql(
            f"FOR d IN {name.value} FILTER LIKE(d._key, @s) REMOVE d IN {name.value}", {"s": f"%{suffix}"}
        )


async def test_g49_each_tenant_user_reads_what_the_tenant_declares(graph: IGraphDBProvider) -> None:
    tenant = load_tenant()
    seeded = await tenant.seed_graph(graph)
    try:
        every = list(seeded.records.values())
        for user in tenant.users:
            key, uid = seeded.users[user.id]
            decided = (await graph.check_access(key, seeded.org, node_ids=every)).node_ids
            assert seeded.tenant_ids(decided) == tenant.readable(user.id), user.id
            single = {
                rid for rid in every
                if await graph.check_record_access_with_details(uid, seeded.org, rid) is not None
            }
            assert single == set(decided), user.id
            searchable = await graph.filter_accessible_record_ids(every, uid, seeded.org)
            assert searchable <= single, user.id
    finally:
        await _remove(graph, seeded)
