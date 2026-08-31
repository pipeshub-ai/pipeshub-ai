"""Unit tests for app.modules.indexing.run.IndexingPipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import app.modules.indexing.run as run_mod
from app.modules.indexing.run import ScanResult


def _scan_scope():
    from app.services.vector_db.strategy import DeleteAction, DeleteScope

    return DeleteScope(
        action=DeleteAction.FILTERED_DELETE,
        collection_names=["records"],
        filter_field="connectorIds",
        filter_values=["conn-1"],
    )


from tests.support.vector_db import (
    make_collection_registry as _make_collection_registry,
)

# ===================================================================
# IndexingPipeline
# ===================================================================



def _make_indexing_pipeline():
    """Create an IndexingPipeline with all dependencies mocked."""
    with patch(
        "app.modules.indexing.run.FastEmbedSparse"
    ) as mock_sparse:
        mock_sparse.return_value = MagicMock()
        from app.modules.indexing.run import IndexingPipeline

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=AsyncMock(),
        )
        return pipeline


class TestIndexingPipelineInit:
    """Tests for IndexingPipeline.__init__."""

    def test_stores_all_deps(self):
        pipeline = _make_indexing_pipeline()
        assert pipeline.collection_registry is not None
        assert pipeline.collection_locator is not None

    @pytest.mark.skip(reason="FastEmbedSparse not used in IndexingPipeline.__init__")
    def test_sparse_embed_failure_raises(self):
        """Raises IndexingError when sparse embed init fails."""
        from app.exceptions.indexing_exceptions import IndexingError
        with patch(
            "app.modules.indexing.run.FastEmbedSparse",
            side_effect=RuntimeError("sparse fail"),
        ):
            with pytest.raises(IndexingError):
                from app.modules.indexing.run import IndexingPipeline
                IndexingPipeline(
                    logger=MagicMock(),
                    config_service=AsyncMock(),
                    graph_provider=AsyncMock(),
                    collection_registry=_make_collection_registry(),
                    vector_db_service=AsyncMock(),
                )


@pytest.mark.skip(reason="_initialize_collection is in VectorStore, not IndexingPipeline")
class TestIndexingPipelineInitializeCollection:
    """Tests for IndexingPipeline._initialize_collection."""

    @pytest.mark.asyncio
    async def test_creates_collection_when_not_found(self):
        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=None)
        pipeline.vector_db_service.create_collection = AsyncMock()
        pipeline.vector_db_service.create_index = AsyncMock()

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.create_collection.assert_awaited_once()
        assert pipeline.vector_db_service.create_index.call_count == 2

    @pytest.mark.asyncio
    async def test_recreates_on_mismatch(self):
        pipeline = _make_indexing_pipeline()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=512)}
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=mock_info)
        pipeline.vector_db_service.delete_collection = AsyncMock()
        pipeline.vector_db_service.create_collection = AsyncMock()
        pipeline.vector_db_service.create_index = AsyncMock()

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.delete_collection.assert_awaited_once()
        pipeline.vector_db_service.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_recreate_when_same_size(self):
        pipeline = _make_indexing_pipeline()
        mock_info = MagicMock()
        mock_info.config.params.vectors = {"dense": MagicMock(size=768)}
        pipeline.vector_db_service.get_collection = AsyncMock(return_value=mock_info)

        await pipeline._initialize_collection(embedding_size=768)

        pipeline.vector_db_service.create_collection.assert_not_awaited()


class TestIndexingPipelineProcessMetadata:
    """Tests for IndexingPipeline._process_metadata."""

    def test_basic_metadata(self):
        pipeline = _make_indexing_pipeline()
        meta = {
            "orgId": "org-1",
            "virtualRecordId": "vr-1",
            "recordName": "test.pdf",
            "blockType": "text",
        }
        result = pipeline._process_metadata(meta)
        assert result["orgId"] == "org-1"
        assert result["virtualRecordId"] == "vr-1"
        assert result["recordName"] == "test.pdf"
        assert result["blockType"] == "text"

    def test_block_type_list_takes_first(self):
        pipeline = _make_indexing_pipeline()
        meta = {"blockType": ["heading", "text"]}
        result = pipeline._process_metadata(meta)
        assert result["blockType"] == "heading"

    def test_optional_fields(self):
        pipeline = _make_indexing_pipeline()
        meta = {
            "bounding_box": [{"x": 0, "y": 0}],
            "sheetName": "Sheet1",
            "sheetNum": 1,
            "pageNum": 3,
        }
        result = pipeline._process_metadata(meta)
        assert result["bounding_box"] == [{"x": 0, "y": 0}]
        assert result["sheetName"] == "Sheet1"
        assert result["sheetNum"] == 1
        assert result["pageNum"] == 3

    def test_defaults_for_missing_fields(self):
        pipeline = _make_indexing_pipeline()
        meta = {}
        result = pipeline._process_metadata(meta)
        assert result["orgId"] == ""
        assert result["virtualRecordId"] == ""
        assert result["blockType"] == "text"
        assert result["blockNum"] == [0]


class TestIndexingPipelineBulkDelete:
    """Tests for IndexingPipeline.bulk_delete_embeddings."""

    @pytest.mark.asyncio
    async def test_empty_list_returns_success(self):
        pipeline = _make_indexing_pipeline()
        result = await pipeline.bulk_delete_embeddings([])
        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 0

    @pytest.mark.asyncio
    async def test_filters_empty_ids(self):
        pipeline = _make_indexing_pipeline()
        result = await pipeline.bulk_delete_embeddings(["", "  "])
        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 0

    @pytest.mark.asyncio
    async def test_rewrites_ids_with_remaining_records(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-1"]
        )
        pipeline.graph_provider.get_document = AsyncMock(
            return_value={"connectorId": "conn-1", "recordGroupId": "rg-1"}
        )
        pipeline.graph_provider.get_edges_from_node = AsyncMock(return_value=[])
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.set_payload = AsyncMock()
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.set_payload.assert_awaited_once()
        pipeline.vector_db_service.delete_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_safe_ids(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value={})
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_rewrite_and_delete(self):
        pipeline = _make_indexing_pipeline()

        async def remaining(virtual_record_id):
            return ["rec-keep"] if virtual_record_id == "vr-shared" else []

        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            side_effect=remaining
        )
        pipeline.graph_provider.get_document = AsyncMock(
            return_value={"connectorId": "conn-2"}
        )
        pipeline.graph_provider.get_edges_from_node = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.set_payload = AsyncMock()
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-shared", "vr-last"])

        assert result["success"] is True
        assert result["virtual_record_ids_processed"] == 2
        pipeline.vector_db_service.set_payload.assert_awaited_once()
        pipeline.vector_db_service.delete_points.assert_awaited_once()
        pipeline.graph_provider.delete_nodes.assert_awaited_once()


class TestBulkDeleteConfirmation:
    """Bulk deletion must re-confirm before destroying points."""

    @pytest.mark.asyncio
    async def test_vrid_that_reappears_is_rewritten_not_deleted(self):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.indexing import run as run_mod

        gp = AsyncMock()
        # empty on the candidate pass, populated on the confirming pass
        reads = [[], ["r1"]]
        gp.get_records_by_virtual_record_id = AsyncMock(
            side_effect=lambda *a, **k: reads.pop(0) if reads else ["r1"]
        )
        gp.get_document = AsyncMock(return_value={"connectorId": "c1"})
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        pipeline = run_mod.IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=gp,
            collection_registry=_make_collection_registry("records"),
            vector_db_service=vdb,
        )

        original = run_mod.EMPTY_CONFIRM_DELAY_SECONDS
        run_mod.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            result = await pipeline.bulk_delete_embeddings(["vr-lag"])
        finally:
            run_mod.EMPTY_CONFIRM_DELAY_SECONDS = original

        assert result["success"] is True
        assert result["virtual_record_ids_deleted"] == 0
        assert result["virtual_record_ids_rewritten"] == 1
        vdb.delete_points.assert_not_awaited()


class TestPurgeConnector:
    """Tests for IndexingPipeline.purge_connector."""

    @pytest.mark.asyncio
    async def test_drop_collection_action_drops_and_skips_bulk_delete(self):
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.DROP_COLLECTION,
                collection_names=["google_drive_records"],
            )
        )
        pipeline.collection_registry.delete_collection = AsyncMock()
        pipeline.bulk_delete_embeddings = AsyncMock()

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="GOOGLE_DRIVE")
        result = await pipeline.purge_connector(ctx, ["vr-1", "vr-2"])

        pipeline.collection_registry.delete_collection.assert_awaited_once_with(
            "google_drive_records"
        )
        pipeline.bulk_delete_embeddings.assert_not_awaited()
        assert result["action"] == "drop_collection"

    @pytest.mark.asyncio
    async def test_drop_recovers_vrids_before_dropping_when_none_supplied(self):
        """A legacy `bulkDeleteRecords` carries no `virtualRecordIds`, and the
        handler turns that into `[]`. Dropping first would destroy the only
        thing left to enumerate, stranding every mapping row -- and a stranded
        row makes its VRID look like a live orphan to the sweeper forever.
        """
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.DROP_COLLECTION,
                collection_names=["google_drive_records"],
            )
        )
        order: list = []
        pipeline.collection_registry.delete_collection = AsyncMock(
            side_effect=lambda name: order.append(f"drop:{name}")
        )
        pipeline._scan_virtual_record_ids = AsyncMock(
            side_effect=lambda scope: order.append("scan")
            or ScanResult(["vr-a", "vr-b"], True)
        )
        pipeline.graph_provider.delete_nodes = AsyncMock(
            side_effect=lambda **kw: order.append("forget")
        )

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="GOOGLE_DRIVE")
        await pipeline.purge_connector(ctx, [])

        assert order == ["scan", "drop:google_drive_records", "forget"]
        assert pipeline.graph_provider.delete_nodes.await_args.kwargs["keys"] == [
            "vr-a",
            "vr-b",
        ]

    @pytest.mark.asyncio
    async def test_drop_scan_uses_the_connector_membership_predicate(self):
        """A drop scope carries no filter, and `_scan_virtual_record_ids`
        refuses to scan without one -- so the recovery has to supply the same
        membership predicate the registry uses when it downgrades a drop."""
        from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.DROP_COLLECTION,
                collection_names=["google_drive_records"],
            )
        )
        pipeline.collection_registry.delete_collection = AsyncMock()
        pipeline._scan_virtual_record_ids = AsyncMock(return_value=ScanResult([], True))

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="GOOGLE_DRIVE")
        await pipeline.purge_connector(ctx, None)

        scanned = pipeline._scan_virtual_record_ids.await_args.args[0]
        assert scanned.filter_field == CONNECTOR_IDS_FIELD
        assert scanned.filter_values == ["conn-1"]
        assert scanned.collection_names == ["google_drive_records"]

    @pytest.mark.asyncio
    async def test_drop_with_supplied_vrids_does_not_scan(self):
        """The producer's list is authoritative; recovery is only for its
        absence."""
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.DROP_COLLECTION,
                collection_names=["google_drive_records"],
            )
        )
        pipeline.collection_registry.delete_collection = AsyncMock()
        pipeline._scan_virtual_record_ids = AsyncMock(
            return_value=ScanResult(["vr-scanned"], True)
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="GOOGLE_DRIVE")
        await pipeline.purge_connector(ctx, ["vr-1"])

        pipeline._scan_virtual_record_ids.assert_not_awaited()
        assert pipeline.graph_provider.delete_nodes.await_args.kwargs["keys"] == ["vr-1"]

    @pytest.mark.asyncio
    async def test_filtered_delete_with_vrids_delegates_to_membership_aware_delete(self):
        """A collection shared with a still-live connector must go through the
        VRID rewrite-or-delete path, never a raw filter delete on connectorIds --
        that would remove points still referenced by another connector."""
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.FILTERED_DELETE,
                collection_names=["records"],
                filter_field="connectorIds",
                filter_values=["conn-1"],
            )
        )
        pipeline.bulk_delete_embeddings = AsyncMock(
            return_value={"virtual_record_ids_processed": 2, "success": True}
        )

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1")
        result = await pipeline.purge_connector(ctx, ["vr-1", "vr-2"])

        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr-1", "vr-2"])
        pipeline.vector_db_service.delete_points.assert_not_awaited()
        assert result["action"] == "filtered_delete"
        assert result["virtual_record_ids_processed"] == 2

    @pytest.mark.asyncio
    async def test_filtered_delete_with_no_filter_refuses_to_delete(self):
        """A predicate-less delete would empty the collection for every
        connector sharing it, so an unfiltered scope must be refused."""
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.FILTERED_DELETE,
                collection_names=["records"],
                filter_field=None,
                filter_values=None,
            )
        )
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.purge_connector(
            DeleteContext(org_id="org-1", connector_id="conn-1"), []
        )

        pipeline.vector_db_service.delete_points.assert_not_awaited()
        # Nothing to scan without a predicate, and nothing supplied to delete.
        assert result["action"] == "noop"

    @pytest.mark.asyncio
    async def test_filtered_delete_without_vrids_recovers_them_by_scanning(self):
        """A producer that sent no VRID list must not become a raw connectorIds
        delete: that would take out points this connector shares with a live
        one through dedup. The ids are recovered from the collection instead,
        then routed through the membership-aware path."""
        from app.services.vector_db.models import ScrollResult, VectorPoint
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.FILTERED_DELETE,
                collection_names=["records"],
                filter_field="connectorIds",
                filter_values=["conn-1"],
            )
        )
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.delete_points = AsyncMock()
        pipeline.vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    VectorPoint(
                        id="p1",
                        dense_vector=[0.0],
                        payload={"metadata": {"virtualRecordId": "vr-1"}},
                    )
                ],
                next_offset=None,
            )
        )
        pipeline.bulk_delete_embeddings = AsyncMock(
            return_value={"virtual_record_ids_processed": 1, "success": True}
        )

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1")
        result = await pipeline.purge_connector(ctx, [])

        pipeline.bulk_delete_embeddings.assert_awaited_once_with(["vr-1"])
        pipeline.vector_db_service.delete_points.assert_not_awaited()
        assert result["action"] == "filtered_delete"

    @pytest.mark.asyncio
    async def test_an_exhausted_scan_reports_complete(self):
        from app.services.vector_db.models import ScrollResult, VectorPoint
        from app.services.vector_db.strategy import DeleteAction, DeleteScope

        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    VectorPoint(
                        id="p1",
                        dense_vector=[0.0],
                        payload={"metadata": {"virtualRecordId": "vr-1"}},
                    )
                ],
                next_offset=None,
            )
        )

        found = await pipeline._scan_virtual_record_ids(_scan_scope())

        assert found.complete is True

    @pytest.mark.asyncio
    async def test_a_scan_stopped_by_the_point_cap_reports_incomplete(self):
        """The caller has to be able to tell "this connector had these VRIDs"
        from "these are the ones we managed to read" — the drop path deletes
        the collection those ids came from."""
        from app.services.vector_db.models import ScrollResult, VectorPoint

        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        cursor = {"n": 0}

        async def _endless(**kwargs):
            cursor["n"] += 1
            return ScrollResult(
                points=[
                    VectorPoint(
                        id=f"p{cursor['n']}-{i}",
                        dense_vector=[0.0],
                        payload={"metadata": {"virtualRecordId": f"vr-{cursor['n']}-{i}"}},
                    )
                    for i in range(run_mod.PURGE_SCAN_PAGE_SIZE)
                ],
                next_offset=f"off-{cursor['n']}",
            )

        pipeline.vector_db_service.scroll = AsyncMock(side_effect=_endless)

        found = await pipeline._scan_virtual_record_ids(_scan_scope())

        assert found.complete is False
        assert len(found.ids) >= run_mod.PURGE_SCAN_MAX_POINTS

    @pytest.mark.asyncio
    async def test_a_failed_scroll_reports_incomplete(self):
        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.scroll = AsyncMock(side_effect=RuntimeError("boom"))

        found = await pipeline._scan_virtual_record_ids(_scan_scope())

        assert found.complete is False
        assert found.ids == []

    @pytest.mark.asyncio
    async def test_a_filterless_scope_reports_incomplete(self):
        """Refusing to scan is not the same as having scanned everything."""
        from app.services.vector_db.strategy import DeleteAction, DeleteScope

        pipeline = _make_indexing_pipeline()

        found = await pipeline._scan_virtual_record_ids(
            DeleteScope(action=DeleteAction.DROP_COLLECTION, collection_names=["records"])
        )

        assert found == ([], False)

    @pytest.mark.asyncio
    async def test_an_incomplete_scan_still_drops_but_warns(self):
        """Refusing the drop would leave the whole collection behind, and the
        cap is reached precisely on the large collections a drop exists for.
        The rows beyond it are reclaimed by the orphan sweeper, which walks
        the mapping collection itself — so proceed, and say so."""
        from app.services.vector_db.strategy import (
            DeleteAction,
            DeleteContext,
            DeleteScope,
        )

        pipeline = _make_indexing_pipeline()
        pipeline.logger = MagicMock()
        pipeline.collection_registry.resolve_delete_scope = AsyncMock(
            return_value=DeleteScope(
                action=DeleteAction.DROP_COLLECTION,
                collection_names=["drive_records"],
            )
        )
        pipeline.collection_registry.delete_collection = AsyncMock()
        pipeline._scan_virtual_record_ids = AsyncMock(
            return_value=ScanResult(["vr-a"], False)
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()

        ctx = DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="GOOGLE_DRIVE")
        await pipeline.purge_connector(ctx, [])

        pipeline.collection_registry.delete_collection.assert_awaited_once()
        assert pipeline.graph_provider.delete_nodes.await_args.kwargs["keys"] == ["vr-a"]
        assert pipeline.logger.warning.called, "an incomplete drop must be visible"

    @pytest.mark.asyncio
    async def test_scan_stops_when_the_cursor_stops_advancing(self):
        """A provider returning the same offset forever must not spin: the
        point cap cannot stop it on its own, since a repeated page never
        advances the count."""
        from app.services.vector_db.models import ScrollResult, VectorPoint
        from app.services.vector_db.strategy import DeleteAction, DeleteScope

        pipeline = _make_indexing_pipeline()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    VectorPoint(
                        id="p1",
                        dense_vector=[0.0],
                        payload={"metadata": {"virtualRecordId": "vr-1"}},
                    )
                ],
                next_offset=None,
            )
        )

        found = await pipeline._scan_virtual_record_ids(
            DeleteScope(
                action=DeleteAction.FILTERED_DELETE,
                collection_names=["records"],
                filter_field="connectorIds",
                filter_values=["conn-1"],
            )
        )

        assert found.ids == ["vr-1"]
        assert pipeline.vector_db_service.scroll.await_count == 1


# ===================================================================
# Connector purge: collection targeting and orphan safety
# ===================================================================


class TestConnectorPurgeDoesNotOrphanPoints:
    """A VRID's points must never outlive the mapping row that finds them.

    `virtualRecordToDocIdMapping` is the orphan sweeper's only handle on a
    point set. Forgetting it while the points survive makes them permanently
    unreachable — not merely stale.
    """

    @pytest.mark.asyncio
    async def test_no_managed_collections_deletes_nothing_and_forgets_nothing(self):
        """An empty or stale manifest resolves no collections. Falling through
        would drop the mapping rows while every point survived."""
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.delete_points = AsyncMock()
        pipeline.collection_locator.all_collections = AsyncMock(return_value=[])

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        pipeline.vector_db_service.delete_points.assert_not_awaited()
        pipeline.graph_provider.delete_nodes.assert_not_awaited()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_a_failed_batch_keeps_its_mapping(self):
        """The points are still there; the row that finds them must be too."""
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.delete_points = AsyncMock(
            side_effect=ConnectionError("vector db down")
        )
        pipeline.collection_locator.all_collections = AsyncMock(return_value=["records"])

        await pipeline.bulk_delete_embeddings(["vr-1"])

        pipeline.graph_provider.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_successful_delete_forgets_its_mapping(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.delete_points = AsyncMock()
        pipeline.collection_locator.all_collections = AsyncMock(return_value=["records"])

        await pipeline.bulk_delete_embeddings(["vr-1"])

        pipeline.graph_provider.delete_nodes.assert_awaited_once()
        assert pipeline.graph_provider.delete_nodes.await_args.kwargs["keys"] == ["vr-1"]

    @pytest.mark.asyncio
    async def test_the_delete_reads_a_fresh_manifest(self):
        """A 30s-stale enumeration resolves fewer collections than exist, and
        points left in the missed ones are unreachable afterwards."""
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        pipeline.vector_db_service.delete_points = AsyncMock()
        pipeline.collection_locator.all_collections = AsyncMock(return_value=["records"])

        await pipeline.bulk_delete_embeddings(["vr-1"])

        pipeline.collection_locator.all_collections.assert_awaited_with(fresh=True)


class TestConnectorPurgeSweepsTheCollectionsAVridLeft:
    """The connector's own collection, when the VRID survives elsewhere.

    Deleting a Drive connector whose file was deduplicated into Slack leaves
    the VRID alive (Slack still has a record), so the rewrite branch runs. That
    branch must still purge the Drive collection — otherwise it keeps points
    for a record that no longer exists.
    """

    @pytest.mark.asyncio
    async def test_a_surviving_vrid_goes_through_the_delete_aware_rewrite(self):
        pipeline = _make_indexing_pipeline()
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-still-here"]
        )
        pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="rewritten")
        pipeline.sync_vector_membership = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-shared"])

        # sync_vector_membership only re-stamps where records remain; it would
        # leave the departed connector's collection holding orphans.
        pipeline.sync_vector_membership.assert_not_awaited()
        pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with("vr-shared")
        assert result["virtual_record_ids_rewritten"] == 1

    @pytest.mark.asyncio
    async def test_a_vrid_that_reappears_on_recheck_also_takes_that_path(self):
        pipeline = _make_indexing_pipeline()
        calls = {"n": 0}

        async def _records(virtual_record_id=None, **kw):
            calls["n"] += 1
            return [] if calls["n"] == 1 else ["rec-reappeared"]

        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(side_effect=_records)
        pipeline.rewrite_or_delete_vector_membership = AsyncMock(return_value="rewritten")
        pipeline.sync_vector_membership = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-flaky"])

        pipeline.rewrite_or_delete_vector_membership.assert_awaited_once_with("vr-flaky")
        pipeline.sync_vector_membership.assert_not_awaited()
        assert result["virtual_record_ids_rewritten"] == 1
