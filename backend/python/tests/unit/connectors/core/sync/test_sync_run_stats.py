"""The run's tally on sync_run.end: record counts and backoff wait.

Contract: docs/logging.md, "Connector sync runs". The runner tests write records
through a real DataSourceEntitiesProcessor (the shared fake store) and add a wait
from a child task, then read the one sync_run.end line through ``log_record_fields``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
)
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.run_context import (
    RecordOutcome,
    SkipReason,
    SyncRunStats,
    bind_run_stats,
    current_run_stats,
    note_backoff_wait,
    reset_run_stats,
)
from app.connectors.core.sync.sync_coordinator import LocalSyncCoordinator
from app.connectors.core.sync.sync_runner import join_cleanup_tasks, run_sync_task
from app.utils.logger import log_record_fields
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

RUN_LOGGER = "test.sync_run_stats"
P7A_END_FIELDS = {"run_id", "connector_id", "sync_type", "duration_ms", "outcome"}
COUNT_FIELDS = [
    "records_new", "records_updated", "records_unchanged", "records_deleted", "records_failed",
    "records_skipped", "skipped_owned_elsewhere", "skipped_in_trash", "skipped_not_content", "backoff_wait_ms",
]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    handler = _Capture()
    logger = logging.getLogger(RUN_LOGGER)
    saved = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield handler.records
    logger.removeHandler(handler)
    logger.setLevel(saved[0])
    logger.propagate = saved[1]


def _log() -> logging.Logger:
    return logging.getLogger(RUN_LOGGER)


def _gp() -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})
    return gp


def _ends(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    return [log_record_fields(r) for r in records if r.getMessage() == "sync_run.end"]


def _end(records: list[logging.LogRecord]) -> dict[str, object]:
    ends = _ends(records)
    assert len(ends) == 1, [r.getMessage() for r in records]
    return ends[0]


def _counts(fields: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in fields.items() if k in COUNT_FIELDS}


def _processor() -> DataSourceEntitiesProcessor:
    """A real processor on the fake store: ``ext-old`` is stored with revision r1, anything else is new."""
    proc = _make_processor()
    tx_store = _make_tx_store()
    stored = _make_record(external_record_id="ext-old", external_revision_id="r1")

    async def by_external_id(*, connector_id: str, external_id: str) -> object:
        return stored if external_id == "ext-old" else None

    tx_store.get_record_by_external_id = AsyncMock(side_effect=by_external_id)
    proc.data_store_provider.transaction.side_effect = lambda **_: _make_ctx(tx_store)
    return proc


async def _write_and_wait(proc: DataSourceEntitiesProcessor, *, new: int = 1, wait_s: float = 1.5) -> None:
    """One sync's work: ``new`` new records and one unchanged one, and a wait noted from a child task."""
    batch = [(_make_record(external_record_id=f"ext-{n}"), []) for n in range(new)]
    batch.append((_make_record(external_record_id="ext-old", external_revision_id="r1"), []))
    await proc.on_new_records(batch)
    # A child task, as a connector's fan-out: it shares the run's tally by reference.
    await asyncio.create_task(_note(wait_s))


async def _note(seconds: float) -> None:
    note_backoff_wait(seconds)


def _connector(proc: DataSourceEntitiesProcessor, run_sync: Callable[[], Awaitable[None]]) -> MagicMock:
    connector = MagicMock()
    connector.data_entities_processor = proc
    connector.run_sync = run_sync
    return connector


async def _run(connector: MagicMock, gp: AsyncMock | None = None, **kwargs) -> None:
    try:
        await run_sync_task(connector, "c1", gp or _gp(), _log(), **kwargs)
    finally:
        await join_cleanup_tasks()


EXPECTED = {"records_new": 1, "records_unchanged": 1, "backoff_wait_ms": 1500}


class TestEndFields:
    def test_zeros_are_left_out(self) -> None:
        assert {k: v for k, v in SyncRunStats().end_fields().items() if v is not None} == {}

    def test_one_of_each_gives_the_documented_names_in_order(self) -> None:
        stats = SyncRunStats(backoff_wait_s=1.2346)
        stats.records.update(RecordOutcome)
        stats.skipped.update({SkipReason.OWNED_ELSEWHERE: 1, SkipReason.IN_TRASH: 2, SkipReason.NOT_CONTENT: 3})

        fields = stats.end_fields()

        assert list(fields) == COUNT_FIELDS
        assert fields["records_skipped"] == 6
        assert fields["backoff_wait_ms"] == 1235

    def test_add_folds_counts_but_not_the_wait(self) -> None:
        """The wait is noted on the run's own object, never on a transaction's pending tally."""
        run, pending = SyncRunStats(backoff_wait_s=1.0), SyncRunStats(backoff_wait_s=9.0)
        pending.records[RecordOutcome.NEW] += 2
        pending.skipped[SkipReason.IN_TRASH] += 1

        run.add(pending)

        assert (run.records[RecordOutcome.NEW], run.skipped[SkipReason.IN_TRASH], run.backoff_wait_s) == (2, 1, 1.0)


