"""Classify on first use: a search asks indexing to classify the records it returned."""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.pipeline import first_use

_LOG = logging.getLogger("test_first_use")


def _doc(record_id: str, **overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "_key": record_id,
        "virtualRecordId": f"vr-{record_id}",
        "connectorId": "kb-1",
        "connectorName": "KB",
        "mimeType": "text/plain",
        "indexingStatus": "COMPLETED",
        "extractionStatus": "NOT_STARTED",
        "contentRev": "rev-1",
    }
    doc.update(overrides)
    return doc


@pytest.fixture(autouse=True)
def _fresh() -> None:
    first_use._asked.clear()
    first_use._producer = None


@pytest.fixture
def producer() -> AsyncMock:
    sender = AsyncMock()
    sender.send_message = AsyncMock(return_value=True)
    with patch.object(first_use, "_get_producer", AsyncMock(return_value=sender)):
        yield sender


def _on(enabled: bool):  # noqa: ANN202
    return patch.object(first_use, "is_classify_on_first_use_enabled", AsyncMock(return_value=enabled))


def test_only_indexed_records_waiting_for_classification_are_picked() -> None:
    docs = [
        _doc("a"),
        _doc("b", extractionStatus="COMPLETED"),
        _doc("c", indexingStatus="IN_PROGRESS"),
        _doc("d", contentRev=None),
        "not a record",
    ]
    assert [d["_key"] for d in first_use.unclassified(docs)] == ["a"]


def test_the_event_redrives_classify_for_the_revision_that_was_searched() -> None:
    event = first_use.first_use_event(_doc("a"), "org-1")
    assert event["eventType"] == "reindexRecord"
    payload = event["payload"]
    assert payload["recordId"] == "a"
    assert payload["stages"] == ["classify"]
    assert payload["stageRev"] == "rev-1"
    assert payload["firstUse"] is True
    assert (payload["orgId"], payload["connectorId"]) == ("org-1", "kb-1")


async def test_a_search_asks_once_per_record_and_revision(producer: AsyncMock) -> None:
    results = {"records": [_doc("a"), _doc("b", extractionStatus="COMPLETED")]}
    with _on(True):
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 1
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 0
        moved_on = {"records": [_doc("a", contentRev="rev-2")]}
        assert await first_use.classify_on_first_use(moved_on, "org-1", object(), _LOG) == 1
    assert producer.send_message.await_count == 2
    topic, event = producer.send_message.await_args.args
    assert topic == "record-events" and event["payload"]["stageRev"] == "rev-2"


async def test_nothing_is_asked_while_the_setting_is_off(producer: AsyncMock) -> None:
    with _on(False):
        assert await first_use.classify_on_first_use({"records": [_doc("a")]}, "org-1", object(), _LOG) == 0
    producer.send_message.assert_not_awaited()


async def test_a_refused_send_is_asked_again_by_the_next_search(producer: AsyncMock) -> None:
    producer.send_message = AsyncMock(side_effect=[False, True])
    results = {"records": [_doc("a")]}
    with _on(True):
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 0
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 1


async def test_a_broken_broker_never_reaches_the_search_and_marks_nothing(producer: AsyncMock) -> None:
    results = {"records": [_doc("a")]}
    with _on(True):
        with patch.object(first_use, "_get_producer", AsyncMock(side_effect=ConnectionError("down"))):
            assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 0
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 1


async def test_a_send_that_raises_leaves_the_rest_to_the_next_search(producer: AsyncMock) -> None:
    producer.send_message = AsyncMock(side_effect=[True, ConnectionError("down"), True, True])
    results = {"records": [_doc("a"), _doc("b"), _doc("c")]}
    with _on(True):
        await first_use.classify_on_first_use(results, "org-1", object(), _LOG)
        # "a" went out and is not asked again; "b" and "c" are.
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 2
    assert [c.args[1]["payload"]["recordId"] for c in producer.send_message.await_args_list] == ["a", "b", "b", "c"]


async def test_two_searches_at_once_ask_once_per_record(producer: AsyncMock) -> None:
    async def yielding_send(*_args: object, **_kwargs: object) -> bool:
        await asyncio.sleep(0)
        return True

    producer.send_message = AsyncMock(side_effect=yielding_send)
    results = {"records": [_doc("a"), _doc("b"), _doc("c")]}
    with _on(True):
        counts = await asyncio.gather(
            first_use.classify_on_first_use(results, "org-1", object(), _LOG),
            first_use.classify_on_first_use(results, "org-1", object(), _LOG),
        )
    assert sorted(counts) == [0, 3]
    asked = [c.args[1]["payload"]["recordId"] for c in producer.send_message.await_args_list]
    assert sorted(asked) == ["a", "b", "c"]


async def test_a_producer_that_cannot_start_releases_every_reservation(producer: AsyncMock) -> None:
    results = {"records": [_doc("a"), _doc("b")]}
    with _on(True):
        with patch.object(first_use, "_get_producer", AsyncMock(side_effect=ConnectionError("down"))):
            assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 0
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 2


async def test_the_cap_applies_after_records_already_asked_for(producer: AsyncMock) -> None:
    results = {"records": [_doc(f"r{i}") for i in range(first_use._MAX_PER_SEARCH + 5)]}
    with _on(True):
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == first_use._MAX_PER_SEARCH
        assert await first_use.classify_on_first_use(results, "org-1", object(), _LOG) == 5
