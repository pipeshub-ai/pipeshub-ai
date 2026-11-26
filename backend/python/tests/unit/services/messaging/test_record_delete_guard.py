"""A ``deleteRecord`` that arrives after its id was pushed again must not strip the new life of that id.

A purge deletes the row and publishes ``deleteRecord``; the same key can be pushed again before the event is
consumed. Its id-keyed cleanup (entity point, taxonomy links, paid outputs, code edges) is then skipped, and the
steps keyed by the old copy's ``virtualRecordId`` (embeddings, hand-over) still run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.exceptions.indexing_exceptions import IndexingError
from app.models.entities import EntityType
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.messaging.kafka.handlers import record as record_module
from app.services.vector_db import membership
from tests.support.record_handler import make_record_handler

pytestmark = pytest.mark.unit

ORG, REC, OLD_VRID = "org-1", "rec-1", "vr-old"
REFS = [{"id": "t1", "type": "topic"}]
PAYLOAD = {
    "recordId": REC,
    "orgId": ORG,
    "virtualRecordId": OLD_VRID,
    "connectorId": "kb-1",
    "taxonomyRefs": REFS,
}
ID_KEYED = ["delete_entity", "taxonomy", "paid_delete", "paid_repurge", "code_edge"]
VRID_KEYED = ["bulk_delete_embeddings", "hand_over"]
LIVE = {"_key": REC, "orgId": ORG, "virtualRecordId": "vr-new", "isDeleted": False}
TRASHED = {**LIVE, "isDeleted": True}


class World:
    """The handler over fakes, with every step of the delete branch writing to one ordered log."""

    def __init__(self, reads: Any = None) -> None:  # noqa: ANN401
        self.calls: list[tuple[str, tuple]] = []
        self.reads: list[str] = []
        self.handler = make_record_handler()
        self.handler._request_code_edge_build_for_deleted = AsyncMock(
            side_effect=lambda *a: self._log("code_edge", *a)
        )
        self.handler._hand_over_shared_copy = AsyncMock(
            side_effect=lambda *a: self._log("hand_over", *a)
        )
        pipeline = self.handler.event_processor.processor.indexing_pipeline
        pipeline.bulk_delete_embeddings = AsyncMock(
            side_effect=lambda *a, **k: self._log("bulk_delete_embeddings", *a)
        )
        store = AsyncMock()
        store.delete_entity.side_effect = lambda *a: self._log("delete_entity", *a)
        self.store = store
        self.handler.event_processor.sink_orchestrator = MagicMock(
            entity_vector_store=store
        )
        self.paid = MagicMock(
            delete_record=AsyncMock(side_effect=lambda *a: self._log("paid_delete", *a))
        )
        self.script = reads if reads is not None else [None]
        self.handler.event_processor.graph_provider.get_document = AsyncMock(
            side_effect=self._read
        )

    def _log(self, name: str, *args: object) -> None:
        self.calls.append((name, args))

    async def _read(self, key: str, collection: str, **kwargs: object) -> Any:  # noqa: ANN401
        assert kwargs.get("raise_on_error") is True, (
            "an unreadable graph must not read as a purge"
        )
        self.reads.append(key)
        script = self.script
        if callable(script):
            return script(key, len(self.reads))
        answer = script[min(len(self.reads) - 1, len(script) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def delete(self, payload: dict | None = None) -> list[str]:
        async def taxonomy(*a: object) -> None:
            self._log("taxonomy", *a[2:4])

        async def repurge(*a: object, **k: object) -> None:
            self._log("paid_repurge", *a[1:3])

        with (
            patch.object(record_module, "delete_unreached_taxonomy_points", taxonomy),
            patch.object(record_module, "schedule_paid_repurge", repurge),
            patch.object(
                record_module.paid_cache_access,
                "paid_cache_for",
                lambda *a, **k: self.paid,
            ),
            patch.object(
                record_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0.0, create=True
            ),
        ):
            return [
                e.event
                async for e in self.handler.process_event(
                    EventTypes.DELETE_RECORD.value, dict(payload or PAYLOAD)
                )
                if e.event != IndexingEvent.START_DOWNLOAD
            ]

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


# --- the characterisation of today's branch ------------------------------------------------------------------


async def test_delete_branch_today() -> None:
    world = World([None])

    events = await world.delete()

    assert events == ["parsing_complete", "indexing_complete"]
    assert world.names == [
        "bulk_delete_embeddings",
        "delete_entity",
        "taxonomy",
        "paid_delete",
        "paid_repurge",
        "code_edge",
        "hand_over",
    ]
    assert dict(world.calls)["bulk_delete_embeddings"] == ([OLD_VRID],)
    assert dict(world.calls)["delete_entity"] == (ORG, EntityType.RECORD.value, REC)


# --- T45a: the late purge of a record that is live again ----------------------------------------------------


async def test_late_purge_keeps_repushed_record() -> None:
    world = World([LIVE])

    events = await world.delete()

    assert events == ["parsing_complete", "indexing_complete"], (
        "the event is acknowledged"
    )
    assert world.names == VRID_KEYED, "only the old copy's steps run, and each once"
    assert dict(world.calls)["bulk_delete_embeddings"] == ([OLD_VRID],)
    assert world.store.delete_entity.await_count == 0
    for step in ID_KEYED:
        assert step not in world.names


async def test_the_guard_runs_before_any_id_keyed_step() -> None:
    """M15: a guard placed after the deletes reads the answer too late."""
    order: list[str] = []
    world = World()
    world.script = lambda key, n: order.append("read") or LIVE
    world.store.delete_entity.side_effect = lambda *a: order.append("delete_entity")

    await world.delete()

    assert order[0] == "read" and "delete_entity" not in order


# --- T45b ---------------------------------------------------------------------------------------------------


async def test_plain_delete_unchanged() -> None:
    world = World([None])

    await world.delete()

    assert sorted(world.names) == sorted(VRID_KEYED + ID_KEYED)
    assert len(world.names) == len(set(world.names)), "each step exactly once"


async def test_the_guard_reads_a_gone_row_once() -> None:
    world = World([None])

    await world.delete()

    assert world.reads == [REC], "one extra read, none repeated for a row that is gone"


async def test_trashed_row_does_not_supersede() -> None:
    world = World([TRASHED])

    await world.delete()

    assert sorted(world.names) == sorted(VRID_KEYED + ID_KEYED)
    assert world.reads == [REC], "a trashed row is the ordinary delete: no confirming read"


async def test_no_org_and_no_row_fails_closed_as_today() -> None:
    world = World([None])
    payload = {k: v for k, v in PAYLOAD.items() if k != "orgId"}

    with patch.object(record_module, "peek_org_context", lambda: None):
        await world.delete(payload)

    assert dict(world.calls)["bulk_delete_embeddings"] == ([OLD_VRID],)
    assert "delete_entity" not in world.names and "paid_delete" not in world.names
    assert "code_edge" in world.names and "hand_over" in world.names


# --- T45c ---------------------------------------------------------------------------------------------------


async def test_graph_read_failure_redelivers() -> None:
    world = World([ConnectionError("graph down")])

    with pytest.raises((IndexingError, ConnectionError)):
        await world.delete()

    assert world.names == ["bulk_delete_embeddings"], (
        "the old copy's embeddings went; nothing keyed by the id ran"
    )
    assert world.store.delete_entity.await_count == 0


async def test_a_failure_of_the_confirming_read_redelivers_too() -> None:
    world = World([LIVE, ConnectionError("graph down")])

    with pytest.raises((IndexingError, ConnectionError)):
        await world.delete()

    assert world.names == ["bulk_delete_embeddings"]


# --- T45e ---------------------------------------------------------------------------------------------------


async def test_hand_over_with_shared_vrid() -> None:
    """The re-created record shares the old vrid through a live twin: the hand-over still runs, naming the deleted id."""
    twin_row = {**LIVE, "virtualRecordId": OLD_VRID}
    world = World([twin_row])
    helper = MagicMock(
        hand_over_after_record_delete=AsyncMock(return_value=True), close=AsyncMock()
    )
    world.handler._hand_over_shared_copy = (
        record_module.RecordEventHandler._hand_over_shared_copy.__get__(world.handler)
    )

    with patch.object(record_module, "StorageCleanupHelper", return_value=helper):
        await world.delete()

    helper.hand_over_after_record_delete.assert_awaited_once_with(
        ORG, "kb-1", OLD_VRID, deleted_record_id=REC
    )
    assert world.store.delete_entity.await_count == 0


# --- T45h ---------------------------------------------------------------------------------------------------


async def test_lagging_read_confirmed_twice() -> None:
    """A read that still shows the purged row as live is asked again; the second answer decides."""
    lagging = World([LIVE, None])
    await lagging.delete()
    assert lagging.reads == [REC, REC]
    assert sorted(lagging.names) == sorted(VRID_KEYED + ID_KEYED), (
        "gone on the second read: nothing leaked"
    )

    both = World([LIVE, LIVE])
    with patch.object(record_module, "record_delete_guard") as counted:
        await both.delete()
    assert both.reads == [REC, REC]
    assert both.names == VRID_KEYED
    counted.assert_called_once_with("skipped_live")


async def test_the_confirming_read_waits_the_shared_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.indexing import run

    assert (
        record_module.EMPTY_CONFIRM_DELAY_SECONDS
        is membership.EMPTY_CONFIRM_DELAY_SECONDS
    )
    assert run.EMPTY_CONFIRM_DELAY_SECONDS is membership.EMPTY_CONFIRM_DELAY_SECONDS
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(record_module.asyncio, "sleep", sleep)
    world = World([LIVE, LIVE])
    with (
        patch.object(record_module, "paid_cache_access"),
        patch.object(record_module, "schedule_paid_repurge", AsyncMock()),
    ):
        async for _ in world.handler.process_event(
            EventTypes.DELETE_RECORD.value, dict(PAYLOAD)
        ):
            pass

    assert slept == [membership.EMPTY_CONFIRM_DELAY_SECONDS]


# --- T45i ---------------------------------------------------------------------------------------------------


async def test_replayed_delete_after_repush() -> None:
    world = World([LIVE, LIVE, LIVE, LIVE])
    await world.delete()
    await world.delete()
    assert world.names == VRID_KEYED * 2, "a replay is skipped like the first"

    purged_again = World([None])
    await purged_again.delete()
    await purged_again.delete()
    assert sorted(purged_again.names) == sorted((VRID_KEYED + ID_KEYED) * 2), (
        "no live row: runs, idempotently"
    )


# --- T45j ---------------------------------------------------------------------------------------------------


async def test_connector_recreate_keeps_delete() -> None:
    """A connector re-creates a record under a new random id: the old id has no row, so its data goes."""
    new_id = "rec-2"
    world = World(lambda key, n: {**LIVE, "_key": new_id} if key == new_id else None)

    await world.delete()

    assert sorted(world.names) == sorted(VRID_KEYED + ID_KEYED)
    assert dict(world.calls)["delete_entity"] == (ORG, EntityType.RECORD.value, REC)


# --- bounded events -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [EventTypes.SOFT_DELETE_RECORDS.value, EventTypes.BULK_DELETE_RECORDS.value],
)
async def test_vector_only_events_are_not_guarded(event: str) -> None:
    world = World([LIVE])
    world.handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = (
        AsyncMock(return_value={})
    )

    async for _ in world.handler.process_event(
        event, {"virtualRecordIds": [OLD_VRID], "orgId": ORG}
    ):
        pass

    assert world.reads == [], "no graph read for an event that touches no entity point"


# --- N1: an abandoned delete leaves the status of a live record alone ------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        ProgressStatus.NOT_STARTED.value,
        ProgressStatus.QUEUED.value,
        ProgressStatus.FAILED.value,
    ],
)
async def test_an_abandoned_delete_touches_no_status(status: str) -> None:
    handler = make_record_handler()
    graph = handler.event_processor.graph_provider
    graph.get_document = AsyncMock(return_value={**LIVE, "indexingStatus": status})
    graph.compare_and_set_indexing_status = AsyncMock(return_value=[REC])
    handler._RecordEventHandler__update_document_status = AsyncMock()
    message = StreamMessage(
        eventType=EventTypes.DELETE_RECORD.value, payload=dict(PAYLOAD)
    )

    await handler.on_message_abandoned(message, reason="retries exhausted", attempts=3)

    graph.compare_and_set_indexing_status.assert_not_awaited()
    handler._RecordEventHandler__update_document_status.assert_not_awaited()


async def test_an_abandoned_index_event_still_fails_its_record() -> None:
    """Control: the same record and message type other than delete is marked FAILED, as before."""
    handler = make_record_handler()
    graph = handler.event_processor.graph_provider
    graph.get_document = AsyncMock(
        return_value={**LIVE, "indexingStatus": ProgressStatus.QUEUED.value}
    )
    graph.compare_and_set_indexing_status = AsyncMock(return_value=[REC])
    handler._RecordEventHandler__update_document_status = AsyncMock(
        return_value={"_key": REC}
    )
    handler._request_code_edge_build_if_repo_drained = AsyncMock()
    message = StreamMessage(
        eventType=EventTypes.NEW_RECORD.value, payload=dict(PAYLOAD)
    )

    await handler.on_message_abandoned(message, reason="retries exhausted", attempts=3)

    graph.compare_and_set_indexing_status.assert_awaited_once()


# --- T45f: the delete takes the record's lease, as every event of the record does ----------------------------------


class _Leases:
    """A lease manager whose ``record:<id>`` pool can be held by 'an index run of the re-created id'."""

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.asked: list[str] = []

    async def try_acquire(
        self, pool: str, _owner: str, _limit: int, _seconds: float
    ) -> bool:
        self.asked.append(pool)
        return pool not in self.held

    async def release(self, *_a: object) -> None:
        return None

    async def renew_many(self, leases: list, _seconds: float) -> dict:
        return dict.fromkeys(leases, True)


def _delete_stream(world: World) -> Any:  # noqa: ANN401
    return lambda parsed: world.handler.process_event(parsed.eventType, parsed.payload)


async def _drive_postgres(world: World, leases: _Leases) -> None:
    import logging
    from types import SimpleNamespace

    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.postgres.consumer import PgMessagingConsumer

    async def no_slot(_parsed: object) -> None:
        return None

    consumer = PgMessagingConsumer(
        logging.getLogger("t45f"),
        RedisStreamsConfig(topics=["t"], group_id="g"),
        indexing=True,
        concurrency_manager=leases,  # type: ignore[arg-type]
    )
    consumer.message_handler = _delete_stream(world)
    consumer._take_slot = no_slot  # type: ignore[method-assign]
    parsed = SimpleNamespace(
        eventType=EventTypes.DELETE_RECORD.value,
        payload=dict(PAYLOAD),
        org_ctx=None,
        requestId="r",
    )
    await consumer._drive_indexing(parsed, "message:1")


async def _drive_kafka(world: World, leases: _Leases) -> None:
    import asyncio
    import json
    import logging

    from app.services.messaging.config import messaging_env
    from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
    from app.services.messaging.kafka.consumer.indexing_consumer import (
        IndexingKafkaConsumer,
    )

    config = KafkaConsumerConfig(
        topics=["idx"],
        client_id="c",
        group_id="g",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        bootstrap_servers=["broker:9092"],
        ssl=False,
        sasl=None,
    )
    consumer = IndexingKafkaConsumer(logging.getLogger("t45f"), config)
    consumer.running = True
    consumer._requeue_message = AsyncMock()
    consumer.parsing_semaphore = asyncio.Semaphore(1)
    consumer.indexing_semaphore = asyncio.Semaphore(1)
    consumer.concurrency_manager = leases
    consumer.message_handler = _delete_stream(world)
    message = MagicMock(
        topic="t",
        partition=0,
        offset=0,
        value=json.dumps(
            {"eventType": EventTypes.DELETE_RECORD.value, "payload": dict(PAYLOAD)}
        ).encode(),
    )
    with (
        patch.object(
            type(messaging_env),
            "record_lease_wait_seconds",
            new_callable=PropertyMock,
            return_value=0.05,
        ),
        patch.object(
            type(messaging_env),
            "concurrency_acquire_poll_seconds",
            new_callable=PropertyMock,
            return_value=0.01,
        ),
    ):
        await consumer._IndexingKafkaConsumer__process_message_wrapper(message)
    if leases.held:
        consumer._requeue_message.assert_awaited_once()  # handed back as queue time, not charged as a failure
    else:
        consumer._requeue_message.assert_not_awaited()


@pytest.mark.parametrize("broker", ["kafka", "postgres"])
async def test_delete_waits_for_record_lease(broker: str) -> None:
    drive = _drive_kafka if broker == "kafka" else _drive_postgres
    world, leases = World([LIVE]), _Leases()
    leases.held.add(f"record:{REC}")

    with patch.object(record_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0.0, create=True):
        if broker == "postgres":
            from app.services.messaging.consumer_concurrency import (
                RequeueWithoutAttempt,
            )

            with pytest.raises(RequeueWithoutAttempt):
                await drive(world, leases)
        else:
            await drive(world, leases)

    assert f"record:{REC}" in leases.asked, "the delete asked for the record's lease"
    assert world.names == [] and world.reads == [], (
        "held: no step ran and the graph was not read"
    )

    leases.held.clear()
    with (
        patch.object(record_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0.0, create=True),
        patch.object(
            record_module.paid_cache_access,
            "paid_cache_for",
            lambda *a, **k: world.paid,
        ),
    ):
        await drive(world, leases)

    assert world.reads == [REC, REC], "released: the guard reads, confirms, and decides"
    assert world.names == VRID_KEYED
