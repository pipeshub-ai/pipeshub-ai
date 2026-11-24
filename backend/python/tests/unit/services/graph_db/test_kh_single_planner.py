"""One planner per knowledge-hub statement text, in the Neo4j provider.

A cold listing statement can take Neo4j minutes to plan. Requests with the same text wait for the one planning it
instead of each planning a copy; a wait past its budget is busy (503), while the planner runs on so the plan is cached.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable
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

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
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
    await asyncio.sleep(0.01)
    assert client.running == 1  # the waiter giving up did not stop the planning
    assert not planner.done()  # the planner's own request is not cut off by the waiters' budget
    planner.cancel()  # its client gives up
    await asyncio.sleep(0.01)
    assert client.running == 1  # and the planning still runs on
    client.release.set()
    await asyncio.sleep(0.01)
    planning, planned = p._kh_plans()
    assert not planning and len(planned) == 1  # the abandoned request's plan counts as made
    assert await p._kh_query("MATCH (a:App) RETURN a")  # and is used: this one runs at once
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


@pytest.mark.asyncio
async def test_a_listing_statement_keeps_its_plan_unless_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    client.release.set()
    p = provider(client)
    await p._kh_query("MATCH (a:App) RETURN a")  # planned
    await p._kh_query("MATCH (a:App) RETURN a")  # run
    assert [c["query"] for c in client.calls] == ["CYPHER replan=skip MATCH (a:App) RETURN a"] * 2
    monkeypatch.setattr(Neo4jProvider, "_KH_KEEP_PLANS", False)
    await p._kh_query("MATCH (a:App) RETURN a")
    assert client.calls[-1]["query"] == "MATCH (a:App) RETURN a"


def _coded(code: str) -> Exception:
    error = RuntimeError(code)
    error.code = code  # type: ignore[attr-defined]
    return error


class StepClient:
    """Each statement takes the next step: (seconds, error or None)."""

    def __init__(self, steps: list[tuple[float, Exception | None]]) -> None:
        self.steps = steps
        self.calls = 0
        self.finished: list[float] = []  # when each statement's step ended, on the clock the provider reads

    async def execute_query(self, query, parameters=None, txn_id=None, **kw) -> list[dict]:
        seconds, error = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        await asyncio.sleep(seconds)
        self.finished.append(time.monotonic())
        if error is not None:
            raise error
        return [{"ok": True}]


@pytest.mark.asyncio
async def test_a_waiter_keeps_one_budget_across_a_failed_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    # The planner fails; one waiter takes over (and runs long), the other waits on it with what is left.
    deadlines: dict[asyncio.Task, list[float]] = {}
    wait_for = asyncio.wait_for

    async def spy(awaitable: Awaitable[object], timeout: float) -> object:
        # The deadline each wait aims at, on the clock the provider reads: independent of how fast the host runs.
        deadlines.setdefault(asyncio.current_task(), []).append(time.monotonic() + timeout)
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", spy)
    p = provider(StepClient([(0.15, RuntimeError("planner failed")), (5.0, None)]), wait_s=0.25)
    planner = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    await asyncio.sleep(0.01)
    waiters = [asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a")) for _ in range(2)]
    done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_EXCEPTION)
    assert any(isinstance(t.exception(), KnowledgeHubListingBusy) for t in done), "the waiter left behind is busy"
    waited_twice = [d for d in deadlines.values() if len(d) == 2]
    assert len(waited_twice) == 1
    first, second = waited_twice[0]
    assert second == pytest.approx(first, abs=0.01)  # the same deadline, not a fresh budget after the failure
    for t in [planner, *pending]:
        t.cancel()
    await asyncio.gather(planner, *pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_waiters_out_of_memory_is_timed_from_its_own_run() -> None:
    # The waiter waits for the plan, then its own statement finds the memory pool spent at once.
    oom = _coded("Neo.TransientError.General.MemoryPoolOutOfMemoryError")
    client = StepClient([(0.5, None), (0.0, oom)])
    p = provider(client)
    planner = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a"))
    await asyncio.sleep(0.01)
    started = time.monotonic()
    with pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query("MATCH (a:App) RETURN a")
    answered = time.monotonic()
    await planner
    assert busy.value.out_of_memory is True
    planned = client.finished[0]
    assert started < planned  # this request did wait for the plan
    # Its wait ends no sooner than the planner's statement, so a run timed from after the wait fits between the two,
    # however fast or slow the host. Counting the wait would make a 503 to retry a 422 "too large".
    assert busy.value.ran_s is not None and busy.value.ran_s <= answered - planned


@pytest.mark.asyncio
async def test_a_parameter_of_another_type_is_another_plan() -> None:
    # Neo4j plans a null sort key apart from a string one: the second is not held behind the first.
    client = FakeClient()
    p = provider(client)
    none_key = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a", {"sortKey": None}))
    str_key = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a", {"sortKey": "b"}))
    same_type = asyncio.create_task(p._kh_query("MATCH (a:App) RETURN a", {"sortKey": "c"}))
    await asyncio.sleep(0.05)
    assert client.max_running == 2  # one planner per type; the second string waits for the first
    client.release.set()
    await asyncio.gather(none_key, str_key, same_type)
