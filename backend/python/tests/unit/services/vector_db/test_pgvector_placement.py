"""The default placement policy: ask once, wait for the worker, remember, fail with the command to run."""

from __future__ import annotations

import asyncio
import contextvars
from typing import TYPE_CHECKING, Never

import pytest

from app.core import org_context
from app.services.vector_db.pgvector.errors import OrgMismatch, PgVectorError
from app.services.vector_db.pgvector.placement import OwnLeafPolicy

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "a" * 24


@pytest.fixture(autouse=True)
def in_org() -> Iterator[None]:
    with org_context.bind(org_context.OrgContext(ORG, None, "service", "test", "-")):
        yield


class FakeRun:
    """Stands in for KernelDB.run: records the requests and answers the status as scripted."""

    def __init__(self, statuses: list[dict | None]) -> None:
        self.statuses, self.asked, self.polled, self.opts = list(statuses), 0, 0, []

    async def run(self, fn, **opts) -> object:
        self.opts.append(opts)
        sql_hits = []

        class Tx:
            async def fetchval(_self, sql, *a) -> None:
                sql_hits.append("ask")

            async def fetchrow(_self, sql, *a) -> dict | None:
                sql_hits.append("status")
                return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

        out = await fn(Tx())
        self.asked += sql_hits.count("ask")
        self.polled += sql_hits.count("status")
        return out


@pytest.mark.asyncio
async def test_concurrent_first_writes_share_one_wait_and_the_org_is_remembered() -> None:
    db = FakeRun([None, {"placed": False, "note": None}, {"placed": True, "note": None}])
    policy = OwnLeafPolicy(db, wait=5, poll=0.01)
    await asyncio.gather(*[policy.before_first_write(ORG) for _ in range(10)])
    assert db.asked == 1 and db.polled == 3
    await policy.before_first_write(ORG)                      # placed: no database round trip
    assert db.asked == 1 and db.polled == 3
    assert all(o.get("custom_plan") is True for o in db.opts)


@pytest.mark.asyncio
async def test_a_refusal_is_raised_at_once_with_its_reason() -> None:
    policy = OwnLeafPolicy(FakeRun([{"placed": False, "note": "run the offline promotion"}]), wait=5, poll=0.01)
    with pytest.raises(PgVectorError, match="offline promotion"):
        await policy.before_first_write(ORG)


@pytest.mark.asyncio
async def test_no_worker_means_a_clear_error_never_a_write_into_the_shared_tree() -> None:
    policy = OwnLeafPolicy(FakeRun([{"placed": False, "note": None}]), wait=0.1, poll=0.02)
    with pytest.raises(PgVectorError, match="placement did not finish"):
        await policy.before_first_write(ORG)
    with pytest.raises(PgVectorError, match="placement did not finish"):          # and it asks again next time
        await policy.before_first_write(ORG)


@pytest.mark.asyncio
async def test_another_org_than_the_one_the_call_runs_for_is_refused_before_any_statement() -> None:
    db = FakeRun([{"placed": True, "note": None}])
    with pytest.raises(OrgMismatch):
        await OwnLeafPolicy(db, wait=1, poll=0.01).before_first_write("b" * 24)
    assert db.asked == 0 and db.polled == 0


@pytest.mark.asyncio
async def test_a_database_error_reaches_the_caller_and_the_org_is_not_remembered() -> None:
    class Broken(FakeRun):
        async def run(self, fn, **opts) -> Never:
            raise ConnectionResetError("gone")

    policy = OwnLeafPolicy(Broken([None]), wait=1, poll=0.01)
    with pytest.raises(ConnectionResetError):
        await policy.before_first_write(ORG)
    assert ORG not in policy._placed


@pytest.mark.asyncio
async def test_a_placed_org_is_still_refused_to_a_call_running_for_another() -> None:
    policy = OwnLeafPolicy(FakeRun([{"placed": True, "note": None}]), wait=1, poll=0.01)
    await policy.before_first_write(ORG)

    async def other_org() -> None:
        with org_context.bind(org_context.OrgContext("b" * 24, None, "service", "t", "-")):
            await policy.before_first_write(ORG)

    with pytest.raises(OrgMismatch):                                          # a context of its own: the fixture's org is not ambient there
        await asyncio.get_running_loop().create_task(other_org(), context=contextvars.Context())


@pytest.mark.asyncio
async def test_a_policy_shared_by_two_event_loops_gives_each_loop_a_task_of_its_own() -> None:
    import threading

    policy = OwnLeafPolicy(FakeRun([{"placed": False, "note": None}, {"placed": True, "note": None}]), wait=5, poll=0.01)
    errors: list[BaseException] = []
    started, release = threading.Event(), threading.Event()

    async def other_loop() -> None:
        with org_context.bind(org_context.OrgContext(ORG, None, "service", "t", "-")):
            started.set()
            await policy.before_first_write(ORG)

    def run_other() -> None:
        try:
            asyncio.run(other_loop())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            release.set()

    first = asyncio.ensure_future(policy.before_first_write(ORG))                  # this loop's task is pending when the other loop asks
    await asyncio.sleep(0)
    t = threading.Thread(target=run_other)
    t.start()
    await asyncio.get_running_loop().run_in_executor(None, release.wait, 10)
    t.join()
    await first
    assert errors == [] and {k[0] for k in policy._asking} <= {asyncio.get_running_loop()}


@pytest.mark.asyncio
async def test_the_wait_counts_the_request_and_cuts_a_slow_call_at_the_deadline() -> None:
    class Slow(FakeRun):
        async def run(self, fn, **opts) -> object:
            await asyncio.sleep(5)                                                # a request that outlasts the whole wait
            return await super().run(fn, **opts)

    policy = OwnLeafPolicy(Slow([{"placed": True, "note": None}]), wait=0.1, poll=0.01)
    t0 = asyncio.get_running_loop().time()
    with pytest.raises(PgVectorError, match="placement did not finish"):
        await policy.before_first_write(ORG)
    assert asyncio.get_running_loop().time() - t0 < 1
