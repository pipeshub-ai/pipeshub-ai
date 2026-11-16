"""A brand-new organisation's first knowledge-base create succeeds before its sign-up events are consumed.

Sign-up stores the organisation and its admin in the document store and queues orgCreated and userAdded; until the
connectors service consumes them the graph has neither, and the first create answered 404 "User not found for
user_id". Now the caller's own org and user are written from Node's live records through those events' handlers,
then the create goes ahead. Two first calls at once, and the events arriving afterwards, leave one user and one
default knowledge base.

Runs in backend-matrix on both graph jobs (NEO4J_IT_URI, ARANGO_IT_URL); the PostgreSQL case is
tests/integration/graph_db/postgres/test_fresh_org_first_call.py.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.graph_db.fresh_org_support import (
    a_failed_default_write_is_made_by_the_redelivery,
    a_first_request_after_add_and_delete_writes_nothing,
    a_new_users_writers_race,
    a_readded_user_keeps_one_node,
    a_removed_default_is_not_made_again_under_its_key,
    first_calls_after_signup,
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

ARANGO_DB = "fresh_org_first_call_it"


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(provider, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        org_id = f"org-fresh-{uuid.uuid4().hex[:10]}"

        async def remove() -> None:
            apps = await provider.get_nodes_by_filters(CollectionNames.APPS.value, {"orgId": org_id})
            users = await provider.get_nodes_by_filters(CollectionNames.USERS.value, {"orgId": org_id})
            for collection, keys in (
                (CollectionNames.APPS.value, [a.get("id") or a.get("_key") for a in apps]),
                (CollectionNames.USERS.value, [u.get("id") or u.get("_key") for u in users]),
                (CollectionNames.TEAMS.value, [f"all_{org_id}"]),
                (CollectionNames.ORGS.value, [org_id]),
            ):
                with contextlib.suppress(Exception):
                    await provider.delete_nodes_and_edges(keys, collection)

        cleanup.push_async_callback(remove)
        yield provider, org_id


@pytest.mark.parametrize("events_after_creates", [False, True], ids=["events-during-creates", "events-after-creates"])
async def test_the_first_creates_after_signup_succeed(graph: tuple[Any, str], events_after_creates: bool) -> None:
    await first_calls_after_signup(*graph, events_after_creates=events_after_creates)


async def test_any_number_of_writers_of_a_new_user_write_one_user_and_one_default(graph: tuple[Any, str]) -> None:
    await a_new_users_writers_race(*graph)


@pytest.mark.parametrize("older_random_key", [True, False], ids=["older-random-key", "derived-key"])
async def test_a_user_readded_under_a_new_address_keeps_their_one_node(graph: tuple[Any, str], older_random_key: bool) -> None:
    await a_readded_user_keeps_one_node(*graph, older_random_key=older_random_key)


async def test_a_first_request_after_add_and_delete_writes_nothing(graph: tuple[Any, str]) -> None:
    await a_first_request_after_add_and_delete_writes_nothing(*graph)


async def test_a_removed_default_is_not_made_again_under_its_key(graph: tuple[Any, str]) -> None:
    await a_removed_default_is_not_made_again_under_its_key(*graph)


async def test_a_failed_default_write_is_made_by_the_redelivery(graph: tuple[Any, str]) -> None:
    await a_failed_default_write_is_made_by_the_redelivery(*graph)
