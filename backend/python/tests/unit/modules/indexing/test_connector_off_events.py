"""The connector-off rule shared by the record handler and the read-time filter.

The filter may only settle an event whose whole effect in the handler is the
skip for a turned-off or removed connector. The parity tests below run the
real handler on the same records and check it does exactly what the filter
would have done instead, so the two cannot drift apart unnoticed.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, OriginTypes, ProgressStatus
from app.modules.indexing.connector_off_events import (
    ConnectorState,
    GraphConnectorOffFilter,
    ReadTimeOutcome,
    connector_off_updates,
    read_time_outcome,
)
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.messaging.connector_off import settle_connector_off
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.utils.user_errors import CONNECTOR_OFF
from tests.support.fake_connector_graph import FakeConnectorGraph

NEW = EventTypes.NEW_RECORD.value
UPDATE = EventTypes.UPDATE_RECORD.value
REINDEX = EventTypes.REINDEX_RECORD.value
DELETE = EventTypes.DELETE_RECORD.value


def _payload(record_id: str = "r1", connector_id: str = "conn-off", **extra: object) -> dict:
    return {
        "recordId": record_id,
        "connectorId": connector_id,
        "orgId": "org-1",
        "extension": "txt",
        "mimeType": "text/plain",
        "virtualRecordId": f"vr-{record_id}",
        **extra,
    }


def _message(event_type: str = NEW, **payload: object) -> StreamMessage:
    return StreamMessage(eventType=event_type, payload=_payload(**payload))


def _states(**states: ConnectorState):  # noqa: ANN202
    return lambda connector_id: states.get(connector_id, ConnectorState.UNKNOWN)


def _record(**fields: object) -> dict:
    return {
        "id": "r1",
        "_key": "r1",
        "connectorId": "conn-off",
        "origin": OriginTypes.CONNECTOR.value,
        "indexingStatus": ProgressStatus.QUEUED.value,
        "extractionStatus": ProgressStatus.NOT_STARTED.value,
        **fields,
    }


OFF = _states(**{"conn-off": ConnectorState.OFF})
REMOVED = _states(**{"conn-off": ConnectorState.REMOVED})


class TestReadTimeOutcome:
    @pytest.mark.parametrize("event_type", [NEW, UPDATE, REINDEX])
    def test_a_queued_record_of_an_off_connector_is_marked_off(self, event_type: str) -> None:
        # txt is reconciled block by block, so the handler does not delete
        # embeddings first on an update or reindex.
        payload = _payload()

        assert read_time_outcome(event_type, payload, _record(), OFF) is ReadTimeOutcome.SETTLE_CONNECTOR_OFF

    def test_a_queued_record_of_a_removed_connector_is_settled_without_a_write(self) -> None:
        assert read_time_outcome(NEW, _payload(), _record(), REMOVED) is ReadTimeOutcome.SETTLE

    def test_a_vector_store_rebuild_is_never_settled(self) -> None:
        """The rebuild deliberately re-embeds disabled connectors from blob."""
        payload = _payload(vectorDbOnly=True)

        assert read_time_outcome(REINDEX, payload, _record(), OFF) is ReadTimeOutcome.PASS
        assert read_time_outcome(REINDEX, payload, _record(), REMOVED) is ReadTimeOutcome.PASS

    def test_an_enrichment_resume_is_never_settled(self) -> None:
        """The handler ends the enrichment instead, which releases the record's copies."""
        record = _record(
            indexingStatus=ProgressStatus.COMPLETED.value,
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
        )

        assert read_time_outcome(NEW, _payload(), record, OFF) is ReadTimeOutcome.PASS
        assert read_time_outcome(NEW, _payload(), record, REMOVED) is ReadTimeOutcome.PASS

    @pytest.mark.parametrize("event_type", [DELETE, EventTypes.BULK_DELETE_RECORDS.value, "deleteConnectorEmbeddings"])
    def test_other_event_types_are_never_settled(self, event_type: str) -> None:
        assert read_time_outcome(event_type, _payload(), _record(), OFF) is ReadTimeOutcome.PASS

    @pytest.mark.parametrize("event_type", [UPDATE, REINDEX])
    def test_an_update_that_first_clears_embeddings_is_not_settled(self, event_type: str) -> None:
        payload = _payload(extension="pptx", mimeType="application/vnd.ms-powerpoint")

        assert read_time_outcome(event_type, payload, _record(), OFF) is ReadTimeOutcome.PASS

    def test_an_already_indexed_record_is_not_settled(self) -> None:
        """The handler's finally block promotes its queued copies."""
        record = _record(indexingStatus=ProgressStatus.COMPLETED.value)

        assert read_time_outcome(NEW, _payload(), record, OFF) is ReadTimeOutcome.PASS
        assert read_time_outcome(NEW, _payload(), record, REMOVED) is ReadTimeOutcome.PASS

    @pytest.mark.parametrize(
        "status",
        [ProgressStatus.EMPTY.value, ProgressStatus.ENABLE_MULTIMODAL_MODELS.value],
    )
    def test_a_removed_connectors_record_whose_copies_would_be_released_is_not_settled(self, status: str) -> None:
        assert read_time_outcome(NEW, _payload(), _record(indexingStatus=status), REMOVED) is ReadTimeOutcome.PASS

    @pytest.mark.parametrize(
        "status",
        [ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value, ProgressStatus.ENABLE_MULTIMODAL_MODELS.value],
    )
    def test_a_status_another_event_may_still_need_is_not_overwritten(self, status: str) -> None:
        """A COMPLETED record's buffered newRecord releases its queued copies;
        marking it off first would make that newRecord miss the guard."""
        record = _record(indexingStatus=status)

        assert read_time_outcome(UPDATE, _payload(), record, OFF) is ReadTimeOutcome.PASS

    def test_a_record_in_progress_is_left_to_the_delivery_that_may_hold_it(self) -> None:
        record = _record(indexingStatus=ProgressStatus.IN_PROGRESS.value)

        assert read_time_outcome(NEW, _payload(), record, OFF) is ReadTimeOutcome.PASS

    def test_an_upload_is_not_gated_on_any_connector(self) -> None:
        record = _record(origin=OriginTypes.UPLOAD.value)

        assert read_time_outcome(NEW, _payload(), record, OFF) is ReadTimeOutcome.PASS

    def test_a_record_now_on_another_connector_is_judged_by_that_one(self) -> None:
        record = _record(connectorId="conn-on")
        states = _states(**{"conn-off": ConnectorState.OFF, "conn-on": ConnectorState.ACTIVE})

        assert read_time_outcome(NEW, _payload(), record, states) is ReadTimeOutcome.PASS

    def test_an_unknown_connector_state_settles_nothing(self) -> None:
        assert read_time_outcome(NEW, _payload(), _record(), _states()) is ReadTimeOutcome.PASS

    def test_a_trashed_record_is_settled_without_a_write(self) -> None:
        assert read_time_outcome(NEW, _payload(), _record(isDeleted=True), OFF) is ReadTimeOutcome.SETTLE

    def test_a_missing_record_is_settled_only_for_a_removed_connector(self) -> None:
        """A removed connector's records go with it. For one that is only off, a
        record read that silently missed must not drop live work."""
        assert read_time_outcome(NEW, _payload(), None, REMOVED) is ReadTimeOutcome.SETTLE
        assert read_time_outcome(NEW, _payload(), None, OFF) is ReadTimeOutcome.PASS

    def test_the_status_written_keeps_a_completed_extraction_and_mirrors_parsing(self) -> None:
        updates = connector_off_updates(
            _record(
                extractionStatus=ProgressStatus.COMPLETED.value,
                parsingStatus=ProgressStatus.IN_PROGRESS.value,
                processingStartedAt=123,
            )
        )

        assert updates == {
            "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
            "extractionStatus": ProgressStatus.COMPLETED.value,
            "parsingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
            "processingStartedAt": None,
            "reason": CONNECTOR_OFF,
        }