class TestNoteBackoffWait:
    def test_control_outside_a_run_it_does_nothing(self) -> None:
        """Control: no run is bound, so this passes on main as behaviour (it fails there only on the import)."""
        note_backoff_wait(3.0)
        assert current_run_stats() is None

    def test_zero_and_negative_add_nothing_and_a_wait_adds_up(self) -> None:
        stats = SyncRunStats()
        token = bind_run_stats(stats)
        try:
            note_backoff_wait(0)
            note_backoff_wait(-1.0)
            assert stats.backoff_wait_s == 0
            note_backoff_wait(0.25)
            note_backoff_wait(0.5)
            assert stats.backoff_wait_s == 0.75
        finally:
            reset_run_stats(token)


class TestCountsOnTheEndLine:
    async def test_a_successful_run_carries_its_counts_and_p7a_fields(self, records) -> None:
        proc = _processor()

        await _run(_connector(proc, lambda: _write_and_wait(proc)))

        end = _end(records)
        assert _counts(end) == EXPECTED
        assert end["outcome"] == "success" and end["sync_type"] == "incremental"
        assert isinstance(end["duration_ms"], int)

    async def test_a_failed_run_carries_what_it_did_before_it_raised(self, records) -> None:
        proc = _processor()

        async def run_sync() -> None:
            await _write_and_wait(proc)
            raise RuntimeError("source went away")

        with pytest.raises(RuntimeError):
            await _run(_connector(proc, run_sync))

        end = _end(records)
        assert end["outcome"] == "failure" and end["error_type"] == "RuntimeError"
        assert _counts(end) == EXPECTED

    async def test_a_stopped_run_carries_what_it_did_before_the_stop(self, records) -> None:
        proc = _processor()
        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        worked = asyncio.Event()

        async def run_sync() -> None:
            await _write_and_wait(proc)
            worked.set()
            await asyncio.sleep(30)

        task = await coordinator.spawn(
            lease, run_sync_task(_connector(proc, run_sync), "c1", _gp(), _log(), lease=lease, coordinator=coordinator)
        )
        await worked.wait()
        await coordinator.request_stop("c1")
        await asyncio.gather(task, return_exceptions=True)
        await join_cleanup_tasks()

        end = _end(records)
        assert end["reason"] == "requested" and end["error_class"] == "cancelled"
        assert _counts(end) == EXPECTED

    async def test_a_partial_full_sync_carries_its_counts(self, records) -> None:
        proc = _processor()

        async def run_sync() -> None:
            await _write_and_wait(proc)
            proc._note_full_sync_seen("c1", "records", "r1")  # the fake records are another connector's

        connector = _connector(proc, run_sync)
        connector.full_sync_coverage = MagicMock(return_value=FullSyncCoverage())
        gp = _gp()
        gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, False))

        await _run(connector, gp, start_status=AppStatus.FULL_SYNCING.value)

        end = _end(records)
        assert end["outcome"] == "partial"
        assert _counts(end) == EXPECTED

    async def test_a_skipped_run_carries_none(self, records) -> None:
        proc = _processor()
        await _run(_connector(proc, AsyncMock(side_effect=ConnectorSyncSkippedError("DESKTOP_OFFLINE", "asleep"))))

        end = _end(records)
        assert end["outcome"] == "skipped" and _counts(end) == {}

    async def test_control_a_run_that_counts_nothing_logs_exactly_the_p7a_line(self, records) -> None:
        """Control (no-regression): passes on main as behaviour; it fails there only on the import."""
        await _run(_connector(_processor(), AsyncMock()))

        assert set(_end(records)) == P7A_END_FIELDS


class TestCountsStayWithTheirRun:
    async def test_two_concurrent_runs_on_one_processor_count_only_their_own(self, records) -> None:
        proc = _processor()
        a_wrote, b_wrote = asyncio.Event(), asyncio.Event()

        async def run_a() -> None:
            await _write_and_wait(proc, new=1, wait_s=1.0)
            a_wrote.set()
            await b_wrote.wait()  # b counts while a is still running
            await _note(0.5)

        async def run_b() -> None:
            await a_wrote.wait()
            await _write_and_wait(proc, new=2, wait_s=2.0)
            b_wrote.set()

        await asyncio.gather(
            run_sync_task(_connector(proc, run_a), "a", _gp(), _log()),
            run_sync_task(_connector(proc, run_b), "b", _gp(), _log()),
        )
        await join_cleanup_tasks()

        by_connector = {e["connector_id"]: _counts(e) for e in _ends(records)}
        assert by_connector == {
            "a": {"records_new": 1, "records_unchanged": 1, "backoff_wait_ms": 1500},
            "b": {"records_new": 2, "records_unchanged": 1, "backoff_wait_ms": 2000},
        }

    async def test_the_tally_ends_with_the_run_and_the_finalizer_counts_nothing(self, records) -> None:
        proc = _processor()
        seen_by_finalizer: list[SyncRunStats | None] = []
        real_finalize = sync_runner._finalize

        async def finalize(*args, **kwargs) -> None:
            seen_by_finalizer.append(current_run_stats())
            note_backoff_wait(9.0)
            await real_finalize(*args, **kwargs)

        with patch.object(sync_runner, "_finalize", finalize):
            await _run(_connector(proc, lambda: _write_and_wait(proc)))

        assert seen_by_finalizer == [None]
        assert current_run_stats() is None
        assert _counts(_end(records)) == EXPECTED
