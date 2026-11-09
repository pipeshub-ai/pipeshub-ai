"""What the processor counts for the running sync: each record outcome once its write commits.

The store below stages each transaction's upserts and keeps them only when it commits,
so a retry sees what an earlier attempt really stored.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import DeleteSource
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as dsep,
)
from app.connectors.core.base.data_store import graph_data_store
from app.connectors.core.sync.run_context import (
    RecordOutcome,
    SyncRunStats,
    bind_run_stats,
    reset_run_stats,
)
from app.schema.node_validator import SchemaValidationError
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _DEADLOCK_MESSAGE,
    TransientError,
    _make_ctx,
    _make_processor,
    _make_record,
    _make_tx_store,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.models.entities import Record

pytestmark = pytest.mark.asyncio


@contextlib.contextmanager
def _run() -> Iterator[SyncRunStats]:
    """A sync run's tally, bound as run_sync_task binds it."""
    stats = SyncRunStats()
    token = bind_run_stats(stats)
    try:
        yield stats
    finally:
        reset_run_stats(token)


def _records(stats: SyncRunStats) -> dict[str, int]:
    return {str(k): v for k, v in stats.records.items() if v}


def _skipped(stats: SyncRunStats) -> dict[str, int]:
    return {str(k): v for k, v in stats.skipped.items() if v}


class _Store:
    """Upserts are staged per transaction and kept only when it commits.

    ``fail`` maps a transaction's number (1-based, in the order opened) to the
    error its commit raises.
    """

    def __init__(self, proc: DataSourceEntitiesProcessor, stored: list[Record] = (), **fail: Exception) -> None:
        self.committed: dict[str, Record] = {r.external_record_id: r for r in stored}
        self.fail: dict[int, Exception] = {int(k.removeprefix("t")): v for k, v in fail.items()}
        self.opened = 0
        self.by_key: dict[str, dict] = {}
        proc.data_store_provider.transaction = self.transaction

    @contextlib.asynccontextmanager
    async def transaction(self, **_: object) -> AsyncIterator[AsyncMock]:
        self.opened += 1
        number, staged = self.opened, {}
        tx_store = _make_tx_store()

        async def by_external_id(*, connector_id: str, external_id: str) -> Record | None:
            return staged.get(external_id) or self.committed.get(external_id)

        async def upsert(records: list[Record]) -> None:
            for record in records:
                if record.record_name == "bad":
                    raise SchemaValidationError("records", "refused")
                staged[record.external_record_id] = record.model_copy()

        async def by_key(key: str, **_: object) -> dict | None:
            return self.by_key.get(key)

        tx_store.get_record_by_external_id = AsyncMock(side_effect=by_external_id)
        tx_store.batch_upsert_records = AsyncMock(side_effect=upsert)
        tx_store.get_record_by_key = AsyncMock(side_effect=by_key)
        tx_store.restore_records = AsyncMock(side_effect=lambda items, _batch: [i["id"] for i in items])
        yield tx_store
        if number in self.fail:
            raise self.fail[number]
        self.committed.update(staged)


def _deadlock() -> Exception:
    return TransientError(_DEADLOCK_MESSAGE)


def _batch(n: int, prefix: str = "ext") -> list[tuple[Record, list]]:
    return [(_make_record(external_record_id=f"{prefix}-{i}", external_revision_id="r1"), []) for i in range(n)]


@contextlib.contextmanager
def _no_retry_wait() -> Iterator[MagicMock]:
    with patch.object(graph_data_store, "_retry_delay", MagicMock(return_value=0)) as delay:
        yield delay


