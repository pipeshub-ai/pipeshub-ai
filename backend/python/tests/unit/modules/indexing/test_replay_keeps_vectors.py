"""A reindex that deletes a virtual record's embeddings keeps every point while a record still references it.

``bulk_delete_embeddings`` rewrites the membership of a virtual record's points in place, and never removes them,
while a record still references it. The record handler no longer calls it ahead of an update or reindex; this pins
the behaviour of the call for the callers that remain (connector and record deletes).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.run import IndexingPipeline
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from tests.support.vector_db import make_collection_registry

pytestmark = pytest.mark.usefixtures("bound_org")


async def test_a_replayed_reindex_keeps_every_point_of_a_referenced_vrid() -> None:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["r1"])
    graph.get_document = AsyncMock(return_value={"connectorId": "conn-1", "recordGroupId": "rg-1"})
    graph.get_edges_from_node = AsyncMock(return_value=[{"_to": f"{CollectionNames.RECORD_GROUPS.value}/rg-1"}])
    vector_store = AsyncMock()
    vector_store.filter_collection = AsyncMock(return_value=MagicMock())
    pipeline = IndexingPipeline(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph_provider=graph,
        collection_registry=make_collection_registry(),
        vector_db_service=vector_store,
    )

    await pipeline.bulk_delete_embeddings(["vr-1"], org_id="org-1")

    vector_store.delete_points.assert_not_awaited()
    # The points are re-stamped with the records that still own them, not removed.
    assert vector_store.set_payload.await_args.args[1][CONNECTOR_IDS_FIELD] == ["conn-1"]
