"""What one sync run logs: its fields on every line, sync_run.start/end, and one failure line.

Contract: docs/logging.md. Records are read through ``log_record_fields`` so the
fields are checked as the formatters print them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
    IncompleteSyncError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.core.sync.sync_coordinator import Admission, LocalSyncCoordinator
from app.connectors.core.sync.sync_runner import join_cleanup_tasks, run_sync_task
from app.connectors.core.sync.task_manager import SyncTaskManager
from app.exceptions.non_retryable import NonRetryableError
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.utils import error_class
from app.utils.logger import TextFormatter, log_record_fields
from app.utils.logging_fields import current_log_fields

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from app.connectors.services.event_service import EventService

RUN_LOGGER = "test.sync_run_logging"
TASK_LOGGER = "app.connectors.core.sync.task_manager"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    handler = _Capture()
    loggers = [logging.getLogger(RUN_LOGGER), logging.getLogger(TASK_LOGGER)]
    saved = [(lg.level, lg.propagate) for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
    yield handler.records
    for lg, (level, propagate) in zip(loggers, saved):
        lg.removeHandler(handler)
        lg.setLevel(level)
        lg.propagate = propagate


def _log() -> logging.Logger:
    return logging.getLogger(RUN_LOGGER)


def _gp() -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "SYNCING"})
    return gp


def _connector(run_sync) -> MagicMock:
    connector = MagicMock()
    connector.run_sync = run_sync
    return connector


def _events(records: list[logging.LogRecord], event: str) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage() == event]


def _one(records: list[logging.LogRecord], event: str) -> tuple[logging.LogRecord, dict[str, object]]:
    found = _events(records, event)
    assert len(found) == 1, [r.getMessage() for r in records]
    return found[0], log_record_fields(found[0])


async def _run(connector: MagicMock, gp: AsyncMock | None = None, **kwargs) -> None:
    try:
        await run_sync_task(connector, "c1", gp or _gp(), _log(), **kwargs)
    finally:
        await join_cleanup_tasks()


def _logs_inside(seen: list[dict[str, object]]) -> Callable[[], Awaitable[None]]:
    async def run_sync() -> None:
        _log().info("inside run_sync")
        seen.append(dict(current_log_fields()))

    return run_sync


class TestStartAndEnd:
    async def test_a_successful_run_has_one_start_and_one_end_with_its_fields(self, records) -> None:
        await _run(_connector(_logs_inside([])))

        _, start = _one(records, "sync_run.start")
        end_record, end = _one(records, "sync_run.end")
        _, inner = _one(records, "inside run_sync")
        assert start["run_id"] == end["run_id"] == inner["run_id"]
        assert start["connector_id"] == end["connector_id"] == inner["connector_id"] == "c1"
        assert start["sync_type"] == end["sync_type"] == "incremental"
        assert end["outcome"] == "success"
        assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
        assert "full_sync_cut_short" not in end
        assert end_record.levelno == logging.INFO
        assert not [r for r in records if r.levelno >= logging.WARNING]

    async def test_a_full_sync_says_so(self, records) -> None:
        await _run(_connector(AsyncMock()), start_status=AppStatus.FULL_SYNCING.value)

        assert _one(records, "sync_run.start")[1]["sync_type"] == "full"
        assert _one(records, "sync_run.end")[1]["sync_type"] == "full"

    async def test_a_skip_is_info_with_its_code(self, records) -> None:
        await _run(_connector(AsyncMock(side_effect=ConnectorSyncSkippedError("DESKTOP_OFFLINE", "asleep"))))

        record, end = _one(records, "sync_run.end")
        assert record.levelno == logging.INFO
        assert end["outcome"] == "skipped" and end["reason"] == "DESKTOP_OFFLINE"
        assert "error_class" not in end


class TestFailure:
    async def test_one_record_at_error_owned_by_the_runner_and_the_task_still_raises(self, records) -> None:
        boom = RuntimeError("boom")
        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        connector = _connector(AsyncMock(side_effect=boom))

        task = await coordinator.spawn(
            lease, run_sync_task(connector, "c1", _gp(), _log(), lease=lease, coordinator=coordinator)
        )
        with pytest.raises(RuntimeError):
            await task
        await join_cleanup_tasks()
        await asyncio.sleep(0)  # the done callback

        assert task.exception() is boom
        errors = [r for r in records if r.levelno >= logging.ERROR]
        assert [r.getMessage() for r in errors] == ["sync_run.end"]
        fields = log_record_fields(errors[0])
        assert fields["outcome"] == "failure" and fields["error_class"] == "internal"
        assert fields["error_type"] == "RuntimeError"
        assert errors[0].exc_info is not None and errors[0].exc_info[1] is boom
        task_record, task_fields = _one(records, "task.failed")
        assert task_record.levelno == logging.DEBUG and task_record.exc_info is None
        assert task_fields["task_key"] == "c1"
        # The sync manager's started/completed lines are DEBUG: sync_run.* already say it.
        assert not [r for r in records if r.name == TASK_LOGGER and r.levelno >= logging.INFO]

    async def test_an_upstream_auth_failure_is_one_warning_without_a_stack(self, records) -> None:
        class Expired(Exception):
            status_code = 401

        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        task = await coordinator.spawn(
            lease,
            run_sync_task(_connector(AsyncMock(side_effect=Expired())), "c1", _gp(), _log(),
                          lease=lease, coordinator=coordinator),
        )
        with pytest.raises(Expired):
            await task
        await join_cleanup_tasks()
        await asyncio.sleep(0)

        record, end = _one(records, "sync_run.end")
        assert record.levelno == logging.WARNING and record.exc_info is None
        assert end["error_class"] == "auth_expired"
        assert _one(records, "task.failed")[0].levelno == logging.DEBUG
        assert not [r for r in records if r.levelno >= logging.ERROR]

    async def test_a_finalizer_error_that_replaces_the_logged_one_is_still_logged(self, records) -> None:
        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        coordinator.end = AsyncMock(side_effect=ValueError("release failed"))
        task = await coordinator.spawn(
            lease,
            run_sync_task(_connector(AsyncMock(side_effect=RuntimeError("boom"))), "c1", _gp(), _log(),
                          lease=lease, coordinator=coordinator),
        )
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)

        task_record, task_fields = _one(records, "task.failed")
        assert task_record.levelno == logging.ERROR and task_record.exc_info is not None
        assert task_fields["error_type"] == "ValueError"

    async def test_an_incomplete_sync_fails_after_its_unlink(self, records) -> None:
        processor = DataSourceEntitiesProcessor(_log(), MagicMock(), MagicMock())
        connector = _connector(None)
        connector.data_entities_processor = processor
        connector.full_sync_coverage = MagicMock(return_value=FullSyncCoverage())

        async def run_sync() -> None:
            processor._note_full_sync_seen("c1", CollectionNames.RECORDS.value, "r1")
            raise IncompleteSyncError("two projects unread")

        connector.run_sync = run_sync
        gp = _gp()
        gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, True))

        with pytest.raises(IncompleteSyncError):
            await _run(connector, gp, start_status=AppStatus.FULL_SYNCING.value)

        gp.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        _, end = _one(records, "sync_run.end")
        assert end["outcome"] == "failure" and end["error_type"] == "IncompleteSyncError"

    async def test_a_full_sync_whose_unlink_failed_is_partial(self, records) -> None:
        processor = DataSourceEntitiesProcessor(_log(), MagicMock(), MagicMock())
        connector = _connector(None)
        connector.data_entities_processor = processor
        connector.full_sync_coverage = MagicMock(return_value=FullSyncCoverage())

        async def run_sync() -> None:
            processor._note_full_sync_seen("c1", CollectionNames.RECORDS.value, "r1")

        connector.run_sync = run_sync
        gp = _gp()
        gp.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(0, False))

        await _run(connector, gp, start_status=AppStatus.FULL_SYNCING.value)

        record, end = _one(records, "sync_run.end")
        assert record.levelno == logging.WARNING
        assert end["outcome"] == "partial" and end["full_sync_cut_short"] is True
        assert not [r for r in records if "did not finish" in r.getMessage()]

    async def test_a_failed_member_write_is_one_classed_step_failure_and_partial(self, records) -> None:
        processor = DataSourceEntitiesProcessor(_log(), MagicMock(), MagicMock())
        processor.finish_full_sync_memberships = AsyncMock(side_effect=ConnectionError("graph down"))
        connector = _connector(None)
        connector.data_entities_processor = processor
        connector.full_sync_coverage = MagicMock(return_value=FullSyncCoverage())

        async def run_sync() -> None:
            processor._note_full_sync_seen("c1", CollectionNames.RECORDS.value, "r1")

        connector.run_sync = run_sync

        await _run(connector, _gp(), start_status=AppStatus.FULL_SYNCING.value)

        record, step = _one(records, "sync_step.failed")
        assert record.levelno == logging.ERROR and step["step"] == "finish_memberships"
        assert step["error_class"] == "unavailable"
        _, end = _one(records, "sync_run.end")
        assert end["outcome"] == "partial" and step["run_id"] == end["run_id"]


async def _blocks(started: asyncio.Event) -> None:
    started.set()
    await asyncio.sleep(30)


class TestStop:
    async def _stopped_by(
        self, records, stop, *, error: BaseException | None = None, **run_kwargs
    ) -> tuple[logging.LogRecord, dict[str, object]]:
        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        started = asyncio.Event()

        async def run_sync() -> None:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                if error is not None:
                    raise error from None
                raise

        task = await coordinator.spawn(
            lease,
            run_sync_task(_connector(run_sync), "c1", _gp(), _log(), lease=lease, coordinator=coordinator,
                          **run_kwargs),
        )
        await started.wait()
        await stop(coordinator, lease, task)
        await asyncio.gather(task, return_exceptions=True)
        await join_cleanup_tasks()
        await asyncio.sleep(0)
        return _one(records, "sync_run.end")

    async def test_a_requested_stop(self, records) -> None:
        record, end = await self._stopped_by(records, _request_stop, start_status=AppStatus.FULL_SYNCING.value)
        assert record.levelno == logging.INFO
        assert end["outcome"] == "failure" and end["error_class"] == "cancelled" and end["reason"] == "requested"
        assert end["full_sync_cut_short"] is True
        assert not [r for r in records if r.levelno >= logging.WARNING]

    async def test_an_external_cancel(self, records) -> None:
        _, end = await self._stopped_by(records, _cancel)
        assert end["reason"] == "cancelled"

    async def test_a_cancel_during_shutdown(self, records) -> None:
        async def shutdown(coordinator, lease, task) -> None:
            coordinator.shutting_down = True
            await _cancel(coordinator, lease, task)

        _, end = await self._stopped_by(records, shutdown)
        assert end["reason"] == "shutdown"

    async def test_a_lost_lease_is_a_warning_and_writes_no_outcome(self, records) -> None:
        gp = _gp()
        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        started = asyncio.Event()
        task = await coordinator.spawn(
            lease, run_sync_task(_connector(lambda: _blocks(started)), "c1", gp, _log(),
                                 lease=lease, coordinator=coordinator),
        )
        await started.wait()
        lease.lost.set()
        await task
        await join_cleanup_tasks()

        record, end = _one(records, "sync_run.end")
        assert record.levelno == logging.WARNING
        assert end["outcome"] == "failure" and end["reason"] == "lease_lost"
        writes = [c.args[2] for c in gp.update_node.await_args_list]
        assert not [w for w in writes if ConnectorStateKeys.LAST_SYNC_OUTCOME in w or "status" in w]

    async def test_a_stop_that_also_raised_logs_the_error_once_with_the_stop_cause(self, records) -> None:
        record, end = await self._stopped_by(records, _request_stop, error=KeyError("while unwinding"))
        assert record.levelno == logging.ERROR and record.exc_info is not None
        assert end["reason"] == "requested" and end["error_class"] == "internal"
        assert _one(records, "task.failed")[0].levelno == logging.DEBUG


async def _request_stop(coordinator, lease, task) -> None:
    await coordinator.request_stop("c1")


async def _cancel(coordinator, lease, task) -> None:
    task.cancel()


class TestFieldsStayWithTheirRun:
    async def test_nothing_leaks_to_the_caller_after_success_failure_or_cancel(self) -> None:
        await _run(_connector(AsyncMock()))
        assert "run_id" not in current_log_fields()

        with pytest.raises(RuntimeError):
            await _run(_connector(AsyncMock(side_effect=RuntimeError("x"))))
        assert "run_id" not in current_log_fields()

        started = asyncio.Event()

        async def caller() -> dict[str, object]:
            try:
                await run_sync_task(_connector(lambda: _blocks(started)), "c1", _gp(), _log())
            except asyncio.CancelledError:
                return dict(current_log_fields())
            return {}

        outer = asyncio.create_task(caller())
        await started.wait()
        outer.cancel()
        assert "run_id" not in await outer
        await join_cleanup_tasks()

    async def test_two_concurrent_runs_keep_their_own_run_id(self, records) -> None:
        seen_a: list[dict[str, object]] = []
        seen_b: list[dict[str, object]] = []

        async def slow(seen: list[dict[str, object]]) -> None:
            await asyncio.sleep(0)
            seen.append(dict(current_log_fields()))
            await asyncio.sleep(0)
            seen.append(dict(current_log_fields()))

        await asyncio.gather(
            run_sync_task(_connector(lambda: slow(seen_a)), "a", _gp(), _log()),
            run_sync_task(_connector(lambda: slow(seen_b)), "b", _gp(), _log()),
        )
        await join_cleanup_tasks()

        assert {f["connector_id"] for f in seen_a} == {"a"} and {f["connector_id"] for f in seen_b} == {"b"}
        assert len({f["run_id"] for f in seen_a}) == 1 and len({f["run_id"] for f in seen_b}) == 1
        assert seen_a[0]["run_id"] != seen_b[0]["run_id"]

    async def test_a_finalizer_failure_is_one_error_with_the_runs_id(self, records) -> None:
        gp = _gp()
        gp.update_node = AsyncMock(side_effect=ConnectionError("graph down"))

        await _run(_connector(AsyncMock()), gp)

        _, end = _one(records, "sync_run.end")
        assert end["outcome"] == "success"
        step = [r for r in _events(records, "sync_step.failed") if log_record_fields(r)["step"] == "idle_write"]
        assert len(step) == 1
        assert step[0].levelno == logging.ERROR and step[0].exc_info is not None
        assert log_record_fields(step[0])["run_id"] == end["run_id"]


class TestTaskManager:
    async def test_an_exception_nothing_logged_is_logged_at_its_level_with_the_stack(self, records) -> None:
        manager = SyncTaskManager(label="Reindex")

        async def fails() -> None:
            raise RuntimeError("reindex broke")

        task = await manager.start_if_idle("reindex:c1", fails())
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

        record, fields = _one(records, "task.failed")
        assert record.levelno == logging.ERROR and record.exc_info is not None
        assert fields["task_label"] == "reindex" and fields["error_class"] == "internal"

    async def test_an_exception_already_logged_is_debug(self, records) -> None:
        manager = SyncTaskManager(label="Reindex")

        async def fails() -> None:
            exc = RuntimeError("logged below")
            error_class.log_failure(_log(), "inner.failed", exc)
            raise exc

        task = await manager.start_if_idle("reindex:c1", fails())
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

        assert _one(records, "task.failed")[0].levelno == logging.DEBUG

    @pytest.mark.parametrize(("kwargs", "level"), [
        ({"label": "Reindex"}, logging.INFO),
        ({"label": "Sync", "lifecycle_level": logging.DEBUG}, logging.DEBUG),
    ])
    async def test_lifecycle_lines_use_the_managers_level(self, records, kwargs, level) -> None:
        manager = SyncTaskManager(**kwargs)
        task = await manager.start_if_idle("k", asyncio.sleep(0))
        await task
        await asyncio.sleep(0)

        lines = [r for r in records if r.name == TASK_LOGGER]
        assert [r.levelno for r in lines] == [level, level]


class TestEntryPoints:
    def _service(self) -> EventService:
        from app.connectors.services.event_service import EventService

        return EventService(_log(), MagicMock(), _gp())

    async def test_an_event_names_the_instance_on_its_lines(self, records) -> None:
        service = self._service()

        async def handler(_name, _payload) -> bool:
            _log().info("inside handler")
            return True

        with patch.object(service, "_handle_init", side_effect=handler):
            assert await service.process_event("rss.init", {"connectorId": "c1", "orgId": "o1"}) is True
        fields = _one(records, "inside handler")[1]
        assert fields["connector_id"] == "c1" and fields["connector_type"] == "rss"
        assert "connector_id" not in current_log_fields()

    async def test_without_a_connector_id_only_the_type_is_bound(self, records) -> None:
        service = self._service()

        async def handler(_name, _payload) -> bool:
            _log().info("inside handler")
            return True

        with patch.object(service, "_handle_delete", side_effect=handler):
            await service.process_event("rss.delete", {"orgId": "o1"})
        fields = _one(records, "inside handler")[1]
        assert "connector_id" not in fields and fields["connector_type"] == "rss"

    async def test_a_malformed_event_binds_nothing(self, records) -> None:
        with pytest.raises(NonRetryableError):
            await self._service().process_event("rss", {"connectorId": "c1"})
        assert all("connector_id" not in log_record_fields(r) for r in records)

    async def test_a_run_carries_the_stored_type_not_the_events_name(self, records) -> None:
        service = self._service()
        service.graph_provider.get_document = AsyncMock(return_value={"id": "c1", "type": "Web"})
        coordinator = LocalSyncCoordinator(_log())
        connector = _connector(_logs_inside([]))

        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
             patch.object(service, "_org_affinity_bounced", AsyncMock(return_value=False)), \
             patch.object(service, "_ensure_connector", AsyncMock(return_value=connector)):
            assert await service.process_event("rss.resync", {"connectorId": "c1", "orgId": "o1"}) is True
            await asyncio.gather(*coordinator._tasks.tasks_with_prefix("c1"))
            await join_cleanup_tasks()

        fields = _one(records, "inside run_sync")[1]
        assert fields["connector_type"] == "web" and fields["connector_id"] == "c1" and fields["run_id"]

    async def test_the_stored_type_is_not_left_bound_after_a_direct_call(self) -> None:
        service = self._service()
        service.graph_provider.get_document = AsyncMock(
            return_value={"id": "c1", "type": "Web", ConnectorStateKeys.IS_ACTIVE: False}
        )
        _, lease = await LocalSyncCoordinator(_log()).begin("c1")

        with patch.object(service, "_drop_queued_intent", AsyncMock()):
            result = await service._start_sync_with_lease("rss", {"connectorId": "c1", "orgId": "o1"}, lease, MagicMock())

        assert result == (True, False)
        assert "connector_type" not in current_log_fields()

    async def test_a_refused_admission_says_why(self, records) -> None:
        service = self._service()
        coordinator = MagicMock()
        coordinator.begin = AsyncMock(return_value=(Admission.REFUSED_BY_STOP, None))

        with patch("app.connectors.services.event_service.get_coordinator", return_value=coordinator), \
             patch.object(service, "_org_affinity_bounced", AsyncMock(return_value=False)):
            assert await service.process_event("rss.start", {"connectorId": "c1", "orgId": "o1"}) is True

        record, fields = _one(records, "sync_run.refused")
        assert record.levelno == logging.INFO and fields["reason"] == "refused_by_stop"
        assert fields["connector_id"] == "c1"

    async def test_a_run_started_on_the_boot_path_names_the_instance(self, records) -> None:
        from app.connectors.core.factory.connector_factory import ConnectorFactory

        coordinator = LocalSyncCoordinator(_log())
        connector = _connector(_logs_inside([]))
        config_service = MagicMock(get_config=AsyncMock(return_value={"sync": {"selectedStrategy": "SCHEDULED"}}))

        with patch.object(ConnectorFactory, "initialize_connector", AsyncMock(return_value=connector)), \
             patch("app.connectors.core.factory.connector_factory.get_coordinator", return_value=coordinator):
            result = await ConnectorFactory.create_and_start_sync(
                name="rss", logger=_log(), data_store_provider=MagicMock(graph_provider=_gp()),
                config_service=config_service, connector_id="c1", scope="team", created_by="u1",
            )
            await asyncio.gather(*coordinator._tasks.tasks_with_prefix("c1"))
            await join_cleanup_tasks()

        assert result is connector
        fields = _one(records, "inside run_sync")[1]
        assert fields["connector_type"] == "rss" and fields["connector_id"] == "c1" and fields["run_id"]
        assert _one(records, "sync_run.end")[1]["connector_type"] == "rss"

    async def test_the_boot_path_names_the_instance_and_logs_a_start_failure(self, records) -> None:
        from app.connectors.core.factory.connector_factory import ConnectorFactory

        async def initialize(**_kwargs) -> MagicMock:
            _log().info("inside initialize")
            return MagicMock()

        coordinator = MagicMock()
        coordinator.begin = AsyncMock(side_effect=ConnectionError("redis down"))
        config_service = MagicMock(get_config=AsyncMock(return_value={"sync": {"selectedStrategy": "SCHEDULED"}}))

        with patch.object(ConnectorFactory, "initialize_connector", side_effect=initialize), \
             patch("app.connectors.core.factory.connector_factory.get_coordinator", return_value=coordinator):
            result = await ConnectorFactory.create_and_start_sync(
                name="rss", logger=_log(), data_store_provider=MagicMock(), config_service=config_service,
                connector_id="c1", scope="team", created_by="u1",
            )

        assert result is None
        inner = _one(records, "inside initialize")[1]
        assert inner["connector_id"] == "c1" and inner["connector_type"] == "rss"
        record, fields = _one(records, "sync_run.start_failed")
        assert record.levelno == logging.WARNING and fields["error_class"] == "unavailable"
        assert fields["connector_type"] == "rss"
        assert "connector_id" not in current_log_fields()


class TestSchemaRejectionText:
    """A sync that a graph write's schema refuses logs the rule and the field, never the rejected value (F36)."""

    PHONE = "+1 555 0100 Seeded"

    async def test_sync_run_end_and_task_failed_leave_the_rejected_value_out(self, records) -> None:
        async def run_sync() -> None:
            NodeSchemaValidator().validate_node("users", {"email": "u1@example.invalid", "businessPhones": self.PHONE})

        coordinator = LocalSyncCoordinator(_log())
        _, lease = await coordinator.begin("c1")
        task = await coordinator.spawn(
            lease, run_sync_task(_connector(run_sync), "c1", _gp(), _log(), lease=lease, coordinator=coordinator)
        )
        with pytest.raises(SchemaValidationError):
            await task
        await join_cleanup_tasks()
        await asyncio.sleep(0)  # the done callback

        end_record, end = _one(records, "sync_run.end")
        task_record, task_fields = _one(records, "task.failed")
        formatter = TextFormatter()
        for record in (end_record, task_record):
            assert self.PHONE not in formatter.format(record)  # fields and the stack written at ERROR
        assert end_record.levelno == logging.ERROR and end_record.exc_text
        assert "'businessPhones'" in end["error_message"] and "is not of type 'array'" in end["error_message"]
        assert end["error_type"] == task_fields["error_type"] == "SchemaValidationError"