class TestWriteOutcomes:
    async def test_new_updated_and_unchanged_by_revision(self) -> None:
        proc = _make_processor()
        _Store(proc, [
            _make_record(external_record_id="edited", external_revision_id="r1"),
            _make_record(external_record_id="same", external_revision_id="r1"),
        ])

        with _run() as stats:
            await proc.on_new_records([
                (_make_record(external_record_id="fresh"), []),
                (_make_record(external_record_id="edited", external_revision_id="r2"), []),
                (_make_record(external_record_id="same", external_revision_id="r1"), []),
            ])

        assert _records(stats) == {"new": 1, "updated": 1, "unchanged": 1}

    async def test_a_placeholder_alone_counts_nothing(self) -> None:
        proc = _make_processor()
        _Store(proc)

        with _run() as stats:
            await proc.on_new_records([(_make_record(external_record_id="stub", is_placeholder=True), [])])

        assert _records(stats) == {} and _skipped(stats) == {}

    async def test_the_real_record_over_its_placeholder_counts_new(self) -> None:
        proc = _make_processor()
        _Store(proc, [_make_record(external_record_id="parent", is_placeholder=True, external_revision_id="r1")])

        with _run() as stats:
            await proc.on_new_records([(_make_record(external_record_id="parent", external_revision_id="r1"), [])])

        assert _records(stats) == {"new": 1}

    async def test_a_placeholder_over_the_real_record_counts_nothing(self) -> None:
        """An ancestor backfill racing the record's own sync: no revision, which would read as changed."""
        proc = _make_processor()
        _Store(proc, [_make_record(external_record_id="parent", external_revision_id="r1")])

        with _run() as stats:
            await proc.on_new_records([(_make_record(external_record_id="parent", is_placeholder=True), [])])

        assert _records(stats) == {}

    async def test_a_record_back_from_the_trash_counts_updated(self) -> None:
        proc = _make_processor()
        trashed = _make_record(
            external_record_id="back", external_revision_id="r1",
            is_deleted=True, delete_source=DeleteSource.CONNECTOR, delete_batch_id="b1",
        )
        _Store(proc, [trashed])

        with _run() as stats:
            await proc.on_new_records([(_make_record(external_record_id="back", external_revision_id="r1"), [])])

        assert _records(stats) == {"updated": 1}

    async def test_a_rename_onto_a_new_external_id_counts_updated(self) -> None:
        """The source renamed the item: a new external id, the id of the record already stored."""
        proc = _make_processor()
        store = _Store(proc)
        record = _make_record(external_record_id="new-name", external_revision_id="r1")
        store.by_key[record.id] = {"_key": record.id, "orgId": "org-1", "connectorId": "conn-1"}

        with _run() as stats:
            await proc.on_new_records([(record, [])])

        assert _records(stats) == {"updated": 1}


class TestSkips:
    @staticmethod
    async def _skip(store_setup, record: Record) -> SyncRunStats:
        proc = _make_processor()
        store = _Store(proc)
        store_setup(store)
        with _run() as stats:
            await proc.on_new_records([(record, [])])
        assert _records(stats) == {}
        return stats

    async def test_another_orgs_record(self) -> None:
        def setup(store: _Store) -> None:
            store.committed["x"] = _make_record(external_record_id="x", org_id="org-2")

        stats = await self._skip(setup, _make_record(external_record_id="x"))
        assert _skipped(stats) == {"owned_elsewhere": 1}

    async def test_an_id_of_another_connector(self) -> None:
        record = _make_record(external_record_id="x")

        def setup(store: _Store) -> None:
            store.by_key[record.id] = {"_key": record.id, "orgId": "org-1", "connectorId": "conn-2"}

        assert _skipped(await self._skip(setup, record)) == {"owned_elsewhere": 1}

    async def test_the_same_id_in_the_trash(self) -> None:
        record = _make_record(external_record_id="x")

        def setup(store: _Store) -> None:
            store.by_key[record.id] = {"_key": record.id, "orgId": "org-1", "connectorId": "conn-1", "isDeleted": True}

        assert _skipped(await self._skip(setup, record)) == {"in_trash": 1}

    async def test_a_record_a_user_deleted(self) -> None:
        def setup(store: _Store) -> None:
            store.committed["x"] = _make_record(
                external_record_id="x", is_deleted=True, delete_source=DeleteSource.USER, delete_batch_id="b1",
            )

        assert _skipped(await self._skip(setup, _make_record(external_record_id="x"))) == {"in_trash": 1}

    async def test_a_system_file(self) -> None:
        stats = await self._skip(lambda _: None, _make_record(external_record_id="x", record_name="Thumbs.db"))
        assert _skipped(stats) == {"not_content": 1}

    async def test_a_placeholder_left_unwritten_is_not_counted_skipped(self) -> None:
        def setup(store: _Store) -> None:
            store.committed["x"] = _make_record(external_record_id="x", org_id="org-2")

        stats = await self._skip(setup, _make_record(external_record_id="x", is_placeholder=True))
        assert _skipped(stats) == {}


