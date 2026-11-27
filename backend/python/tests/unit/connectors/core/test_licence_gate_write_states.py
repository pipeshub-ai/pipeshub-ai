"""States of the licence gate's write side that need no database: the type rule fails closed, the cached licence
answer ages out in both directions, and two connectors sharing one processor keep their own run state.

What users can read in each state is checked on real databases in
tests/integration/graph_db/test_licence_gate_write_real_backends.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import (
    AccessLimit,
    AppStatus,
    Connectors,
    PermissionModel,
)
from app.connectors.core.base.data_processor import permission_sync_gate
from app.connectors.core.base.data_processor.permission_sync_gate import (
    LICENCE_MAX_AGE_SECONDS,
    OPEN_CONNECTOR_TYPES,
    AccessLimitOwed,
    gated,
    permission_sync_licensed,
)
from app.connectors.core.base.data_store.graph_data_store import _OPEN_TRANSACTION
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task
from app.models.entities import AppUser
from tests.unit.connectors.core.test_permission_sync_gate import (
    LICENSED,
    UNLICENSED,
    _app,
    _config,
    _connector,
    _gate_processor,
    _real_write,
    _runner_graph,
    _wire,
)


@pytest.mark.parametrize("type_", sorted({c.value for c in Connectors}))
def test_every_registered_type_is_gated_unless_it_is_listed_open(type_: str) -> None:
    """Fail closed: only a type named in OPEN_CONNECTOR_TYPES stays open, so a type added later is gated."""
    assert gated({"type": type_, "scope": "team"}) is (type_.upper() not in OPEN_CONNECTOR_TYPES)
    assert gated({"type": type_, "scope": "personal"}) is False


@pytest.mark.parametrize("type_", ["", None, "brand_new_source", "Jira"])
def test_an_unknown_blank_or_oddly_cased_type_is_gated(type_: str | None) -> None:
    assert gated({"type": type_, "scope": "TEAM"}) is True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
@pytest.mark.parametrize(("before", "after"), [(UNLICENSED, LICENSED), (LICENSED, UNLICENSED), (None, UNLICENSED)])
async def test_a_cached_licence_answer_is_kept_for_its_age_and_never_longer(
    before: object, after: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key entered (or a lapse) is seen within LICENCE_MAX_AGE_SECONDS, not sooner and not later."""
    clock = _Clock()
    monkeypatch.setattr(permission_sync_gate.time, "monotonic", clock)
    config = _config(before)
    first = await permission_sync_licensed(config)
    config.get_config = AsyncMock(return_value=after)

    clock.now += LICENCE_MAX_AGE_SECONDS
    assert await permission_sync_licensed(config) is first, "still the remembered answer"
    clock.now += 1
    assert await permission_sync_licensed(config) is (after == LICENSED or after is None)


@pytest.mark.asyncio
async def test_a_failed_read_after_the_answer_aged_out_raises_and_is_not_taken_for_licensed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(permission_sync_gate.time, "monotonic", clock)
    config = _config(UNLICENSED)
    assert await permission_sync_licensed(config) is False
    config.get_config = AsyncMock(side_effect=ConnectionError("down"))
    clock.now += LICENCE_MAX_AGE_SECONDS + 1
    with pytest.raises(ConnectionError):
        await permission_sync_licensed(config)
    config.get_config = AsyncMock(return_value=UNLICENSED)
    assert await permission_sync_licensed(config) is False


@pytest.mark.asyncio
async def test_two_connectors_on_one_processor_keep_their_own_skips_and_lifts() -> None:
    """An org-shared processor: the start of B's run does not forget what A's run skipped, so A is not lifted."""
    proc, _ = _gate_processor(UNLICENSED, _app(limit=AccessLimit.CREATOR.value))

    def user(connector_id: str) -> AppUser:
        return AppUser(app_name=Connectors.JIRA, connector_id=connector_id, source_user_id="s", email="a@x.com",
                       full_name="A")

    await proc.on_new_app_users([user("A")])                   # skipped, noted for A
    proc.config_service = _config(LICENSED)
    assert await proc.apply_licence_gate("B", full_sync=True) is True
    assert await proc.lift_access_limit("A", PermissionModel.RECORD_LEVEL) is False, "A skipped a write this run"
    assert await proc.lift_access_limit("B", PermissionModel.RECORD_LEVEL) is True
    proc.clear_access_limit.assert_awaited_once_with("B", PermissionModel.RECORD_LEVEL)