def _handler(graph: FakeConnectorGraph) -> tuple[RecordEventHandler, MagicMock]:
    event_processor = MagicMock()
    event_processor.graph_provider = graph
    event_processor.processor.indexing_pipeline = AsyncMock()
    event_processor.sink_orchestrator = None
    handler = RecordEventHandler(
        logger=logging.getLogger("test"),
        config_service=AsyncMock(),
        event_processor=event_processor,
        producer=AsyncMock(),
    )
    return handler, event_processor


SETTLED_SCENARIOS = [
    pytest.param(NEW, {}, {}, True, id="off-new"),
    pytest.param(UPDATE, {}, {}, True, id="off-reconciled-update"),
    pytest.param(NEW, {}, {"parsingStatus": ProgressStatus.IN_PROGRESS.value,
                            "extractionStatus": ProgressStatus.COMPLETED.value,
                            "processingStartedAt": 5}, True, id="off-mid-parse"),
    pytest.param(NEW, {}, {"indexingStatus": ProgressStatus.FAILED.value}, True, id="off-failed"),
    pytest.param(NEW, {}, {}, False, id="removed-new"),
    pytest.param(REINDEX, {"forceReindex": True}, {"indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value}, False,
                 id="removed-forced-reindex"),
    pytest.param(NEW, {}, {"isDeleted": True}, True, id="off-trashed"),
]


class TestTheHandlerDoesExactlyWhatTheFilterSettles:
    @pytest.mark.parametrize(("event_type", "payload_extra", "record_fields", "connector_on_graph"), SETTLED_SCENARIOS)
    async def test_parity(
        self, event_type: str, payload_extra: dict, record_fields: dict, connector_on_graph: bool
    ) -> None:
        def build() -> FakeConnectorGraph:
            graph = FakeConnectorGraph()
            if connector_on_graph:
                graph.add_connector("conn-off", active=False)
            graph.add_record("r1", "conn-off", **record_fields)
            return graph

        message = _message(event_type, **payload_extra)

        # The filter, on one copy of the graph.
        filtered = build()
        result = await GraphConnectorOffFilter(filtered, logging.getLogger("t"), 15.0).settle([message])

        # The handler, on another.
        handled = build()
        handler, event_processor = _handler(handled)
        events = [e.event async for e in handler.process_event(event_type, dict(message.payload))]

        assert result.settled == frozenset({0})
        assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
        assert filtered.records == handled.records
        assert event_processor.processor.indexing_pipeline.mock_calls == []

    async def test_a_record_whose_connector_is_on_is_not_settled_and_the_handler_indexes_it(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=True)
        graph.add_record("r1", "conn-off")

        result = await GraphConnectorOffFilter(graph, logging.getLogger("t"), 15.0).settle([_message()])

        assert result.settled == frozenset()
        assert graph.batch_updates == []


def _filter(graph: FakeConnectorGraph, clock: list[float] | None = None) -> GraphConnectorOffFilter:
    now = clock if clock is not None else [0.0]
    return GraphConnectorOffFilter(graph, logging.getLogger("t"), 15.0, clock=lambda: now[0])


class TestGraphConnectorOffFilter:
    async def test_a_batch_costs_one_connector_read_one_record_read_and_one_write(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_connector("conn-on", active=True)
        messages = []
        for i in range(50):
            graph.add_record(f"off-{i}", "conn-off")
            messages.append(_message(record_id=f"off-{i}"))
            messages.append(_message(record_id=f"on-{i}", connector_id="conn-on"))

        result = await _filter(graph).settle(messages)

        assert result.settled == frozenset(range(0, 100, 2))
        assert dict(result.by_connector) == {("conn-off", "off"): 50}
        assert graph.calls == {
            "get_nodes_by_field_in:apps": 1,
            "get_nodes_by_field_in:records": 1,
            "update_nodes_fields_if_match:records": 1,
        }
        assert {r["indexingStatus"] for k, r in graph.records.items()} == {ProgressStatus.AUTO_INDEX_OFF.value}

    async def test_a_connector_known_to_be_on_costs_no_graph_call_until_it_is_due_a_refresh(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-on", active=True)
        clock = [0.0]
        connector_filter = _filter(graph, clock)

        await connector_filter.settle([_message(connector_id="conn-on")])
        await connector_filter.settle([_message(connector_id="conn-on")])
        assert graph.calls == {"get_nodes_by_field_in:apps": 1}

        clock[0] = 16.0
        await connector_filter.settle([_message(connector_id="conn-on")])
        assert graph.calls == {"get_nodes_by_field_in:apps": 2}

    async def test_turning_a_connector_back_on_takes_effect_on_the_next_batch(self) -> None:
        """Off is never remembered: a sync started by re-enabling must index."""
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")
        graph.add_record("r2", "conn-off")
        connector_filter = _filter(graph)

        first = await connector_filter.settle([_message(record_id="r1")])
        graph.set_active("conn-off", True)
        second = await connector_filter.settle([_message(record_id="r2")])

        assert first.settled == frozenset({0})
        assert second.settled == frozenset()
        assert graph.records["r2"]["indexingStatus"] == ProgressStatus.QUEUED.value

    async def test_a_removed_connector_is_settled_without_any_write(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_record("r1", "conn-gone")

        result = await _filter(graph).settle(
            [_message(record_id="r1", connector_id="conn-gone"), _message(record_id="r-deleted", connector_id="conn-gone")]
        )

        assert result.settled == frozenset({0, 1})
        assert dict(result.by_connector) == {("conn-gone", "removed"): 2}
        assert graph.batch_updates == []
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.QUEUED.value

    @pytest.mark.parametrize("unreadable", ["apps", "records"])
    async def test_an_unreadable_graph_settles_nothing_and_pauses_the_filter(self, unreadable: str) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")
        graph.fail_reads.add(unreadable)
        clock = [0.0]
        connector_filter = _filter(graph, clock)

        result = await connector_filter.settle([_message()])
        calls_after_failure = sum(graph.calls.values())
        again = await connector_filter.settle([_message()])

        assert result.settled == again.settled == frozenset()
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.QUEUED.value
        # Paused: one timeout per pause, not one per batch.
        assert sum(graph.calls.values()) == calls_after_failure

        graph.fail_reads.clear()
        clock[0] = 31.0
        assert (await connector_filter.settle([_message()])).settled == frozenset({0})

    async def test_a_failed_status_write_settles_only_what_needed_no_write(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")
        graph.add_record("r2", "conn-gone")
        graph.fail_writes = True

        result = await _filter(graph).settle(
            [_message(record_id="r1"), _message(record_id="r2", connector_id="conn-gone")]
        )

        assert result.settled == frozenset({1})
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.QUEUED.value

    async def test_rebuild_and_enrichment_resume_events_of_an_off_connector_are_left_alone(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("rebuilt", "conn-off", indexingStatus=ProgressStatus.COMPLETED.value)
        graph.add_record(
            "resumed",
            "conn-off",
            indexingStatus=ProgressStatus.COMPLETED.value,
            extractionStatus=ProgressStatus.IN_PROGRESS.value,
        )

        result = await _filter(graph).settle([
            _message(REINDEX, record_id="rebuilt", vectorDbOnly=True),
            _message(record_id="resumed"),
        ])

        assert result.settled == frozenset()
        assert graph.batch_updates == []

    async def test_positions_refer_to_the_whole_batch_including_unparseable_entries(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")

        result = await settle_connector_off(_filter(graph), [None, _message(), None], logging.getLogger("t"))

        assert result.settled == frozenset({1})

    async def test_a_filter_that_raises_settles_nothing(self) -> None:
        broken = MagicMock()
        broken.settle = AsyncMock(side_effect=RuntimeError("bug"))

        result = await settle_connector_off(broken, [_message()], logging.getLogger("t"))

        assert result.settled == frozenset()


class TestNothingIsSettledThatTheHandlerStillNeeds:
    async def test_a_completed_records_new_record_and_update_in_one_batch_both_reach_the_handler(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off", indexingStatus=ProgressStatus.COMPLETED.value)

        result = await _filter(graph).settle([_message(NEW), _message(UPDATE)])

        assert result.settled == frozenset()
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.COMPLETED.value

    async def test_no_event_of_a_record_is_settled_when_another_of_its_events_goes_to_the_handler(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")
        graph.add_record("r2", "conn-off")

        result = await _filter(graph).settle([
            _message(DELETE),
            _message(NEW),
            _message(NEW, record_id="r2"),
        ])

        assert result.settled == frozenset({2})
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.QUEUED.value

    async def test_a_record_another_delivery_moved_on_after_the_read_is_left_to_it(self) -> None:
        graph = FakeConnectorGraph()
        graph.add_connector("conn-off", active=False)
        graph.add_record("r1", "conn-off")
        graph.add_record("r2", "conn-off")
        read = graph.get_nodes_by_field_in

        async def read_then_another_delivery_starts(collection, *args, **kwargs):  # noqa: ANN202
            docs = await read(collection, *args, **kwargs)
            if collection == "records":
                graph.records["r1"]["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
                graph.records["r1"]["processingStartedAt"] = 42
            return docs

        graph.get_nodes_by_field_in = read_then_another_delivery_starts

        result = await _filter(graph).settle([_message(), _message(record_id="r2")])

        assert result.settled == frozenset({1})
        assert graph.records["r1"]["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
        assert graph.records["r1"]["processingStartedAt"] == 42
        assert graph.records["r2"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value


async def test_a_completed_records_queued_copy_is_still_promoted_when_its_update_shares_the_batch() -> None:
    """The handler runs a record's events in order: the newRecord finds the
    record COMPLETED and releases its queued copy, then the update is skipped.
    Marking the record off at read time first made the newRecord miss that."""
    graph = FakeConnectorGraph()
    graph.add_connector("conn-off", active=False)
    graph.add_record(
        "r1", "conn-off",
        indexingStatus=ProgressStatus.COMPLETED.value,
        extractionStatus=ProgressStatus.COMPLETED.value,
        md5Checksum="md5-a", virtualRecordId="vr-r1",
    )
    graph.add_record("copy", "conn-off", md5Checksum="md5-a")
    batch = [_message(NEW), _message(UPDATE)]

    result = await _filter(graph).settle(batch)
    handler, _event_processor = _handler(graph)
    handler._reconcile_pending_duplicates = AsyncMock()
    with patch.object(record_module, "notify_record_indexed", AsyncMock()):
        for i, message in enumerate(batch):
            if i not in result.settled:
                _ = [e async for e in handler.process_event(message.eventType, dict(message.payload))]

    assert result.settled == frozenset()
    assert graph.records["copy"]["indexingStatus"] == ProgressStatus.COMPLETED.value