class TestCountedOnlyOnCommit:
    async def test_a_rolled_back_write_is_not_counted(self) -> None:
        proc = _make_processor()
        _Store(proc, t1=RuntimeError("commit failed"))

        with _run() as stats, pytest.raises(RuntimeError):
            async with proc._sync_transaction():
                dsep._stats_sink().records[RecordOutcome.NEW] += 1

        assert _records(stats) == {}

    async def test_a_nested_transaction_folds_into_its_parent_and_goes_with_its_rollback(self) -> None:
        proc = _make_processor()
        _Store(proc, t1=RuntimeError("outer commit failed"))

        with _run() as stats, pytest.raises(RuntimeError):
            async with proc._sync_transaction():
                async with proc._sync_transaction():
                    dsep._stats_sink().records[RecordOutcome.NEW] += 1
                assert _records(stats) == {}, "not before the outer commit"

        assert _records(stats) == {}

    async def test_a_deadlock_retry_counts_once(self) -> None:
        proc = _make_processor()
        _Store(proc, t1=_deadlock())

        with _run() as stats, _no_retry_wait() as delay, patch.object(
            proc, "_write_records_slice", wraps=proc._write_records_slice
        ) as slices:
            await proc.on_new_records(_batch(3))

        assert slices.await_count == 2 and delay.call_count == 1, "the retry happened"
        assert _records(stats) == {"new": 3}

    async def test_a_retry_after_a_partial_commit_counts_the_committed_slice_again_as_unchanged(self) -> None:
        """Known limit (docs): a retry re-runs the whole call, and the slices that committed are written again."""
        proc = _make_processor()
        _Store(proc, t2=_deadlock())

        with _run() as stats, _no_retry_wait() as delay, patch.object(
            proc, "_write_records_slice", wraps=proc._write_records_slice
        ) as slices:
            await proc.on_new_records(_batch(150))

        assert slices.await_count == 4 and delay.call_count == 1, "slice 1, slice 2 (deadlock), then both again"
        assert _records(stats) == {"new": 150, "unchanged": 100}

    async def test_a_failure_for_good_after_a_partial_commit_counts_the_whole_batch_failed(self) -> None:
        """Known limit (docs): the last attempt fails in slice 1, so all 150 count failed beside the 100 new."""
        proc = _make_processor()
        attempts = graph_data_store._RETRY_ATTEMPTS
        # Attempt 1: slice 1 commits (t1), slice 2 deadlocks (t2); every later attempt deadlocks in slice 1.
        _Store(proc, t2=_deadlock(), **{f"t{n}": _deadlock() for n in range(3, attempts + 2)})

        with _run() as stats, _no_retry_wait() as delay, pytest.raises(TransientError):
            await proc.on_new_records(_batch(150))

        assert delay.call_count == attempts - 1
        assert _records(stats) == {"new": 100, "failed": 150}


