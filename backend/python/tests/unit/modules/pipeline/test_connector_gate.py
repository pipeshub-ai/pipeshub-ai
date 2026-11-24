"""Whether a stage job's connector is being deleted. Only "not deleting" is remembered: a delete
can be put back, and a job must see that at once."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.pipeline.connector_gate import ConnectorDeletingCheck


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _check(*apps: object) -> tuple[ConnectorDeletingCheck, MagicMock, _Clock]:
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=list(apps))
    clock = _Clock()
    check = ConnectorDeletingCheck(graph, logging.getLogger("t"), ttl_s=5.0, failed_read_ttl_s=1.0, clock=clock)
    return check, graph, clock


@pytest.mark.asyncio
async def test_a_connector_marked_deleting_is_being_deleted() -> None:
    check, _, _ = _check({"_key": "c1", "status": "DELETING", "isActive": True})
    assert await check("c1") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("app", [{"_key": "c1", "isActive": True}, {"_key": "c1", "isActive": False}, None])
async def test_a_live_off_or_missing_connector_is_not(app: object) -> None:
    """A missing app is the handler's removed connector, not this gate's: only an explicit DELETING holds work."""
    check, _, _ = _check(app)
    assert await check("c1") is False


@pytest.mark.asyncio
async def test_not_deleting_is_remembered_for_a_few_seconds() -> None:
    check, graph, clock = _check({"_key": "c1"}, {"_key": "c1", "status": "DELETING"})
    assert await check("c1") is False
    clock.now += 4.9
    assert await check("c1") is False
    assert graph.get_document.await_count == 1
    clock.now += 0.2
    assert await check("c1") is True
    assert graph.get_document.await_count == 2


@pytest.mark.asyncio
async def test_deleting_is_read_afresh_so_a_reverted_delete_is_seen_at_once() -> None:
    """A delete whose graph delete fails puts the connector's status back."""
    check, graph, _ = _check({"_key": "c1", "status": "DELETING"}, {"_key": "c1", "status": None})
    assert await check("c1") is True
    assert await check("c1") is False
    assert graph.get_document.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_read_is_not_a_delete_and_is_asked_again_after_a_second() -> None:
    check, graph, clock = _check(RuntimeError("timeout"), {"_key": "c1", "status": "DELETING"})
    assert await check("c1") is False
    clock.now += 0.9
    assert await check("c1") is False
    assert graph.get_document.await_count == 1
    clock.now += 0.2
    assert await check("c1") is True


@pytest.mark.asyncio
async def test_no_connector_id_is_not_read() -> None:
    check, graph, _ = _check()
    assert await check("") is False
    graph.get_document.assert_not_awaited()
