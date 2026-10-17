"""A failed named-entity write is retried from the stored extraction, with backoff."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import named_entity_persist_retry as retry

LOG = logging.getLogger("test")


def _graph(rows, record=None):
    graph = MagicMock()
    graph.get_due_named_entity_persist_retries = AsyncMock(return_value=rows)
    graph.clear_named_entity_persist_retry = AsyncMock(return_value=True)
    graph.schedule_named_entity_persist_retry = AsyncMock(return_value=2)
    graph.get_document = AsyncMock(return_value=record)
    return graph


def _row(attempts=1):
    return {"recordId": "r1", "orgId": "o1", "attempts": attempts, "dueAt": 100}


async def _drain(graph, sink):
    return await retry.retry_pending_named_entity_persists(graph_provider=graph, sink=sink, logger=LOG, page_size=10)


@pytest.mark.parametrize(("outcome", "cleared", "written"), [
    ("written", True, 1), ("kept", True, 0), ("disabled", True, 0), (None, True, 0),
    ("failed", False, 0), ("superseded", False, 0),
])
async def test_a_due_retry_is_cleared_unless_it_failed_again(outcome, cleared, written):
    graph = _graph([_row()])
    with patch.object(retry, "_retry_one", AsyncMock(return_value=outcome)):
        assert await _drain(graph, MagicMock()) == written
    assert graph.clear_named_entity_persist_retry.await_count == (1 if cleared else 0)
    if cleared:
        graph.clear_named_entity_persist_retry.assert_awaited_with("r1", 100)


async def test_a_failure_before_the_write_still_grows_the_backoff():
    graph = _graph([_row()])
    with patch.object(retry, "_retry_one", AsyncMock(side_effect=TimeoutError())):
        assert await _drain(graph, MagicMock()) == 0
    graph.schedule_named_entity_persist_retry.assert_awaited_once_with("o1", "r1", "TimeoutError", 1)
    graph.clear_named_entity_persist_retry.assert_not_awaited()


async def test_a_retry_that_keeps_failing_is_dropped():
    graph = _graph([_row(attempts=retry.MAX_PERSIST_ATTEMPTS + 1)])
    one = AsyncMock()
    with patch.object(retry, "_retry_one", one):
        await _drain(graph, MagicMock())
    one.assert_not_awaited()
    graph.clear_named_entity_persist_retry.assert_awaited_once_with("r1", 100)


@pytest.mark.parametrize("record", [None, {"isDeleted": True, "virtualRecordId": "v"}, {"orgId": "o1"}])
async def test_a_record_that_is_gone_has_nothing_to_retry(record):
    sink = MagicMock()
    assert await retry._retry_one(_graph([], record), sink, "r1", "o1", 100) == "gone"


async def test_the_stored_extraction_is_written_again_without_a_model_call():
    record = {"_key": "r1", "id": "r1", "orgId": "o1", "virtualRecordId": "v1"}
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value={"semantic_metadata": {"summary": "s"}})
    sink.reproject_named_entities = AsyncMock(return_value="written")
    context = MagicMock()
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", context):
        assert await retry._retry_one(_graph([], record), sink, "r1", "o1", 100) == "written"
    assert context.call_args.kwargs["record"].semantic_metadata.summary == "s"
    # The write lands only while this retry (due at 100) is still the pending one.
    sink.reproject_named_entities.assert_awaited_once_with(context.return_value, retry_due=100, retry_attempts=0)


async def test_without_a_sink_nothing_is_read():
    graph = _graph([_row()])
    assert await _drain(graph, None) == 0
    graph.get_due_named_entity_persist_retries.assert_not_awaited()


async def test_a_failed_record_read_keeps_the_retry_and_grows_its_backoff():
    """The pass runs while the graph may still be unhealthy: a read error is not a
    record that is gone."""
    graph = _graph([_row()])
    graph.get_document = AsyncMock(side_effect=ConnectionError("graph down"))
    assert await _drain(graph, MagicMock()) == 0
    graph.get_document.assert_awaited_once_with("r1", "records", raise_on_error=True)
    graph.clear_named_entity_persist_retry.assert_not_awaited()
    graph.schedule_named_entity_persist_retry.assert_awaited_once_with("o1", "r1", "ConnectionError", 1)


async def test_a_blob_lookup_that_finds_nothing_is_a_failure_not_an_absence():
    graph = _graph([_row()], {"_key": "r1", "orgId": "o1", "virtualRecordId": "v1"})
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value=None)
    assert await _drain(graph, sink) == 0
    graph.clear_named_entity_persist_retry.assert_not_awaited()
    graph.schedule_named_entity_persist_retry.assert_awaited_once_with("o1", "r1", "StoredContentUnreadableError", 1)


async def test_a_stored_document_that_is_gone_ends_the_retry():
    from app.modules.transformers.blob_storage import StorageDocumentNotFoundError

    graph = _graph([_row()], {"_key": "r1", "orgId": "o1", "virtualRecordId": "v1"})
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(side_effect=StorageDocumentNotFoundError("gone"))
    await _drain(graph, sink)
    graph.clear_named_entity_persist_retry.assert_awaited_once_with("r1", 100)



def _manager(acquired: bool):
    manager = MagicMock()
    manager.try_acquire = AsyncMock(return_value=acquired)
    manager.release = AsyncMock()
    return manager


async def test_a_record_being_indexed_is_left_to_its_own_write():
    graph = _graph([_row()])
    manager = _manager(acquired=False)
    one = AsyncMock()
    with patch.object(retry, "_retry_one", one):
        await retry.retry_pending_named_entity_persists(
            graph_provider=graph, sink=MagicMock(), logger=LOG, page_size=10, concurrency_manager=manager,
        )
    assert manager.try_acquire.await_args.args[0] == "record:r1"
    one.assert_not_awaited()
    graph.clear_named_entity_persist_retry.assert_not_awaited()
    graph.schedule_named_entity_persist_retry.assert_not_awaited()


@pytest.mark.parametrize("result", [AsyncMock(return_value="written"), AsyncMock(side_effect=TimeoutError())])
async def test_the_record_lease_is_released_whatever_the_retry_does(result):
    graph = _graph([_row()])
    manager = _manager(acquired=True)
    with patch.object(retry, "_retry_one", result):
        await retry.retry_pending_named_entity_persists(
            graph_provider=graph, sink=MagicMock(), logger=LOG, page_size=10, concurrency_manager=manager,
        )
    pool, owner = manager.release.await_args.args
    assert pool == "record:r1" and owner == manager.try_acquire.await_args.args[1]