class TestFailures:
    async def test_a_batch_that_fails_for_good_counts_what_did_not_commit(self) -> None:
        proc = _make_processor()
        _Store(proc, t2=RuntimeError("graph down"))

        with _run() as stats, pytest.raises(RuntimeError):
            await proc.on_new_records(_batch(150))

        assert _records(stats) == {"new": 100, "failed": 50}

    async def test_a_failure_after_the_commit_is_not_a_failed_write(self) -> None:
        proc = _make_processor()
        _Store(proc)
        proc.messaging_producer.send_messages = AsyncMock(side_effect=RuntimeError("broker down"))

        with _run() as stats, pytest.raises(RuntimeError):
            await proc.on_new_records(_batch(3))

        assert _records(stats) == {"new": 3}

    async def test_a_record_the_schema_refused_counts_failed(self) -> None:
        proc = _make_processor()
        _Store(proc)
        batch = _batch(3)
        batch[1][0].record_name = "bad"

        with _run() as stats:
            await proc.on_new_records(batch)

        assert _records(stats) == {"new": 2, "failed": 1}

    async def test_a_quarantined_record_is_not_counted_failed_again_when_the_batch_fails(self) -> None:
        proc = _make_processor()
        # Slice 1 (t1) is refused, so its 3 records are written one by one (t2-t4); slice 2 (t5) fails for good.
        _Store(proc, t5=RuntimeError("graph down"))
        batch = _batch(5)
        batch[1][0].record_name = "bad"

        with _run() as stats, patch.object(dsep, "_RECORDS_PER_TRANSACTION", 3), pytest.raises(RuntimeError):
            await proc.on_new_records(batch)

        assert _records(stats) == {"new": 2, "failed": 3}


def _stored_doc(key: str = "r1") -> dict:
    return {"_key": key, "id": key, "orgId": "org-1", "connectorId": "conn-1"}


def _deleted(*keys: str) -> list[dict]:
    return [{"record_id": k} for k in keys]