@pytest.mark.asyncio
@pytest.mark.parametrize("type_", [Connectors.KNOWLEDGE_BASE.value, Connectors.S3.value, Connectors.WEB.value])
async def test_an_open_type_is_never_limited_or_skipped(type_: str) -> None:
    proc, tx_store = _gate_processor(UNLICENSED, _app(type_=type_))
    assert await proc.apply_licence_gate("conn-1", full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()
    assert await proc.skips_permission_writes("conn-1") is False


@pytest.mark.asyncio
async def test_a_missing_app_document_is_not_gated_and_is_not_limited() -> None:
    """A connector whose app is gone (deleted mid-run): nothing to limit, and its writes are not skipped."""
    proc, _ = _gate_processor(UNLICENSED, {})
    assert await proc.apply_licence_gate("conn-1", full_sync=True) is False
    proc.set_access_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_end_of_a_run_limits_a_connector_whose_limit_at_the_first_skip_failed() -> None:
    """The backstop, driven through the real failure: the limit at the first skipped write raised (so did that write),
    and the end of the run sets it."""
    proc, _ = _gate_processor(UNLICENSED, _app())
    proc.limit_unlicensed = AsyncMock(side_effect=[ConnectionError("write refused"), None])
    await proc.limit_after_skipped_writes("conn-1")
    proc.limit_unlicensed.assert_not_awaited()          # nothing skipped yet

    with pytest.raises(ConnectionError):
        await proc.skips_permission_writes("conn-1")
    await proc.limit_after_skipped_writes("conn-1")
    assert proc.limit_unlicensed.await_count == 2, "the end of the run set the limit the first skip owed"
    await proc.limit_after_skipped_writes("conn-1")
    assert proc.limit_unlicensed.await_count == 2, "and only once"


@pytest.mark.asyncio
async def test_the_end_of_a_run_does_not_limit_again_what_its_first_skip_limited() -> None:
    proc, _ = _gate_processor(UNLICENSED, _app())
    proc.limit_unlicensed = AsyncMock()
    assert await proc.skips_permission_writes("conn-1") is True
    await proc.limit_after_skipped_writes("conn-1")
    proc.limit_unlicensed.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("app", [_app(limit=AccessLimit.CREATOR.value), _app(type_=Connectors.S3.value)])
async def test_the_end_of_a_run_leaves_a_limited_or_open_connector_alone(app: dict) -> None:
    proc, _ = _gate_processor(UNLICENSED, app)
    proc.limit_unlicensed = AsyncMock()
    proc._permissions_skipped = {"conn-1"}
    await proc.limit_after_skipped_writes("conn-1")
    proc.limit_unlicensed.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_watcher_learns_of_a_limit_whose_refresh_raised_after_the_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The limit committed, then marking the stamps raised: the watcher is told anyway, and again by the retry that
    finds the limit stored and runs the refreshes alone."""
    proc, _ = _gate_processor(UNLICENSED, _app())
    _real_write(proc, monkeypatch)
    graph = proc.data_store_provider.graph_provider
    graph.kh_scope_mark_changed = AsyncMock(side_effect=ConnectionError("stamps down"))
    permission_sync_gate.take_limited()

    with pytest.raises(ConnectionError):
        await proc.limit_unlicensed("conn-1", _app())
    assert "conn-1" in permission_sync_gate.take_limited()

    graph.kh_scope_mark_changed = AsyncMock()
    await proc.limit_unlicensed("conn-1", _app(limit=AccessLimit.CREATOR.value))
    assert "conn-1" in permission_sync_gate.take_limited()
    assert proc.data_store_provider.execute_in_transaction.await_count == 1, "the stored limit is not written again"


@pytest.mark.asyncio
async def test_the_end_of_a_run_tells_the_watcher_of_a_limit_its_first_skip_stored_before_raising() -> None:
    """The first skip's write committed and then raised, so the limit is stored: the end of the run writes nothing,
    but the watcher still learns the connector is limited."""
    proc, _ = _gate_processor(UNLICENSED, _app(limit=AccessLimit.CREATOR.value))
    proc.limit_unlicensed = AsyncMock()
    proc._permissions_skipped = {"conn-1"}
    permission_sync_gate.take_limited()

    await proc.limit_after_skipped_writes("conn-1")

    proc.limit_unlicensed.assert_not_awaited()
    assert "conn-1" in permission_sync_gate.take_limited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["true", "allowed", ["x"], [], 1, 0, True, {}, {"allowed": "true"}, {"allowed": 1}])
async def test_a_licence_value_that_is_present_but_not_allowed_true_is_unlicensed(value: object) -> None:
    """Only a missing key keeps the transition shortcut (licensed); anything else stored must say allowed: true."""
    assert await permission_sync_licensed(_config(value)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "licensed"), [(None, True), ({"allowed": True}, True), ({"allowed": False}, False)])
async def test_a_missing_key_or_allowed_true_is_licensed(value: object, licensed: bool) -> None:
    assert await permission_sync_licensed(_config(value)) is licensed


def _unlimited_run(proc) -> None:
    """A run that began licensed on a connector with no limit (``apply_licence_gate`` limited nothing)."""
    proc.limit_unlicensed = AsyncMock()


@pytest.mark.asyncio
async def test_the_first_write_a_run_skips_on_an_unlimited_connector_limits_it_before_the_skip_returns() -> None:
    proc, _ = _gate_processor(LICENSED, _app())
    _unlimited_run(proc)
    assert await proc.apply_licence_gate("conn-1", full_sync=False) is False
    proc.config_service = _config(UNLICENSED)                      # the licence lapsed partway

    assert await proc.skips_permission_writes("conn-1") is True
    proc.limit_unlicensed.assert_awaited_once()
    assert await proc.skips_permission_writes("conn-1") is True
    proc.limit_unlicensed.assert_awaited_once()                    # once per run, not per write


@pytest.mark.asyncio
async def test_a_limit_that_cannot_be_set_at_the_first_skip_fails_the_write() -> None:
    proc, tx_store = _gate_processor(UNLICENSED, _app())
    proc.limit_unlicensed = AsyncMock(side_effect=ConnectionError("write refused"))
    with pytest.raises(ConnectionError):
        await proc.skips_permission_writes("conn-1")
    user = AppUser(app_name=Connectors.JIRA, connector_id="conn-1", source_user_id="s", email="a@x.com", full_name="A")
    with pytest.raises(ConnectionError):
        await proc.on_new_app_users([user])
    tx_store.batch_upsert_app_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_connector_limited_at_the_start_of_the_run_is_not_limited_again_at_its_skips() -> None:
    proc, _ = _gate_processor(UNLICENSED, _app())
    _unlimited_run(proc)
    await proc.apply_licence_gate("conn-1", full_sync=True)
    proc.limit_unlicensed.assert_awaited_once()                    # at the start
    assert await proc.skips_permission_writes("conn-1") is True
    proc.limit_unlicensed.assert_awaited_once()


@pytest.mark.asyncio
async def test_after_a_lift_the_next_skip_limits_again() -> None:
    proc, _ = _gate_processor(LICENSED, _app(limit=AccessLimit.CREATOR.value))
    _unlimited_run(proc)
    assert await proc.apply_licence_gate("conn-1", full_sync=True) is True
    assert await proc.lift_access_limit("conn-1", PermissionModel.RECORD_LEVEL) is True
    proc.data_store_provider.graph_provider.get_document = AsyncMock(return_value=_app())   # stored: lifted
    proc.config_service = _config(UNLICENSED)
    assert await proc.skips_permission_writes("conn-1") is True
    proc.limit_unlicensed.assert_awaited_once()


@pytest.mark.asyncio
async def test_inside_a_store_transaction_a_skip_owing_the_limit_raises_instead_of_opening_another() -> None:
    proc, _ = _gate_processor(UNLICENSED, _app())
    proc.limit_unlicensed = AsyncMock()
    token = _OPEN_TRANSACTION.set(True)
    try:
        with pytest.raises(AccessLimitOwed):
            await proc.skips_permission_writes("conn-1")
    finally:
        _OPEN_TRANSACTION.reset(token)
    proc.limit_unlicensed.assert_not_awaited()
    await proc.limit_after_skipped_writes("conn-1")
    proc.limit_unlicensed.assert_awaited_once()          # still owed: the end of the run sets it


@pytest.mark.asyncio
async def test_limited_before_the_transaction_a_skip_inside_it_just_skips() -> None:
    proc, _ = _gate_processor(UNLICENSED, _app())
    proc.limit_unlicensed = AsyncMock()
    await proc.limit_if_licence_lapsed(["conn-1", None, "conn-1"])
    proc.limit_unlicensed.assert_awaited_once()
    token = _OPEN_TRANSACTION.set(True)
    try:
        assert await proc.skips_permission_writes("conn-1") is True
    finally:
        _OPEN_TRANSACTION.reset(token)
    proc.limit_unlicensed.assert_awaited_once()


@pytest.mark.asyncio
async def test_licensed_the_check_before_a_transaction_writes_nothing() -> None:
    proc, _ = _gate_processor(LICENSED, _app())
    proc.limit_unlicensed = AsyncMock()
    await proc.limit_if_licence_lapsed(["conn-1"])
    proc.limit_unlicensed.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cancel_while_the_end_of_run_limit_is_written_still_writes_idle_releases_and_limits() -> None:
    """The limit owed at the end of a run is written in the detached finalizer: a cancel of the run (a shutdown, a
    delete) cannot skip it, nor the IDLE write and the release after it."""
    proc, _ = _gate_processor(UNLICENSED, _app())
    gp = _runner_graph(_app())
    gp.batch_upsert_nodes = AsyncMock(return_value=True)
    _wire(proc, gp)
    entered, go, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
    limited: list[str] = []

    async def slow_backstop(connector_id: str) -> None:
        entered.set()
        await go.wait()
        limited.append(connector_id)

    proc.limit_after_skipped_writes = slow_backstop
    coordinator = AsyncMock()

    async def end(_lease: SyncLease) -> bool:
        released.set()
        return True

    coordinator.end = AsyncMock(side_effect=end)
    task = asyncio.create_task(run_sync_task(
        _connector(proc), "conn-1", gp, logging.getLogger("t"),
        lease=SyncLease("conn-1", "tok", 1), coordinator=coordinator,
    ))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    go.set()
    await asyncio.wait_for(released.wait(), 5)
    assert limited == ["conn-1"]
    assert any(c.args[2].get("status") == AppStatus.IDLE.value for c in gp.update_node.await_args_list)


async def _watch_until(predicate, timeout: float = 5) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        assert loop.time() < end, "the watcher did not get there"
        await asyncio.sleep(0.01)


def _watched_graph(apps: list[dict]) -> AsyncMock:
    gp = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    gp.get_org_apps = AsyncMock(side_effect=lambda *_a, **_k: list(apps))
    gp.update_node = AsyncMock(return_value=True)
    return gp


def _queued(gp: AsyncMock) -> int:
    return sum(1 for c in gp.update_node.await_args_list if c.args[2].get("status") == AppStatus.QUEUED.value)


@pytest.mark.asyncio
async def test_a_connector_limited_again_after_the_watcher_settled_is_queued_for_the_full_sync_that_lifts_it() -> None:
    """Licensed and settled (nothing limited, so the watcher stopped scanning), a sync's end of run limits a
    connector again: its first-skip limit had failed, and its lease went to a licensed run meanwhile. The watcher
    must look again and queue the full sync that lifts it."""
    from app.connectors.core.sync import licence_lift

    apps: list[dict] = []
    gp = _watched_graph(apps)
    # Counts the watcher's lift passes, not graph reads: other work in the same loop may read the connectors too.
    lift = AsyncMock(wraps=licence_lift.queue_limited_full_syncs)
    with patch.object(licence_lift, "drain_queued_syncs", AsyncMock(return_value=[])), \
            patch.object(licence_lift, "queue_limited_full_syncs", lift):
        task = asyncio.create_task(licence_lift.watch_licence(_config(LICENSED), gp, logging.getLogger("t"), 0.01))
        try:
            await _watch_until(lambda: lift.await_count >= 1)
            await asyncio.sleep(0.05)
            scans = lift.await_count
            assert scans == 1, "settled: nothing limited, no more scans"

            proc, _ = _gate_processor(UNLICENSED, _app())
            await proc.limit_unlicensed("conn-1", _app())          # what the finalizer's backstop does
            apps.append({**_app(limit=AccessLimit.CREATOR.value), "status": AppStatus.IDLE.value,
                         "isActive": True, "isAuthenticated": True, "scope": "team"})
            await _watch_until(lambda: _queued(gp) == 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_a_connector_limited_again_gets_its_retries_back() -> None:
    """Its earlier full syncs used up the watcher's three tries; limited again, it is queued again."""
    from app.connectors.core.sync import licence_lift

    apps = [{**_app(limit=AccessLimit.CREATOR.value), "status": AppStatus.IDLE.value, "isActive": True,
             "isAuthenticated": True}]
    gp = _watched_graph(apps)           # the queue write is not applied, so it stays IDLE and limited
    with patch.object(licence_lift, "drain_queued_syncs", AsyncMock(return_value=[])):
        task = asyncio.create_task(licence_lift.watch_licence(_config(LICENSED), gp, logging.getLogger("t"), 0.01))
        try:
            await _watch_until(lambda: _queued(gp) == licence_lift.MAX_QUEUED_FULL_SYNCS)
            await asyncio.sleep(0.05)
            assert _queued(gp) == licence_lift.MAX_QUEUED_FULL_SYNCS, "the cap holds"
            proc, _ = _gate_processor(UNLICENSED, _app())
            await proc.limit_unlicensed("conn-1", _app())
            await _watch_until(lambda: _queued(gp) == licence_lift.MAX_QUEUED_FULL_SYNCS + 1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
