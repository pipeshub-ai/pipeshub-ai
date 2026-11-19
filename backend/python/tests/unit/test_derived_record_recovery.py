"""Recovery of a record that an installed ownership says belongs to another connector (a derived record).

The built-in ownership has no such records, so these use a test ownership that does: a record stored under the id
``DERIVED_HOLDER`` is owned by ``OWNER``, which is the connector that has a lane and that is turned off and on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import OriginTypes, ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.extension import DefaultRecordOwnership, IndexingExtension
from app.utils.user_errors import CONNECTOR_OFF
from tests.unit.test_indexing_main import (
    _document_lookup,
    _make_container,
    _make_graph_provider,
)
from tests.unit.test_stranded_sweep_parked_while_off import (
    ON,
    FakeGraph,
    Producer,
    _parked,
    _sweep,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

DERIVED_HOLDER = "derived-holder"


class DerivedOwnership(DefaultRecordOwnership):
    """Records stored under DERIVED_HOLDER belong to ON; ``derived_pages`` finds the ones parked for it."""

    def __init__(self, graph: FakeGraph | None = None) -> None:
        self.graph = graph
        self.asked: list[tuple[str, str, dict]] = []

    def owner_connector_id(self, record: object) -> str | None:
        own = super().owner_connector_id(record)
        return ON if own == DERIVED_HOLDER else own

    def is_derived(self, record: object) -> bool:
        return super().owner_connector_id(record) == DERIVED_HOLDER

    async def derived_pages(
        self, graph: object, connector_id: str, org_id: str, filters: Mapping[str, Any] | None, *, page_size: int
    ) -> AsyncIterator[list]:
        self.asked.append((connector_id, org_id, dict(filters or {})))
        if connector_id != ON:
            return
        rows = [
            dict(doc) for doc in self.graph.records.values()
            if doc.get("connectorId") == DERIVED_HOLDER and all(doc.get(k) == v for k, v in (filters or {}).items())
        ]
        if rows:
            yield rows


@pytest.mark.asyncio
async def test_a_derived_record_parked_while_its_owner_was_off_is_found_by_the_sweep() -> None:
    graph, producer = FakeGraph(), Producer()
    graph.records["d1"] = _parked("d1", connector_id=DERIVED_HOLDER, origin=OriginTypes.UPLOAD.value)
    ownership = DerivedOwnership(graph)
    extension.install(IndexingExtension(ownership=ownership))

    assert await _sweep(graph, producer) == 1

    assert producer.sent == ["d1"]
    assert (ON, "org-1", {"indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value, "reason": CONNECTOR_OFF}) in ownership.asked


@pytest.mark.asyncio
async def test_the_default_ownership_adds_no_walk_to_the_parked_records() -> None:
    graph, producer = FakeGraph(), Producer()
    graph.records["r1"] = _parked("r1")

    assert await _sweep(graph, producer) == 1
    assert producer.sent == ["r1"]


@pytest.mark.asyncio
async def test_stale_recovery_republishes_a_derived_record_on_its_owners_lane() -> None:
    from app.indexing_main import recover_in_progress_records

    extension.install(IndexingExtension(ownership=DerivedOwnership()))
    container = _make_container()
    gp = _make_graph_provider()
    gp.get_document = AsyncMock(side_effect=_document_lookup(gp, connector={"_key": ON, "isActive": True}))
    gp.get_nodes_by_filters = AsyncMock(return_value=[{
        "_key": "d1", "recordName": "a.pdf", "version": 0, "orgId": "org-1", "connectorId": DERIVED_HOLDER,
        "origin": OriginTypes.UPLOAD.value, "virtualRecordId": "V_d1",
    }])

    await recover_in_progress_records(container, gp)

    payload = container.kafka_consumers[0][2].send_event.await_args.kwargs["payload"]
    assert payload["connectorId"] == ON
    assert payload["virtualRecordId"] == "V_d1"
