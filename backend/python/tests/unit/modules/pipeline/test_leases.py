"""RecordLeases: a stage's write to the stored record under the lease its re-index holds."""

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from app.modules.pipeline import leases as leases_module
from app.modules.pipeline.leases import (
    LeaseLost,
    RecordBusy,
    RecordLeases,
    RevisionSuperseded,
)


def _current(*answers: bool) -> AsyncMock:
    return AsyncMock(side_effect=list(answers))


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(leases_module, "_POLL_S", 0.0)


@pytest.mark.asyncio
async def test_unbound_it_only_checks_the_revision() -> None:
    leases = RecordLeases()
    async with leases.hold("r1", owner="stage:j", wait_s=1, still_current=_current(True)):
        pass
    with pytest.raises(RevisionSuperseded):
        async with leases.hold("r1", owner="stage:j", wait_s=1, still_current=_current(False)):
            pass



class TestUnboundHoldsOfOneRecordDoNotOverlap:
    """Without distributed concurrency there is no lease to take, but classify and entities still
    read-modify-write the same stored record on this process's loop: one would drop the other's section."""

    @pytest.mark.asyncio
    async def test_a_second_holder_waits_for_the_first(self) -> None:
        leases = RecordLeases()
        events: list[str] = []
        first_in = asyncio.Event()
        release_first = asyncio.Event()

        async def first() -> None:
            async with leases.hold("r1", owner="classify", wait_s=5, still_current=AsyncMock(return_value=True)):
                events.append("first in")
                first_in.set()
                await release_first.wait()
                events.append("first out")

        async def second() -> None:
            await first_in.wait()
            async with leases.hold("r1", owner="entities", wait_s=5, still_current=AsyncMock(return_value=True)):
                events.append("second in")

        tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
        await first_in.wait()
        await asyncio.sleep(0.05)
        assert events == ["first in"]
        release_first.set()
        await asyncio.gather(*tasks)
        assert events == ["first in", "first out", "second in"]

    @pytest.mark.asyncio
    async def test_the_revision_is_checked_again_once_the_wait_is_over(self) -> None:
        leases = RecordLeases()
        held = asyncio.Event()
        release = asyncio.Event()

        async def holder() -> None:
            async with leases.hold("r1", owner="reindex", wait_s=5, still_current=AsyncMock(return_value=True)):
                held.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await held.wait()
        current = {"rev": True}

        async def still_current() -> bool:
            return current["rev"]

        waiting = asyncio.create_task(self._enter(leases, still_current))
        await asyncio.sleep(0.05)
        # A re-index moves the record on while this one waits for the lock.
        current["rev"] = False
        release.set()
        await task
        with pytest.raises(RevisionSuperseded):
            await waiting

    @pytest.mark.asyncio
    async def test_a_holder_that_waits_too_long_is_busy(self) -> None:
        leases = RecordLeases()
        held = asyncio.Event()
        release = asyncio.Event()

        async def holder() -> None:
            async with leases.hold("r1", owner="classify", wait_s=5, still_current=AsyncMock(return_value=True)):
                held.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await held.wait()
        with pytest.raises(RecordBusy):
            async with leases.hold("r1", owner="entities", wait_s=0.05, still_current=AsyncMock(return_value=True)):
                pass
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_other_records_do_not_wait(self) -> None:
        leases = RecordLeases()
        release = asyncio.Event()
        held = asyncio.Event()

        async def holder() -> None:
            async with leases.hold("r1", owner="classify", wait_s=5, still_current=AsyncMock(return_value=True)):
                held.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await held.wait()
        async with leases.hold("r2", owner="entities", wait_s=0.05, still_current=AsyncMock(return_value=True)):
            pass
        release.set()
        await task
        assert leases._local == {}

    @staticmethod
    async def _enter(leases: RecordLeases, still_current: Callable[[], Awaitable[bool]]) -> None:
        async with leases.hold("r1", owner="entities", wait_s=5, still_current=still_current):
            pass

@pytest.mark.asyncio
async def test_it_takes_the_records_own_lease_and_always_releases_it() -> None:
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(side_effect=[False, True])
    leases = RecordLeases()
    leases.bind(manager)

    async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True, True)):
        pass

    pool, holder, limit, _lease = manager.try_acquire.await_args.args
    assert pool == "record:r1" and limit == 1 and holder.startswith("stage:j:")
    manager.release.assert_awaited_once_with("record:r1", holder)


@pytest.mark.asyncio
async def test_a_reindex_starting_while_it_waits_supersedes_the_write() -> None:
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(return_value=False)
    leases = RecordLeases()
    leases.bind(manager)
    with pytest.raises(RevisionSuperseded):
        async with leases.hold("r1", owner="stage:j", wait_s=60, still_current=_current(False)):
            pytest.fail("must not write over a newer revision")
    manager.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_record_that_stays_busy_is_retried_later() -> None:
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(return_value=False)
    leases = RecordLeases()
    leases.bind(manager)
    with pytest.raises(RecordBusy):
        async with leases.hold("r1", owner="stage:j", wait_s=0, still_current=AsyncMock(return_value=True)):
            pytest.fail("must not write without the lease")


