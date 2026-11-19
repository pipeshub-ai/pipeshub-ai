"""The recovery pass of the indexing service runs the installed lifecycle's own recovery, never lets its failure stop the pass, and primes
the owner of each stale record before the record's connector is read."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import (
    DefaultIndexingLifecycle,
    DefaultRecordOwnership,
    IndexingExtension,
)
from tests.unit.test_indexing_main import _make_container, _make_graph_provider

pytestmark = pytest.mark.asyncio


class Life(DefaultIndexingLifecycle):
    def __init__(self, *, fail: bool = False) -> None:
        self.passes, self.fail = 0, fail

    async def recovery_pass(self) -> int:
        self.passes += 1
        if self.fail:
            raise RuntimeError("recovery failed")
        return 3


def _stale(key: str = "r1") -> dict:
    return {
        "_key": key, "indexingStatus": ProgressStatus.IN_PROGRESS.value, "processingStartedAt": 1,
        "orgId": "org-1", "origin": "CONNECTOR", "connectorId": "own-conn",
    }


async def _recover(gp: object) -> None:
    from app.indexing_main import recover_in_progress_records

    await recover_in_progress_records(_make_container(), gp)  # type: ignore[arg-type]


async def test_the_lifecycles_recovery_runs_once_in_a_pass_that_has_stale_records() -> None:
    life = Life()
    extension.install(IndexingExtension(lifecycle=life))
    gp = _make_graph_provider()
    gp.get_nodes_by_filters = AsyncMock(return_value=[_stale()])

    await _recover(gp)

    assert life.passes == 1


async def test_a_failing_lifecycle_recovery_does_not_fail_the_pass() -> None:
    life = Life(fail=True)
    extension.install(IndexingExtension(lifecycle=life))
    gp = _make_graph_provider()
    gp.get_nodes_by_filters = AsyncMock(return_value=[_stale()])

    await _recover(gp)

    assert life.passes == 1


async def test_a_stale_record_is_primed_before_its_connector_is_read() -> None:
    primed: list[str] = []

    class Own(DefaultRecordOwnership):
        async def prime(self, records: list, graph: object) -> None:
            primed.extend(r["_key"] for r in records)

    extension.install(IndexingExtension(ownership=Own()))
    gp = _make_graph_provider()
    gp.get_nodes_by_filters = AsyncMock(return_value=[_stale()])
    gp.get_document = AsyncMock(return_value={"_key": "r1", **_stale()})

    await _recover(gp)

    assert primed and set(primed) == {"r1"}
