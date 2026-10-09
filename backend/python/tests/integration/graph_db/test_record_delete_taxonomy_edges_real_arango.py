"""Every ArangoDB record delete path removes the record's taxonomy edges.

Indexing links a record to departments, categories, languages and topics. The
Drive, Gmail/Outlook, local-FS and generic connector deletes each built their
own edge list without those collections, so the edges outlived the record.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_delete_taxonomy_edges_real_arango.py -m integration
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.taxonomy import RECORD_ENRICHMENT_EDGE_COLLECTIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("record-delete-taxonomy-it")
ARANGO_DB = "record_delete_taxonomy_it"
RECORDS = CollectionNames.RECORDS.value
# One node collection per enrichment edge; departments are global, so a plain
# collection stands in for the edge's target.
TARGETS = {
    CollectionNames.BELONGS_TO_DEPARTMENT.value: CollectionNames.CATEGORIES.value,
    CollectionNames.BELONGS_TO_CATEGORY.value: CollectionNames.CATEGORIES.value,
    CollectionNames.BELONGS_TO_LANGUAGE.value: CollectionNames.LANGUAGES.value,
    CollectionNames.BELONGS_TO_TOPIC.value: CollectionNames.TOPICS.value,
}


@pytest.fixture
async def arango() -> AsyncIterator[tuple]:
    try:
        provider = await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable("arango", exc)
    org_id = f"org-del-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        for collection in (*TARGETS.values(), RECORDS):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id},
            )
        await provider.disconnect()


async def _record_with_taxonomy_edges(provider, org_id: str) -> str:
    record = f"{org_id}-rec"
    await provider.batch_upsert_nodes([{
        "id": record, "orgId": org_id, "recordName": "Doc", "externalRecordId": record,
        "recordType": "FILE", "origin": "CONNECTOR", "connectorId": f"{org_id}-conn",
        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
    }], RECORDS)
    for edge_collection, target_collection in TARGETS.items():
        target = f"{org_id}-{edge_collection}"
        await provider.http_client.execute_aql(
            f"UPSERT {{_key: @key}} INSERT {{_key: @key, name: @key, orgId: @org}} UPDATE {{}} IN {target_collection}",
            {"key": target, "org": org_id},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: CONCAT('{RECORDS}/', @rec), _to: CONCAT('{target_collection}/', @target), "
            f"createdAtTimestamp: 1}} INTO {edge_collection}",
            {"rec": record, "target": target},
        )
    return record


def test_every_seeded_edge_collection_is_an_enrichment_edge() -> None:
    assert set(RECORD_ENRICHMENT_EDGE_COLLECTIONS) == set(TARGETS)


async def _edges_from(provider, record: str) -> dict[str, int]:
    counts = {}
    for edge_collection in TARGETS:
        rows = await provider.http_client.execute_aql(
            f"FOR e IN {edge_collection} FILTER e._from == @from COLLECT WITH COUNT INTO n RETURN n",
            {"from": f"{RECORDS}/{record}"},
        )
        counts[edge_collection] = rows[0]
    return counts


@pytest.mark.parametrize(
    "delete",
    [
        pytest.param(lambda provider, record: provider._delete_record_with_type(record, []), id="connector"),
        pytest.param(lambda provider, record: provider._delete_drive_specific_edges(record), id="drive"),
        pytest.param(lambda provider, record: provider._delete_outlook_edges(record), id="gmail-outlook"),
        pytest.param(lambda provider, record: provider._delete_local_fs_edges(record), id="local-fs"),
    ],
)
async def test_a_record_delete_leaves_no_taxonomy_edge_behind(arango, delete) -> None:
    provider, org_id = arango
    record = await _record_with_taxonomy_edges(provider, org_id)
    assert set((await _edges_from(provider, record)).values()) == {1}

    await delete(provider, record)

    assert set((await _edges_from(provider, record)).values()) == {0}