class TestDeletes:
    @staticmethod
    def _proc(tx_store: AsyncMock, *, soft: bool = False) -> tuple[DataSourceEntitiesProcessor, contextlib.AbstractContextManager]:
        proc = _make_processor()
        proc.data_store_provider.transaction.side_effect = lambda **_: _make_ctx(tx_store)
        return proc, patch.object(dsep, "is_soft_delete_enabled", AsyncMock(return_value=soft))

    async def test_a_hard_delete_counts_what_the_store_removed(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(return_value=_stored_doc())
        tx_store.delete_single_record = AsyncMock(return_value={"success": True, "deleted_records": _deleted("r1")})
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag:
            await proc.on_record_deleted("r1")

        assert _records(stats) == {"deleted": 1}

    async def test_a_hard_delete_of_an_id_not_stored_counts_nothing(self) -> None:
        tx_store = _make_tx_store()
        tx_store.delete_single_record = AsyncMock(return_value={"success": True, "deleted_records": []})
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag:
            await proc.on_record_deleted("never-synced")

        assert _records(stats) == {}

    async def test_a_retry_after_a_publish_failure_counts_the_delete_once(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(side_effect=[_stored_doc(), {}])
        tx_store.delete_single_record = AsyncMock(side_effect=[
            {"success": True, "deleted_records": _deleted("r1"), "eventData": {"payloads": []}},
            {"success": True, "deleted_records": []},
        ])
        proc, flag = self._proc(tx_store)
        proc._publish_delete_events = AsyncMock(side_effect=[_deadlock(), []])

        with _run() as stats, flag, _no_retry_wait() as delay:
            await proc.on_record_deleted("r1")

        assert delay.call_count == 1 and tx_store.delete_single_record.await_count == 2, "the retry happened"
        assert _records(stats) == {"deleted": 1}

    async def test_a_hard_delete_the_store_refused_counts_failed(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(return_value=_stored_doc())
        tx_store.delete_single_record = AsyncMock(return_value={"success": False, "reason": "edge sweep failed"})
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag, pytest.raises(RuntimeError):
            await proc.on_record_deleted("r1")

        assert _records(stats) == {"failed": 1}

    async def test_a_cascade_counts_the_descendants(self) -> None:
        tx_store = _make_tx_store()
        tx_store.delete_records_recursive = AsyncMock(return_value={
            "success": True, "successfully_deleted": 1, "deleted_records": _deleted("folder", "a", "b", "c"),
        })
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag:
            await proc.on_records_deleted_cascade(["folder"], "conn-1")

        assert _records(stats) == {"deleted": 4}

    async def test_a_cascade_with_a_root_already_gone_counts_no_failure(self) -> None:
        tx_store = _make_tx_store()
        tx_store.delete_records_recursive = AsyncMock(return_value={
            "success": True, "successfully_deleted": 1, "failed_count": 1,
            "deleted_records": _deleted("r1"), "failed_records": [{"record_id": "gone", "reason": "Validation failed"}],
        })
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag:
            await proc.on_records_deleted_cascade(["r1", "gone"], "conn-1")

        assert _records(stats) == {"deleted": 1}

    async def test_a_cascade_the_store_refused_counts_every_root_failed(self) -> None:
        tx_store = _make_tx_store()
        tx_store.delete_records_recursive = AsyncMock(return_value={"success": False, "reason": "refused"})
        proc, flag = self._proc(tx_store)

        with _run() as stats, flag:
            await proc.on_records_deleted_cascade(["r1", "r2"], "conn-1")

        assert _records(stats) == {"failed": 2}

    async def test_a_soft_delete_counts_only_what_it_sent_to_the_trash(self) -> None:
        """The root was already in the trash; its one live child goes now."""
        tx_store = _make_tx_store()
        tx_store.soft_delete_records = AsyncMock(return_value={"soft_deleted_records": _deleted("child")})
        proc, flag = self._proc(tx_store, soft=True)
        proc._publish_soft_delete_events = AsyncMock(return_value=[])

        with _run() as stats, flag:
            await proc.on_records_deleted_cascade(["root"], "conn-1", include_trashed_roots=True)

        assert _records(stats) == {"deleted": 1}

    async def test_a_single_record_soft_delete_counts_once(self) -> None:
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(return_value=_stored_doc())
        tx_store.soft_delete_records = AsyncMock(return_value={"soft_deleted_records": _deleted("r1")})
        proc, flag = self._proc(tx_store, soft=True)
        proc._publish_soft_delete_events = AsyncMock(return_value=[])

        with _run() as stats, flag:
            assert await proc.on_record_deleted("r1") is True

        assert _records(stats) == {"deleted": 1}


class TestRunsStayApart:
    async def test_control_outside_a_run_nothing_is_counted_and_nothing_raises(self) -> None:
        """Control: passes on main as behaviour (it fails there only on the import). The KB upload path."""
        proc = _make_processor()
        _Store(proc)
        await proc.on_new_records(_batch(2))
        tx_store = _make_tx_store()
        tx_store.delete_records_recursive = AsyncMock(return_value={"success": True, "deleted_records": _deleted("r1")})
        proc.data_store_provider.transaction = lambda **_: _make_ctx(tx_store)
        with patch.object(dsep, "is_soft_delete_enabled", AsyncMock(return_value=False)):
            await proc.on_records_deleted_cascade(["r1"], "conn-1")

    async def test_two_runs_on_one_processor_interleaved_at_the_commit(self) -> None:
        proc = _make_processor()
        b_committing = asyncio.Event()
        transaction = _Store(proc).transaction

        @contextlib.asynccontextmanager
        async def interleaved(**kwargs: object) -> AsyncIterator[AsyncMock]:
            async with transaction(**kwargs) as tx_store:
                yield tx_store
                if asyncio.current_task().get_name() == "a":
                    await b_committing.wait()  # a's tally is pending while b writes and commits
                else:
                    b_committing.set()

        proc.data_store_provider.transaction = interleaved

        async def run(n: int, prefix: str) -> SyncRunStats:
            with _run() as stats:
                await proc.on_new_records(_batch(n, prefix))
            return stats

        a, b = await asyncio.gather(
            asyncio.create_task(run(1, "a"), name="a"), asyncio.create_task(run(2, "b"), name="b"),
        )

        assert _records(a) == {"new": 1} and _records(b) == {"new": 2}
