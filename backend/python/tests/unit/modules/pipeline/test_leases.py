"""RecordLeases: a stage's write to the stored record under the lease its re-index holds."""

from unittest.mock import AsyncMock

import pytest

from app.modules.pipeline import leases as leases_module
from app.modules.pipeline.leases import RecordBusy, RecordLeases, RevisionSuperseded


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
