"""The connector's creator is linked to the account the connector signs in as only when its listing proves them
absent, on Neo4j and ArangoDB, through the real processor. The PostgreSQL twin is postgres/test_authenticator_link_pg.py;
the cases themselves are in authenticator_link_support.py.

A creator the listing names has their own source account, and the account the credentials act as is someone else's:
linked to it, they would read everything it reads. What the store answers is the links: who holds one for which
connector, so that a former authenticator's goes and another connector's stays.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD; without them each case skips.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from functools import partial
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.graph_db import authenticator_link_support as cases
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "authenticator_link_it"

logger = logging.getLogger("authenticator-link-it")


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[cases.LinkWorld]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        w = cases.plan_world(graph, f"org-auth-link-{run}", run)

        async def remove() -> None:
            for connector_id in (w.connector_id, w.other_connector_id):
                with contextlib.suppress(Exception):
                    await graph.delete_connector_instance(connector_id, w.org_id)
            with contextlib.suppress(Exception):
                service = await graph.get_user_by_email(w.service_email, org_id=w.org_id)
                if service is not None:
                    await graph.delete_nodes_and_edges([service.id], CollectionNames.USERS.value)
            for collection, keys in w.keys().items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, collection)
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges([w.org_id], CollectionNames.ORGS.value)

        cleanup.push_async_callback(remove)      # before seeding: a seed that fails partway is cleaned up too
        await cases.seed_world(w)
        yield w


def _broken_app_read(w: cases.LinkWorld) -> contextlib.AbstractContextManager:
    """The app read fails below the provider, where its own error handling runs."""
    if hasattr(w.graph, "http_client"):
        return cases.failing(w.graph.http_client, "get_document", when=lambda collection, *_a, **_k: collection == "apps")
    return cases.failing(w.graph.client, "execute_query", when=lambda query, *_a, **_k: ":App" in query)


async def test_a_creator_the_listing_names_later_loses_the_link(world: cases.LinkWorld) -> None:
    await cases.a_creator_listed_later_loses_the_link(world)


async def test_a_listed_creator_is_never_linked_even_after_the_listing_drops_them(world: cases.LinkWorld) -> None:
    await cases.a_listed_creator_is_never_linked(world)


async def test_a_cut_listing_makes_no_link_and_removes_none(world: cases.LinkWorld) -> None:
    await cases.a_cut_listing_makes_no_link_and_removes_none(world)


async def test_a_cut_listing_that_names_the_creator_unlinks_them(world: cases.LinkWorld) -> None:
    await cases.a_cut_listing_that_names_the_creator_unlinks_them(world)


async def test_a_membership_with_a_source_account_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.a_source_account_membership_is_not_a_listing(world)


async def test_an_external_collaborator_membership_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.an_external_collaborator_is_not_listed(world)


async def test_a_membership_with_no_source_account_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.a_membership_without_a_source_account_is_not_a_listing(world)


async def test_a_listing_by_another_connector_does_not_count(world: cases.LinkWorld) -> None:
    await cases.a_listing_by_another_connector_does_not_count(world)


async def test_a_former_authenticators_link_goes_on_a_cut_listing(world: cases.LinkWorld) -> None:
    await cases.a_former_authenticators_link_goes_on_a_cut_listing(world)


async def test_a_former_authenticators_link_to_another_connector_stays(world: cases.LinkWorld) -> None:
    await cases.a_former_authenticators_link_to_another_connector_stays(world)


async def test_a_failed_app_read_raises_only_when_asked_to(world: cases.LinkWorld) -> None:
    await cases.a_failed_app_read_raises_only_when_asked_to(world, partial(_broken_app_read, world))


async def test_a_failed_app_read_stops_the_link_decision(world: cases.LinkWorld) -> None:
    await cases.a_failed_app_read_stops_the_link_decision(world, partial(_broken_app_read, world))
