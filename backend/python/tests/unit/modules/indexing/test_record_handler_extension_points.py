"""The record handler consults the installed lifecycle for first-use stage runs, and the installed ownership when it releases queued copies."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import (
    DefaultIndexingLifecycle,
    DefaultRecordOwnership,
    IndexingExtension,
    RecordOwnerUnknown,
)
from tests.unit.modules.indexing.test_connector_off_events import (
    REINDEX,
    FakeConnectorGraph,
    _handler,
    _payload,
)

pytestmark = pytest.mark.asyncio


def _graph() -> FakeConnectorGraph:
    graph = FakeConnectorGraph()
    graph.add_connector("conn-1", active=True)
    graph.add_record(
        "r1", "conn-1", indexingStatus=ProgressStatus.COMPLETED.value, extractionStatus=ProgressStatus.NOT_STARTED.value,
        contentRev="rev-1",
    )
    return graph


async def _run(handler: object, **extra: object) -> list:
    payload = _payload(connector_id="conn-1", stages=["classify"], stageRev="rev-1", **extra)
    return [e.event async for e in handler.process_event(REINDEX, payload)]  # type: ignore[attr-defined]


class Life(DefaultIndexingLifecycle):
    def __init__(self, skip: bool) -> None:
        self.skip, self.asked = skip, []

    async def skip_first_use(self, record: object) -> bool:
        self.asked.append(record["_key"])  # type: ignore[index]
        return self.skip


async def test_a_first_use_run_the_lifecycle_skips_redrives_nothing() -> None:
    life = Life(skip=True)
    extension.install(IndexingExtension(lifecycle=life))
    handler, processor = _handler(_graph())
    processor.stage_ingress = AsyncMock()

    await _run(handler, firstUse=True)

    assert life.asked == ["r1"]
    processor.stage_ingress.redrive.assert_not_awaited()


async def test_a_first_use_run_the_lifecycle_does_not_skip_is_redriven_without_force() -> None:
    life = Life(skip=False)
    extension.install(IndexingExtension(lifecycle=life))
    handler, processor = _handler(_graph())
    processor.stage_ingress = AsyncMock()
    processor.stage_ingress.redrive.return_value = "dispatched"

    await _run(handler, firstUse=True)

    processor.stage_ingress.redrive.assert_awaited_once()
    assert processor.stage_ingress.redrive.await_args.kwargs["force"] is False


async def test_an_explicit_stage_rerun_never_asks_the_lifecycle() -> None:
    life = Life(skip=True)
    extension.install(IndexingExtension(lifecycle=life))
    handler, processor = _handler(_graph())
    processor.stage_ingress = AsyncMock()
    processor.stage_ingress.redrive.return_value = "dispatched"

    await _run(handler)

    assert life.asked == []
    assert processor.stage_ingress.redrive.await_args.kwargs["force"] is True


async def test_a_record_whose_owner_cannot_be_read_is_dropped_like_a_missing_one() -> None:
    class Own(DefaultRecordOwnership):
        async def prime(self, records: list, graph: object) -> None:
            raise RecordOwnerUnknown("not readable")

    extension.install(IndexingExtension(ownership=Own()))
    handler, processor = _handler(_graph())
    processor.stage_ingress = AsyncMock()

    events = await _run(handler, firstUse=True)

    assert [e.name for e in events if e.name != "START_DOWNLOAD"] == ["PARSING_COMPLETE", "INDEXING_COMPLETE"]
    processor.stage_ingress.redrive.assert_not_awaited()


async def test_queued_copies_are_released_only_after_the_owner_is_primed_and_not_when_it_cannot_be_read() -> None:
    primed: list[str] = []

    class Own(DefaultRecordOwnership):
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        async def prime(self, records: list, graph: object) -> None:
            primed.extend(r["_key"] for r in records)
            if self.fail:
                raise RecordOwnerUnknown("not readable")

    graph = _graph()
    handler, _processor = _handler(graph)
    graph.update_queued_duplicates_status = AsyncMock(return_value=0)

    extension.install(IndexingExtension(ownership=Own(fail=True)))
    await handler._release_queued_copies("r1")
    assert primed == ["r1"]
    graph.update_queued_duplicates_status.assert_not_awaited()

    extension.install(IndexingExtension(ownership=Own(fail=False)))
    await handler._release_queued_copies("r1")
    graph.update_queued_duplicates_status.assert_awaited_once()
