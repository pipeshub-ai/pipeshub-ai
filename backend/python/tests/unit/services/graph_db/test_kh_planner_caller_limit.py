"""The access check keeps its own statement limit inside the Neo4j knowledge-hub planner.

Listing statements share one planner per statement text and may wait a minute for it. An access check passes its
own limit (2 s on a read): its wait for another request's plan, and every statement it runs, stay within that limit,
so the check answers or fails closed in time. A request without a limit keeps the planner's defaults.
"""

import asyncio
import logging

import pytest

from app.exceptions.graph_db_exceptions import KnowledgeHubListingBusy
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.settle import settle

STATEMENT = "MATCH (r:Record) RETURN r"


class HeldClient:
    """Holds every statement until ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls: list[dict] = []

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
        self.calls.append({"query": query, "txn_id": txn_id, **kw})
        await self.release.wait()
        return [{"n": len(self.calls)}]


def provider(client: HeldClient, wait_s: float = 5.0) -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_planner_caller_limit")
    p.client = client
    p._KH_PLAN_WAIT_S = wait_s
    return p


@pytest.mark.asyncio
async def test_a_check_plans_a_cold_statement_under_its_own_limit() -> None:
    client = HeldClient()
    client.release.set()
    p = provider(client)

    await p._kh_query(STATEMENT, timeout=2.0)

    assert [call.get("timeout") for call in client.calls] == [2.0]


@pytest.mark.asyncio
async def test_a_check_waits_for_another_requests_plan_no_longer_than_its_limit() -> None:
    client = HeldClient()
    # The planner's own wait outlasts the hang backstop: a check that waited for it would never settle.
    p = provider(client, wait_s=3600.0)
    listing = asyncio.create_task(p._kh_query(STATEMENT))
    await asyncio.sleep(0)

    with pytest.raises(KnowledgeHubListingBusy):
        await settle(p._kh_query(STATEMENT, timeout=0.05))
    # It gave up while the plan it waited for was still being made.
    assert not listing.done()

    # The listing's planner runs on under the planner's own limit, and its plan serves the next check.
    client.release.set()
    await listing
    assert client.calls[0]["timeout"] == Neo4jProvider._KH_QUERY_TIMEOUT_S
    await p._kh_query(STATEMENT, timeout=2.0)
    assert client.calls[-1]["timeout"] == 2.0
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_check_inside_a_transaction_keeps_its_limit() -> None:
    client = HeldClient()
    client.release.set()
    p = provider(client)

    await p._kh_query("RETURN 1", txn_id="txn-1", timeout=2.0)

    assert client.calls == [{"query": "RETURN 1", "txn_id": "txn-1", "plain": True, "timeout": 2.0}]


@pytest.mark.asyncio
async def test_a_listing_without_a_limit_keeps_the_planner_defaults() -> None:
    client = HeldClient()
    client.release.set()
    p = provider(client)

    await p._kh_query(STATEMENT)
    await p._kh_query(STATEMENT)

    assert [call["timeout"] for call in client.calls] == [Neo4jProvider._KH_QUERY_TIMEOUT_S] * 2