@pytest.mark.asyncio
async def test_a_revision_that_moved_before_the_lease_was_granted_is_not_written() -> None:
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(return_value=True)
    leases = RecordLeases()
    leases.bind(manager)
    with pytest.raises(RevisionSuperseded):
        async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(False)):
            pytest.fail("must not write over a newer revision")
    manager.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_held_lease_is_renewed_until_it_is_released(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(leases_module, "_MIN_RENEW_S", 0.0)
    monkeypatch.setattr(type(messaging_env), "concurrency_lease_seconds", property(lambda _self: 0.03))
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(return_value=True)
    manager.renew = AsyncMock(return_value=True)
    leases = RecordLeases()
    leases.bind(manager)

    async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
        await asyncio.sleep(0.1)
    renewals = manager.renew.await_count
    assert renewals >= 2
    assert manager.renew.await_args.args[0] == "record:r1"
    await asyncio.sleep(0.05)
    assert manager.renew.await_count == renewals  # stopped with the hold
    manager.release.assert_awaited_once()


def _short_lease(monkeypatch: pytest.MonkeyPatch, lease_s: float = 0.03) -> None:
    from app.services.messaging.config import messaging_env

    monkeypatch.setattr(leases_module, "_MIN_RENEW_S", 0.0)
    monkeypatch.setattr(type(messaging_env), "concurrency_lease_seconds", property(lambda _self: lease_s))


def _held(renew: AsyncMock) -> tuple[AsyncMock, RecordLeases]:
    manager = AsyncMock()
    manager.try_acquire = AsyncMock(return_value=True)
    manager.renew = renew
    leases = RecordLeases()
    leases.bind(manager)
    return manager, leases


@pytest.mark.asyncio
async def test_a_lost_lease_stops_the_holder_before_its_next_write(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    _short_lease(monkeypatch)
    manager, leases = _held(AsyncMock(return_value=False))
    writes: list[int] = []
    with pytest.raises(LeaseLost):
        async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
            for i in range(50):
                await asyncio.sleep(0.01)
                writes.append(i)
    assert len(writes) < 5  # stopped at the first renewal, not after the body ran
    assert isinstance(LeaseLost("x"), RecordBusy)  # the stages retry it as a busy record
    current = asyncio.current_task()
    assert current is not None and current.cancelling() == 0  # our cancellation was taken back
    manager.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_renewals_failing_past_the_deadline_lose_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    _short_lease(monkeypatch)
    _, leases = _held(AsyncMock(side_effect=ConnectionError("redis down")))
    with pytest.raises(LeaseLost, match="kept failing"):
        async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
            await asyncio.sleep(1)
            pytest.fail("must not keep writing without the lease")


@pytest.mark.asyncio
async def test_a_renewal_blip_inside_the_deadline_keeps_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    _short_lease(monkeypatch, 0.09)
    renew = AsyncMock(side_effect=[ConnectionError("blip"), True, True, True, True, True, True, True, True, True])
    _, leases = _held(renew)
    async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
        await asyncio.sleep(0.15)
    assert renew.await_count >= 3


@pytest.mark.asyncio
async def test_an_outside_cancellation_is_not_turned_into_a_lost_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    _short_lease(monkeypatch)
    manager, leases = _held(AsyncMock(return_value=False))

    async def body() -> None:
        async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
            await asyncio.sleep(1)

    task = asyncio.create_task(body())
    await asyncio.sleep(0)
    task.cancel()  # shutdown, before any renewal
    with pytest.raises(asyncio.CancelledError):
        await task
    manager.release.assert_awaited_once()

    # Shutdown arriving in the same step as the loss is still a cancellation.
    tasks: list[asyncio.Task[None]] = []

    def shutdown_then_lost(*_args: object) -> bool:
        _ = tasks[0].cancel()
        return False

    manager, leases = _held(AsyncMock(side_effect=shutdown_then_lost))
    tasks.append(asyncio.create_task(body()))
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    manager.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_body_that_swallows_the_cancellation_still_ends_in_a_lost_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import contextlib

    _short_lease(monkeypatch)
    manager, leases = _held(AsyncMock(return_value=False))
    finished = False
    with pytest.raises(LeaseLost):
        async with leases.hold("r1", owner="stage:j", wait_s=5, still_current=_current(True)):
            # Some enrichment code suppresses CancelledError around an await and carries on.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(1)
            finished = True
    assert finished  # it ran on, so its writes may have landed after the lease lapsed: retried
    current = asyncio.current_task()
    assert current is not None and current.cancelling() == 0
    manager.release.assert_awaited_once()
