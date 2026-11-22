"""A licence key entered: every limited connector gets the full sync that lifts its limit, without anyone pressing sync.

That the full sync, once it runs with a licence, lifts the limit is covered in
tests/unit/connectors/core/test_permission_sync_gate.py (``test_the_limit_stays_after_a_licensed_incremental_sync_and_
lifts_after_the_licensed_full_sync``); the queued sync runs full because the start path merges ``pendingFullSync``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync import licence_lift
from app.connectors.core.sync.licence_lift import (
    queue_limited_full_syncs,
    watch_licence,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOG = logging.getLogger("test.licence_lift")
FULL = ConnectorStateKeys.PENDING_FULL_SYNC
RESYNC = ConnectorStateKeys.PENDING_RESYNC


def _app(key: str, *, limit: str | None = "CREATOR", type_: str = "JIRA", status: str = "IDLE",
         active: bool = True) -> dict:
    return {"_key": key, "type": type_, "scope": "team", "accessLimit": limit, "status": status,
            ConnectorStateKeys.IS_ACTIVE: active, ConnectorStateKeys.IS_AUTHENTICATED: True}


def _graph(apps: list[dict]) -> AsyncMock:
    gp = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    gp.get_org_apps = AsyncMock(return_value=apps)
    gp.update_node = AsyncMock(return_value=True)
    return gp


def _writes(gp: AsyncMock) -> dict[str, dict]:
    return {c.args[0]: c.args[2] for c in gp.update_node.await_args_list}


@pytest.fixture
def drain() -> Iterator[AsyncMock]:
    with patch.object(licence_lift, "drain_queued_syncs", new=AsyncMock(return_value=[])) as mock:
        yield mock


@pytest.mark.asyncio
async def test_each_limited_active_connector_is_queued_for_one_full_sync(drain: AsyncMock) -> None:
    gp = _graph([
        _app("idle"),
        _app("off", active=False),
        _app("running", status="FULL_SYNCING"),
        _app("queued", status="QUEUED"),
        _app("going", status="DELETING"),
        _app("open", limit=None),
        _app("bucket", type_="S3"),
    ])

    assert await queue_limited_full_syncs(gp, _LOG) is True

    writes = _writes(gp)
    assert set(writes) == {"idle", "off", "running", "queued"}
    assert writes["idle"][FULL] and writes["idle"][RESYNC] and writes["idle"]["status"] == "QUEUED"
    assert writes["off"] == {FULL: True}, "not started; enabling it later runs a full sync"
    assert writes["running"] == {FULL: True, RESYNC: True}, "its finalizer re-issues it as a full sync"
    assert writes["queued"] == {FULL: True}, "already queued: not queued twice"
    assert all(c.args[1] == CollectionNames.APPS.value for c in gp.update_node.await_args_list)
    drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_no_limited_connector_nothing_is_written_or_drained(drain: AsyncMock) -> None:
    gp = _graph([_app("open", limit=None), _app("bucket", type_="S3")])
    assert await queue_limited_full_syncs(gp, _LOG) is True
    gp.update_node.assert_not_awaited()
    drain.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_queue_write_asks_for_another_pass(drain: AsyncMock) -> None:
    gp = _graph([_app("a"), _app("b")])
    gp.update_node = AsyncMock(side_effect=[ConnectionError("down"), True])
    assert await queue_limited_full_syncs(gp, _LOG) is False
    assert gp.update_node.await_count == 2, "one failure does not stop the others"


async def _run_watch(licensed: list[bool], queue: AsyncMock) -> None:
    """Drive watch_licence through one check per answer in ``licensed``."""
    sleeps = 0

    async def sleep(_s: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= len(licensed):
            raise asyncio.CancelledError

    with patch.object(licence_lift, "permission_sync_licensed", new=AsyncMock(side_effect=licensed)), \
            patch.object(licence_lift, "queue_limited_full_syncs", new=queue), \
            patch.object(licence_lift.asyncio, "sleep", new=sleep), pytest.raises(asyncio.CancelledError):
        await watch_licence(MagicMock(), MagicMock(), _LOG)


@pytest.mark.asyncio
async def test_unlicensed_then_licensed_queues_once_and_a_repeat_detection_does_not_queue_again() -> None:
    queue = AsyncMock(return_value=True)
    await _run_watch([False, False, True, True, True], queue)
    queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_first_check_after_a_start_queues_a_licence_entered_while_down() -> None:
    queue = AsyncMock(return_value=True)
    await _run_watch([True, True], queue)
    queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_pass_is_run_again_on_the_next_check() -> None:
    queue = AsyncMock(side_effect=[False, RuntimeError("org list down"), True])
    await _run_watch([True, True, True, True], queue)
    assert queue.await_count == 3


@pytest.mark.asyncio
async def test_a_licence_that_lapses_and_returns_queues_again() -> None:
    queue = AsyncMock(return_value=True)
    await _run_watch([True, False, True], queue)
    assert queue.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_licence_read_is_retried_not_taken_for_licensed() -> None:
    queue = AsyncMock(return_value=True)
    with patch.object(licence_lift, "permission_sync_licensed",
                      new=AsyncMock(side_effect=[ConnectionError("store down"), True])), \
            patch.object(licence_lift, "queue_limited_full_syncs", new=queue):
        sleeps = 0

        async def sleep(_s: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                raise asyncio.CancelledError

        with patch.object(licence_lift.asyncio, "sleep", new=sleep), pytest.raises(asyncio.CancelledError):
            await watch_licence(MagicMock(), MagicMock(), _LOG)
    queue.assert_awaited_once()
