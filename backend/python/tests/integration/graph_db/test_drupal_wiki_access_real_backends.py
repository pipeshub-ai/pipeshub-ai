"""Drupal Wiki access on real Neo4j and ArangoDB: each user reads in PipesHub what the wiki lets them read.

See ``drupal_wiki_world`` for what runs for real. Runs on Neo4j and ArangoDB (backend-matrix). Environment:
NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.graph_db.drupal_wiki_world import (
    ALICE,
    BOB,
    CAROL,
    DrupalWorld,
    a_deleted_page_goes_on_the_next_sync,
    a_disabled_reader_loses_every_space_until_enabled_again,
    a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not,
    logger,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "drupal_wiki_access_it"


async def _remove(world: DrupalWorld) -> None:
    graph = world.graph
    keys = [world.org_id, world.connector_id, *world.user_ids.values()]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids OR n.orgId = $o DETACH DELETE n",
            parameters={"c": world.connector_id, "ids": keys, "o": world.org_id},
        )
        return
    for name in await graph.http_client.execute_aql(
            "FOR c IN COLLECTIONS() FILTER NOT STARTS_WITH(c.name, '_') RETURN c.name", bind_vars={}):
        await graph.http_client.execute_aql(
            f"FOR d IN `{name}` FILTER d.connectorId == @c OR d.orgId == @o OR d._key IN @k "
            f"OR STARTS_WITH(d._from, 'users/') AND PARSE_IDENTIFIER(d._from).key IN @k REMOVE d IN `{name}`",
            bind_vars={"c": world.connector_id, "o": world.org_id, "k": keys},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DrupalWorld]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        built = DrupalWorld(graph, f"org-dw-{run}", f"drupal-wiki-{run}",
                            {email: f"user-{run}-{i}" for i, email in enumerate((ALICE, BOB, CAROL))})
        cleanup.push_async_callback(_remove, built)
        await built.seed()
        yield built


async def test_a_disabled_or_removed_reader_reads_nothing(world: DrupalWorld) -> None:
    await a_disabled_reader_loses_every_space_until_enabled_again(world)


async def test_a_deleted_page_is_removed_by_the_next_sync(world: DrupalWorld) -> None:
    await a_deleted_page_goes_on_the_next_sync(world)


async def test_a_shared_page_is_read_in_both_spaces_and_its_sub_page_only_in_its_own(world: DrupalWorld) -> None:
    await a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not(world)
