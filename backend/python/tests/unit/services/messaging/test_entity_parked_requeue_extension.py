"""Re-queuing the records a connector parked while it was off consults the installed ownership: pages it adds are walked too, a
record it cannot read is skipped without stopping the rest, and the default adds nothing."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import OriginTypes, ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import DefaultRecordOwnership, IndexingExtension
from app.utils.user_errors import CONNECTOR_OFF
from tests.unit.services.messaging.test_entity_resume_requeues_parked import (
    CONNECTOR_ID,
    FakeGraph,
    FakeProducer,
    _record,
    _service,
    _set_active,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _parked(key: str, **fields: object) -> dict:
    return _record(key, ProgressStatus.AUTO_INDEX_OFF.value, reason=CONNECTOR_OFF, **fields)


async def _requeue(graph: FakeGraph, producer: FakeProducer) -> int:
    _set_active(graph, True)
    return await _service(graph, producer)._requeue_records_parked_while_off(CONNECTOR_ID)


async def test_with_the_default_ownership_only_the_connectors_own_parked_records_are_requeued() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    graph.records()["own"] = _parked("own")
    graph.records()["upload"] = _parked("upload", origin=OriginTypes.UPLOAD.value)

    assert await _requeue(graph, producer) == 1

    assert producer.sent_keys() == {"own"}


class Derived(DefaultRecordOwnership):
    def __init__(self, extra: list[dict], unreadable: frozenset[str] = frozenset()) -> None:
        self.extra, self.unreadable = extra, unreadable
        self.asked: list[tuple] = []

    def is_derived(self, record: object) -> bool:
        return isinstance(record, dict) and bool(record.get("derived"))

    async def prime_readable(self, page: list, graph: object, skips: object, logger: object) -> list:
        return [r for r in page if r["_key"] not in self.unreadable]

    async def derived_pages(
        self, graph: object, connector_id: str, org_id: str, filters: object, *, page_size: int
    ) -> AsyncIterator[list]:
        self.asked.append((connector_id, filters, page_size))
        yield self.extra


async def test_pages_the_ownership_adds_are_requeued_with_the_regular_ones() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    graph.records()["own"] = _parked("own")
    extra = _parked("derived-1", derived=True, origin=OriginTypes.UPLOAD.value, connectorId="hidden")
    graph.records()["derived-1"] = extra  # under a connector of its own: only the ownership's pages reach it
    own = Derived([dict(extra)])
    extension.install(IndexingExtension(ownership=own))

    assert await _requeue(graph, producer) == 2

    assert producer.sent_keys() == {"own", "derived-1"}
    assert graph.records()["derived-1"]["indexingStatus"] == ProgressStatus.QUEUED.value
    connector_id, filters, _page_size = own.asked[0]
    assert connector_id == CONNECTOR_ID
    assert filters == {"indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value, "reason": CONNECTOR_OFF}


async def test_a_record_the_ownership_cannot_read_is_skipped_and_the_rest_of_the_page_still_goes() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    graph.records()["a"] = _parked("a")
    graph.records()["b"] = _parked("b")
    extension.install(IndexingExtension(ownership=Derived([], unreadable=frozenset({"a"}))))

    assert await _requeue(graph, producer) == 1

    assert producer.sent_keys() == {"b"}
    assert graph.records()["a"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value


async def test_a_derived_record_that_is_not_live_is_never_requeued() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    gone = _parked("gone", derived=True, origin=OriginTypes.UPLOAD.value, isDeleted=True, connectorId="hidden")
    graph.records()["gone"] = gone
    extension.install(IndexingExtension(ownership=Derived([dict(gone)])))

    assert await _requeue(graph, producer) == 0
    assert producer.sent_keys() == set()
