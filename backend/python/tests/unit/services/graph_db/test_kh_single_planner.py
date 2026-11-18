"""One planner per knowledge-hub statement text, in the Neo4j provider.

A cold listing statement can take Neo4j minutes to plan. Requests with the same text wait for the one planning it
instead of each planning a copy; a wait past its budget is busy (503), while the planner runs on so the plan is cached.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.exceptions.graph_db_exceptions import KnowledgeHubListingBusy
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


class FakeClient:
    """Holds every statement until ``release`` is set; counts the ones running at once."""

    def __init__(self, fail_first: bool = False) -> None:
        self.release = asyncio.Event()
        self.calls: list[dict] = []
        self.running = 0
        self.max_running = 0
        self.fail_first = fail_first

    async def execute_query(self, query, parameters=None, txn_id=None, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls.append({"query": query, "txn_id": txn_id, **kw})
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await self.release.wait()
            if self.fail_first and len(self.calls) == 1:
                raise RuntimeError("planner failed")
            return [{"n": len(self.calls)}]
        finally:
            self.running -= 1


def provider(client: FakeClient, wait_s: float = 5.0) -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_single_planner")
    p.client = client
    p._KH_PLAN_WAIT_S = wait_s
    return p


@pytest.mark.asyncio
async def test_requests_with_one_text_wait_for_the_single_planner() -> None:
    client = FakeClient()
    p = provider(client)
    requests = [asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a")) for _ in range(5)]
    await asyncio.sleep(0.05)
    assert client.max_running == 1  # one copy plans; four wait
    client.release.set()
    rows = await asyncio.gather(*requests)
    assert all(r for r in rows)
    assert client.calls[0]["timeout"] == Neo4jProvider._KH_QUERY_TIMEOUT_S
    # Once planned, the same text runs at once and concurrently.
    client.max_running = 0
    client.release.clear()
    again = [asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a")) for _ in range(3)]
    await asyncio.sleep(0.05)
    assert client.max_running == 3
    client.release.set()
    await asyncio.gather(*again)


@pytest.mark.asyncio
async def test_other_texts_are_not_held_by_a_planner() -> None:
    client = FakeClient()
    p = provider(client)
    first = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    other = asyncio.create_task(p._kh_query("MATCH (r:Record) RETURN r"))
    await asyncio.sleep(0.05)
    assert client.max_running == 2
    client.release.set()
    await asyncio.gather(first, other)


@pytest.mark.asyncio
async def test_a_wait_past_its_budget_is_busy_and_the_planner_runs_on() -> None:
    client = FakeClient()
    p = provider(client, wait_s=0.05)
    planner = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    await asyncio.sleep(0.01)
    with pytest.raises(KnowledgeHubListingBusy):
        await p._kh_query("MATCH (a:App) RETURN a")
    assert len(client.calls) == 1  # the waiter planned nothing of its own
    assert not planner.done()  # the planner's own request is not cut off by the waiters' budget
    planner.cancel()  # its client gives up: the planning runs on
    client.release.set()
    await asyncio.sleep(0.01)
    assert await p._kh_query("MATCH (a:App) RETURN a")  # the plan the abandoned request paid for is used
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_failed_planner_hands_planning_to_a_waiter() -> None:
    client = FakeClient(fail_first=True)
    p = provider(client)
    leader = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    follower = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    await asyncio.sleep(0.05)
    client.release.set()
    with pytest.raises(RuntimeError, match="planner failed"):
        await leader
    assert await follower
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_statement_past_the_server_limit_is_busy() -> None:
    p = provider(FakeClient())

    async def timed_out(*_a: object, **_k: object) -> None:
        raise _coded("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration")

    p.client = SimpleNamespace(execute_query=timed_out)
    with pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query("RETURN 1")
    assert busy.value.out_of_memory is False


@pytest.mark.asyncio
async def test_inside_a_transaction_the_statement_runs_as_before() -> None:
    client = FakeClient()
    client.release.set()
    p = provider(client)
    await p._kh_query("RETURN 1", txn_id="txn-1")
    assert client.calls == [{"query": "RETURN 1", "txn_id": "txn-1", "plain": True}]


def _coded(code: str) -> Exception:
    error = RuntimeError(code)
    error.code = code  # type: ignore[attr-defined]
    return error
