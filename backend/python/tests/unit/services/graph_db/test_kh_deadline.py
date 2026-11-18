"""A knowledge-hub listing on Neo4j answers by the web app's deadline, while a cold plan goes on being made.

The route sets a deadline inside the web app's own 90 s timeout. One request plans a cold statement and the others
with the same text wait for that plan (`test_kh_single_planner.py`). Every one of those waits, the planner's own
included, ends at the deadline with "took too long, try again": past it the browser has already shown a failure.
The planning itself is not cut short, so the plan is cached for the retry. A statement whose plan is cached carries
the time left as its server-side limit.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.connectors.sources.localKB.api.knowledge_hub_router import _handle_get_nodes
from app.exceptions.graph_db_exceptions import KnowledgeHubListingBusy
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.request_deadline import deadline_after, seconds_left

TEXT = "MATCH (a:App) RETURN a"


class FakeClient:
    """Holds every statement until ``release`` is set; records what each was sent with."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.calls: list[dict[str, Any]] = []
        self.running = 0

    async def execute_query(self, query: str, parameters: dict | None = None, txn_id: str | None = None, **kw: Any) -> list[dict]:  # noqa: ANN401
        self.calls.append({"query": query, "txn_id": txn_id, **kw})
        self.running += 1
        try:
            await self.release.wait()
            return [{"n": len(self.calls)}]
        finally:
            self.running -= 1


def provider(client: Any, wait_s: float = 5.0) -> Neo4jProvider:  # noqa: ANN401
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_deadline")
    p.client = client
    p._KH_PLAN_WAIT_S = wait_s
    return p


async def _planned(p: Neo4jProvider, client: FakeClient) -> None:
    """Plan ``TEXT`` once, with no deadline."""
    client.release.set()
    await p._kh_query(TEXT)
    client.release.clear()


@pytest.mark.asyncio
async def test_a_waiter_is_answered_at_the_request_deadline_when_it_comes_before_the_plan_wait() -> None:
    client = FakeClient()
    p = provider(client, wait_s=30)
    planner = asyncio.create_task(p._kh_query(TEXT))
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    with deadline_after(0.05), pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query(TEXT)
    assert busy.value.timed_out and "took too long" in str(busy.value)
    assert asyncio.get_running_loop().time() - started < 5  # the deadline, not the 30 s plan wait
    assert len(client.calls) == 1 and client.running == 1  # the waiter planned nothing; the planning goes on
    client.release.set()
    assert await planner


@pytest.mark.asyncio
async def test_a_plan_wait_shorter_than_the_deadline_is_busy_not_timed_out() -> None:
    client = FakeClient()
    p = provider(client, wait_s=0.05)
    planner = asyncio.create_task(p._kh_query(TEXT))
    await asyncio.sleep(0.01)
    with deadline_after(30), pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query(TEXT)
    assert not busy.value.timed_out
    client.release.set()
    await planner


@pytest.mark.asyncio
async def test_the_planners_own_request_is_answered_at_its_deadline_and_the_plan_is_still_made() -> None:
    client = FakeClient()
    p = provider(client)
    with deadline_after(0.05), pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query(TEXT)
    assert busy.value.timed_out
    assert client.running == 1  # answering the request did not stop the planning
    assert client.calls[0]["timeout"] == Neo4jProvider._KH_QUERY_TIMEOUT_S  # the planner keeps its own limit
    client.release.set()
    await asyncio.sleep(0.01)
    planning, planned = p._kh_plans()
    assert not planning and len(planned) == 1
    assert await p._kh_query(TEXT)  # the retry finds the plan made and runs at once
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_a_planned_statement_carries_the_time_left_as_its_limit() -> None:
    client = FakeClient()
    p = provider(client)
    await _planned(p, client)
    client.release.set()
    await p._kh_query(TEXT)
    assert client.calls[-1]["timeout"] == Neo4jProvider._KH_QUERY_TIMEOUT_S
    with deadline_after(30):
        await p._kh_query(TEXT)
    assert 29 < client.calls[-1]["timeout"] <= 30


@pytest.mark.asyncio
async def test_a_planned_statement_held_up_by_the_client_is_answered_at_the_deadline() -> None:
    # A pooled connection the client waits for, or a lost one it retries: the request still answers by its deadline.
    client = FakeClient()
    p = provider(client)
    await _planned(p, client)
    with deadline_after(0.05), pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query(TEXT)
    assert busy.value.timed_out
    await asyncio.sleep(0.01)
    assert client.running == 0  # the held statement was given up, not left running in this process


@pytest.mark.asyncio
async def test_nothing_is_sent_after_the_deadline() -> None:
    client = FakeClient()
    p = provider(client)
    with deadline_after(0.001):
        await asyncio.sleep(0.01)
        with pytest.raises(KnowledgeHubListingBusy) as busy:
            await p._kh_query(TEXT)
    assert busy.value.timed_out and client.calls == []


@pytest.mark.asyncio
async def test_a_statement_the_database_ends_at_its_limit_says_it_took_too_long() -> None:
    class Ended(Exception):
        code = "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration"

    async def ended(*_a: object, **_k: object) -> None:
        raise Ended

    with pytest.raises(KnowledgeHubListingBusy) as busy:
        await provider(SimpleNamespace(execute_query=ended))._kh_query("RETURN 1")
    assert busy.value.timed_out and not busy.value.out_of_memory


@pytest.mark.asyncio
async def test_the_route_lists_under_the_knowledge_hub_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_HUB_DEADLINE_SECONDS", "40")
    seen: list[float | None] = []

    async def get_nodes(**_kwargs: object) -> SimpleNamespace:
        seen.append(seconds_left())
        return SimpleNamespace(success=False, error="The listing took too long. Please try again in a moment.", errorCode=503)

    request = MagicMock()
    request.state.user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    service = SimpleNamespace(get_nodes=get_nodes)
    kwargs: dict[str, Any] = {
        "parent_id": None, "parent_type": None, "only_containers": False, "page": 1, "limit": 50,
        "sort_by": "name", "sort_order": "asc", "q": None, "node_types": None, "record_types": None,
        "origins": None, "connector_ids": None, "indexing_status": None, "created_at": None,
        "updated_at": None, "size": None, "flattened": None, "include": None, "cursor": None,
    }
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await _handle_get_nodes(request=request, knowledge_hub_service=service, **kwargs)  # type: ignore[arg-type]
    assert seen and seen[0] is not None and 39 < seen[0] <= 40
    assert seconds_left() is None
    # Busy, not broken: the web app is told to come back, with the reason.
    assert refused.value.status_code == 503 and refused.value.headers == {"Retry-After": "5"}
    assert "took too long" in refused.value.detail
