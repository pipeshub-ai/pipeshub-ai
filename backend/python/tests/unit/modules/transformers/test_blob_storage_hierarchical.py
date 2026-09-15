"""Tests for BlobStorage hierarchical path integration and virtual record mapping.

Covers gaps not addressed by test_blob_storage_override.py:
- apply() when graph_provider is None (flat fallback via storage_path)
- store_virtual_record_mapping behavior (graph DB upsert)
- get_actual_content_path (lookup + strip org prefix)
- _get_current_document_path (Node.js endpoint call)
- save_reconciliation_metadata with document_path parameter
- _strip_org_prefix edge cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage
from app.utils.storage_path import build_hierarchical_storage_path


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_blob_storage_override.py)
# ---------------------------------------------------------------------------


def _make_bs(*, config_service=None, graph_provider=None):
    return BlobStorage(
        logger=MagicMock(),
        config_service=config_service or AsyncMock(),
        graph_provider=graph_provider,
    )


def _configure_auth(bs, storage_type="local"):
    bs.config_service.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": storage_type},
        ]
    )


def _resp(status=200, json_value=None, text_value=""):
    r = AsyncMock()
    r.status = status
    r.json = AsyncMock(return_value=json_value or {})
    r.text = AsyncMock(return_value=text_value)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path with no graph_provider (flat fallback)
# ---------------------------------------------------------------------------


class TestHierarchicalPathWithoutGraphProvider:
    @pytest.mark.asyncio
    async def test_falls_back_to_flat_vrid_path(self):
        record = MagicMock(connector_id=None)
        result = await build_hierarchical_storage_path(
            record, None, virtual_record_id="vrid-123"
        )
        assert result == "records/vrid-123"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_provider_and_no_vrid(self):
        record = MagicMock(connector_id=None)
        result = await build_hierarchical_storage_path(
            record, None, virtual_record_id=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_connector_id_with_vrid_falls_back(self):
        gp = AsyncMock()
        record = MagicMock(connector_id=None)
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"


# ---------------------------------------------------------------------------
# store_virtual_record_mapping
# ---------------------------------------------------------------------------


class TestStoreVirtualRecordMapping:
    @pytest.mark.asyncio
    async def test_upserts_mapping_to_graph_db(self):
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        result = await bs.store_virtual_record_mapping(
            org_id="org-1",
            virtual_record_id="vrid-1",
            document_id="doc-1",
        )

        assert result is True
        gp.batch_upsert_nodes.assert_called_once()
        call_args = gp.batch_upsert_nodes.call_args[0]
        doc = call_args[0][0]
        assert doc["virtualRecordId"] == "vrid-1"
        assert doc["documentId"] == "doc-1"
        assert doc["record_doc_id"] == "doc-1"
        assert doc["orgId"] == "org-1"

    @pytest.mark.asyncio
    async def test_includes_file_size_when_provided(self):
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        await bs.store_virtual_record_mapping(
            org_id="org-1",
            virtual_record_id="vrid-1",
            document_id="doc-1",
            file_size_bytes=42000,
        )

        doc = gp.batch_upsert_nodes.call_args[0][0][0]
        assert doc["fileSizeBytes"] == 42000

    @pytest.mark.asyncio
    async def test_omits_file_size_when_none(self):
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        await bs.store_virtual_record_mapping(
            org_id="org-1",
            virtual_record_id="vrid-1",
            document_id="doc-1",
            file_size_bytes=None,
        )

        doc = gp.batch_upsert_nodes.call_args[0][0][0]
        assert "fileSizeBytes" not in doc

    @pytest.mark.asyncio
    async def test_raises_when_upsert_returns_false(self):
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=False)
        bs = _make_bs(graph_provider=gp)

        with pytest.raises(Exception, match="Failed to store virtual record mapping"):
            await bs.store_virtual_record_mapping(
                org_id="org-1",
                virtual_record_id="vrid-1",
                document_id="doc-1",
            )


# ---------------------------------------------------------------------------
# _strip_org_prefix
# ---------------------------------------------------------------------------


class TestStripOrgPrefix:
    def test_strips_matching_prefix(self):
        result = BlobStorage._strip_org_prefix("org-1", "org-1/PipesHub/records/conn-1/file.txt")
        assert result == "records/conn-1/file.txt"

    def test_returns_unchanged_when_no_prefix(self):
        result = BlobStorage._strip_org_prefix("org-1", "records/conn-1/file.txt")
        assert result == "records/conn-1/file.txt"

    def test_does_not_strip_partial_match(self):
        result = BlobStorage._strip_org_prefix("org-1", "org-1/Other/file.txt")
        assert result == "org-1/Other/file.txt"


# ---------------------------------------------------------------------------
# _get_current_document_path
# ---------------------------------------------------------------------------


class TestGetCurrentDocumentPath:
    @pytest.mark.asyncio
    async def test_returns_path_on_success(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "_get_auth_and_config",
            new_callable=AsyncMock,
            return_value=({"Authorization": "Bearer x"}, "http://localhost:3001", "local"),
        ), patch(
            "app.modules.transformers.blob_storage.get_shared_session",
        ) as mock_get_session:
            resp = AsyncMock()
            resp.status = 200
            resp.json = AsyncMock(return_value={"documentPath": "org-1/PipesHub/records/conn-1/file.txt"})
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)

            session = MagicMock()
            session.get = MagicMock(return_value=resp)
            mock_get_session.return_value = session

            result = await bs._get_current_document_path("org-1", "doc-123")

        assert result == "org-1/PipesHub/records/conn-1/file.txt"

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "_get_auth_and_config",
            new_callable=AsyncMock,
            return_value=({"Authorization": "Bearer x"}, "http://localhost:3001", "local"),
        ), patch(
            "app.modules.transformers.blob_storage.get_shared_session",
        ) as mock_get_session:
            resp = AsyncMock()
            resp.status = 404
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)

            session = MagicMock()
            session.get = MagicMock(return_value=resp)
            mock_get_session.return_value = session

            result = await bs._get_current_document_path("org-1", "doc-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "_get_auth_and_config",
            new_callable=AsyncMock,
            side_effect=ValueError("missing secret"),
        ):
            result = await bs._get_current_document_path("org-1", "doc-123")

        assert result is None


# ---------------------------------------------------------------------------
# get_actual_content_path
# ---------------------------------------------------------------------------


class TestGetActualContentPath:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_graph_provider(self):
        bs = _make_bs(graph_provider=None)
        result = await bs.get_actual_content_path("org-1", "vrid-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_existing_doc(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "get_document_id_by_virtual_record_id",
            new_callable=AsyncMock, return_value=None,
        ):
            result = await bs.get_actual_content_path("org-1", "vrid-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_record_doc_id(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "get_document_id_by_virtual_record_id",
            new_callable=AsyncMock, return_value={"some_field": "x"},
        ):
            result = await bs.get_actual_content_path("org-1", "vrid-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_strips_org_prefix_from_returned_path(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "get_document_id_by_virtual_record_id",
            new_callable=AsyncMock,
            return_value={"record_doc_id": "doc-1"},
        ), patch.object(
            bs, "_get_current_document_path",
            new_callable=AsyncMock,
            return_value="org-1/PipesHub/records/conn-1/file.txt",
        ):
            result = await bs.get_actual_content_path("org-1", "vrid-1")

        assert result == "records/conn-1/file.txt"

    @pytest.mark.asyncio
    async def test_returns_none_when_path_lookup_fails(self):
        bs = _make_bs(graph_provider=AsyncMock())

        with patch.object(
            bs, "get_document_id_by_virtual_record_id",
            new_callable=AsyncMock,
            side_effect=RuntimeError("lookup failed"),
        ):
            result = await bs.get_actual_content_path("org-1", "vrid-1")

        assert result is None


# ---------------------------------------------------------------------------
# save_reconciliation_metadata with document_path
# ---------------------------------------------------------------------------


class TestSaveReconciliationMetadata:
    @pytest.mark.asyncio
    async def test_uses_provided_document_path(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        with patch.object(
            bs, "_create_metadata_document",
            new_callable=AsyncMock,
            return_value="meta-doc-1",
        ) as mock_create:
            result = await bs.save_reconciliation_metadata(
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vrid-1",
                metadata_dict={"summary": "test"},
                document_path="records/conn-1/group/file.txt",
            )

        assert result == "meta-doc-1"
        mock_create.assert_called_once_with(
            "org-1", "rec-1", "vrid-1", {"summary": "test"},
            "records/conn-1/group/file.txt",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_vrid_path_when_no_document_path(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        with patch.object(
            bs, "_create_metadata_document",
            new_callable=AsyncMock,
            return_value="meta-doc-2",
        ) as mock_create:
            result = await bs.save_reconciliation_metadata(
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vrid-1",
                metadata_dict={"summary": "test"},
                document_path=None,
            )

        assert result == "meta-doc-2"
        mock_create.assert_called_once_with(
            "org-1", "rec-1", "vrid-1", {"summary": "test"},
            "records/vrid-1",
        )

    @pytest.mark.asyncio
    async def test_updates_existing_metadata_via_buffer(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "existing-meta-doc"}
        )
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        with patch.object(
            bs, "_update_metadata_buffer",
            new_callable=AsyncMock,
            return_value=("existing-meta-doc", 500),
        ) as mock_update:
            result = await bs.save_reconciliation_metadata(
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vrid-1",
                metadata_dict={"summary": "updated"},
            )

        assert result == "existing-meta-doc"
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_replacement_when_buffer_update_fails(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"record_metadata_doc_id": "existing-meta-doc"}
        )
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        bs = _make_bs(graph_provider=gp)

        with patch.object(
            bs, "_update_metadata_buffer",
            new_callable=AsyncMock,
            side_effect=RuntimeError("buffer update failed"),
        ), patch.object(
            bs, "_create_metadata_document",
            new_callable=AsyncMock,
            return_value="replacement-doc",
        ) as mock_create:
            result = await bs.save_reconciliation_metadata(
                org_id="org-1",
                record_id="rec-1",
                virtual_record_id="vrid-1",
                metadata_dict={"summary": "fallback"},
            )

        assert result == "replacement-doc"
        mock_create.assert_called_once()
