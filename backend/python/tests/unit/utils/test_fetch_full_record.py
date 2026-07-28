"""Tests for app.utils.fetch_full_record — record fetching tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.models.entities import TicketRecord


class TestFetchFullRecordArgs:
    def test_valid_args(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=["r1", "r2"])
        assert args.record_ids == ["r1", "r2"]
        assert "Fetching full record" in args.reason

    def test_custom_reason(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=["r1"], reason="Need full context")
        assert args.reason == "Need full context"

    def test_missing_record_ids_fails(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        with pytest.raises(ValidationError):
            FetchFullRecordArgs()

    def test_empty_record_ids(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=[])
        assert args.record_ids == []




# ===========================================================================
# _enrich_sql_table_with_fk_relations
# ===========================================================================


class TestEnrichSqlTableWithFkRelations:
    @pytest.mark.asyncio
    async def test_enriches_with_fk_ids(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "rec-1", "record_name": "users"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=["child-1", "child-2"])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=["parent-1"])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert result["fk_child_record_ids"] == ["child-1", "child-2"]
        assert result["fk_parent_record_ids"] == ["parent-1"]

    @pytest.mark.asyncio
    async def test_returns_copy_not_original(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "rec-1", "record_name": "orders"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert "fk_parent_record_ids" not in record
        assert "fk_parent_record_ids" in result

    @pytest.mark.asyncio
    async def test_skips_when_no_record_id(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"record_name": "no_id_table"}
        graph_provider = AsyncMock()

        result = await _enrich_sql_table_with_fk_relations(record, graph_provider)
        assert result is record

    @pytest.mark.asyncio
    async def test_uses_record_id_field(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"record_id": "rec-alt", "record_name": "alt"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert "fk_child_record_ids" in result

    @pytest.mark.asyncio
    async def test_child_fetch_exception_handled(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "rec-1"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("graph down")
        )
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=["p1"])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert result["fk_child_record_ids"] == []
        assert result["fk_parent_record_ids"] == ["p1"]

    @pytest.mark.asyncio
    async def test_parent_fetch_exception_handled(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "rec-1"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=["c1"])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("graph down")
        )

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert result["fk_child_record_ids"] == ["c1"]
        assert result["fk_parent_record_ids"] == []

    @pytest.mark.asyncio
    async def test_converts_non_list_iterables(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "rec-1"}
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(
            return_value={"c1", "c2"}
        )
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(
            return_value=("p1",)
        )

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _enrich_sql_table_with_fk_relations(record, graph_provider)

        assert isinstance(result["fk_child_record_ids"], list)
        assert isinstance(result["fk_parent_record_ids"], list)


# ===========================================================================
# _apply_live_ticket_context_metadata
# ===========================================================================


class TestApplyLiveTicketContextMetadata:
    @pytest.mark.asyncio
    async def test_skips_non_ticket(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "r1", "record_type": "FILE", "context_metadata": "original"}
        config_service = MagicMock()
        graph_provider = AsyncMock()

        with patch.object(
            TicketRecord,
            "to_llm_context_with_live_fields",
            new=AsyncMock(),
        ) as mock_live:
            await _apply_live_ticket_context_metadata(
                record,
                config_service=config_service,
                graph_provider=graph_provider,
                frontend_url=None,
            )

        mock_live.assert_not_called()
        assert record["context_metadata"] == "original"

    @pytest.mark.asyncio
    async def test_skips_without_config_service(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "r1", "record_type": "TICKET", "context_metadata": "original"}

        with patch.object(
            TicketRecord,
            "to_llm_context_with_live_fields",
            new=AsyncMock(),
        ) as mock_live:
            await _apply_live_ticket_context_metadata(
                record,
                config_service=None,
                graph_provider=AsyncMock(),
                frontend_url=None,
            )

        mock_live.assert_not_called()
        assert record["context_metadata"] == "original"

    @pytest.mark.asyncio
    async def test_upgrades_ticket_with_live_fields(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "ticket-1", "record_type": "TICKET"}
        config_service = MagicMock()
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"status": "OPEN"})

        ticket = MagicMock(spec=TicketRecord)
        ticket.to_llm_context_with_live_fields = AsyncMock(return_value="live context")

        with patch(
            "app.utils.fetch_full_record.create_record_instance_from_dict",
            return_value=ticket,
        ):
            await _apply_live_ticket_context_metadata(
                record,
                config_service=config_service,
                graph_provider=graph_provider,
                frontend_url="http://frontend",
            )

        assert record["context_metadata"] == "live context"
        ticket.to_llm_context_with_live_fields.assert_awaited_once_with(
            frontend_url="http://frontend",
            config_service=config_service,
        )


# ===========================================================================
# _fetch_multiple_records_impl
# ===========================================================================


class TestFetchMultipleRecordsImpl:
    @pytest.mark.asyncio
    async def test_found_records(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "content": "Record 1 data"},
            "vr2": {"id": "r2", "content": "Record 2 data"},
        }
        result = await _fetch_multiple_records_impl(["r1", "r2"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 2

    @pytest.mark.asyncio
    async def test_map_hit_ticket_upgrades_live_context_metadata(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {
                "id": "r1",
                "record_type": "TICKET",
                "context_metadata": "graph-only",
            },
        }
        graph_provider = AsyncMock()
        graph_provider.config_service = MagicMock()

        with patch(
            "app.utils.fetch_full_record._apply_live_ticket_context_metadata",
            new=AsyncMock(),
        ) as mock_upgrade:
            result = await _fetch_multiple_records_impl(
                ["r1"],
                records_map,
                graph_provider=graph_provider,
            )

        assert result["ok"] is True
        mock_upgrade.assert_awaited_once()
        assert mock_upgrade.call_args[0][0]["id"] == "r1"

    @pytest.mark.asyncio
    async def test_graph_fallback_ticket_upgrades_live_context_metadata(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "TICKET",
        })
        graph_provider.config_service = MagicMock()

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {
                "id": "r1",
                "record_type": "TICKET",
                "context_metadata": "graph-only",
            }

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ), patch(
            "app.utils.fetch_full_record._apply_live_ticket_context_metadata",
            new=AsyncMock(),
        ) as mock_upgrade:
            result = await _fetch_multiple_records_impl(
                ["r1"],
                records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="user-1",
            )

        assert result["ok"] is True
        mock_upgrade.assert_awaited_once()
        assert mock_upgrade.call_args[0][0]["id"] == "r1"

    @pytest.mark.asyncio
    async def test_partial_found(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r1", "r_missing"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1
        assert "r_missing" in result["not_available_ids"]

    @pytest.mark.asyncio
    async def test_none_found(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r_missing"], records_map)
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_record_ids(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        result = await _fetch_multiple_records_impl([], {"vr1": {"id": "r1"}})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_empty_virtual_record_map(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        result = await _fetch_multiple_records_impl(["r1"], {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_none_values_in_map_skipped(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": None,
            "vr2": {"id": "r2", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r2"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1

    @pytest.mark.asyncio
    async def test_sql_table_enriched_with_fk_when_graph_provider(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_type": "SQL_TABLE"},
        }
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=["c1"])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=["p1"])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _fetch_multiple_records_impl(
                ["r1"], records_map, graph_provider=graph_provider
            )

        assert result["ok"] is True
        rec = result["records"][0]
        assert rec["fk_child_record_ids"] == ["c1"]
        assert rec["fk_parent_record_ids"] == ["p1"]

    @pytest.mark.asyncio
    async def test_sql_table_not_enriched_without_graph_provider(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_type": "SQL_TABLE"},
        }
        result = await _fetch_multiple_records_impl(["r1"], records_map)

        assert result["ok"] is True
        rec = result["records"][0]
        assert "fk_child_record_ids" not in rec

    @pytest.mark.asyncio
    async def test_non_sql_table_not_enriched(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_type": "DOCUMENT"},
        }
        graph_provider = AsyncMock()

        result = await _fetch_multiple_records_impl(
            ["r1"], records_map, graph_provider=graph_provider
        )

        assert result["ok"] is True
        rec = result["records"][0]
        assert "fk_child_record_ids" not in rec

    @pytest.mark.asyncio
    async def test_missing_record_fetched_from_blob(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "DOCUMENT",
        })
        graph_provider.config_service = MagicMock()

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"id": "r1", "content": "fetched from blob"}

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ):
            result = await _fetch_multiple_records_impl(
                ["r1"], records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="user-1",
            )

        assert result["ok"] is True
        assert result["record_count"] == 1

    @pytest.mark.asyncio
    async def test_fetched_sql_table_enriched_with_fk(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "SQL_TABLE",
        })
        graph_provider.config_service = MagicMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=["c1"])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"id": "r1", "record_type": "SQL_TABLE", "content": "data"}

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ), patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _fetch_multiple_records_impl(
                ["r1"], records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="user-1",
            )

        assert result["ok"] is True
        rec = result["records"][0]
        assert rec["fk_child_record_ids"] == ["c1"]

    @pytest.mark.asyncio
    async def test_recordType_key_also_triggers_fk_enrichment(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "recordType": "SQL_TABLE"},
        }
        graph_provider = AsyncMock()
        graph_provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph_provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])

        with patch("app.config.constants.arangodb.RecordRelations") as mock_rr:
            mock_rr.FOREIGN_KEY.value = "FOREIGN_KEY"
            result = await _fetch_multiple_records_impl(
                ["r1"], records_map, graph_provider=graph_provider
            )

        assert result["ok"] is True
        rec = result["records"][0]
        assert "fk_child_record_ids" in rec


# ===========================================================================
# create_fetch_full_record_tool
# ===========================================================================


class TestFetchMultipleRecordsImplGraphFallback:
    """Covers the org_id + graph_provider fallback branch (lines 97-125 in source)."""

    def _make_graph_provider(self, *, document=None, raises=None, endpoints=None, has_access=True):
        gp = MagicMock()
        gp.config_service = MagicMock()
        gp.check_record_access_with_details = AsyncMock(return_value=has_access)
        if raises is not None:
            gp.get_document = AsyncMock(side_effect=raises)
        else:
            gp.get_document = AsyncMock(return_value=document)
        gp.config_service.get_config = AsyncMock(
            return_value=endpoints if endpoints is not None else {},
        )
        return gp

    @pytest.mark.asyncio
    async def test_graph_fallback_returns_blob_record(self):
        """graphDb lookup hits, indexing COMPLETED, BlobStorage populates the map."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
            endpoints={"frontend": {"publicEndpoint": "https://app.example"}},
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1", "content": "blob-content"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ) as mock_get_record:
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
            )

        assert result["ok"] is True
        assert len(result["records"]) == 1
        assert result["records"][0]["virtual_record_id"] == "vrid-1"
        assert result["records"][0]["content"] == "blob-content"
        mock_get_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_fallback_endpoint_config_exception(self):
        """If fetching endpoints config raises, we still proceed with frontend_url=None."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
        )
        graph_provider.config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd down"),
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1", "content": "blob-content"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
            )

        assert result["ok"] is True
        assert len(result["records"]) == 1

    @pytest.mark.asyncio
    async def test_graph_fallback_indexing_not_completed(self):
        """If indexingStatus is not COMPLETED, record is marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "IN_PROGRESS", "virtualRecordId": "vrid-1"},
        )

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
        )

        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_graph_fallback_document_not_found(self):
        """graph_provider.get_document returns None → record not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(document=None)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
        )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_exception_swallowed(self):
        """Exceptions inside the fallback block are swallowed; record marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(raises=RuntimeError("arango down"))

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
        )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_blob_record_missing(self):
        """get_record does not populate the map → record marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            pass

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
            )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_endpoints_not_dict(self):
        """If endpoints_config is not a dict, we skip it cleanly and continue."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
            endpoints="not-a-dict",  # pyright: ignore [reportArgumentType]
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="user-1",
            )

        assert result["ok"] is True


class TestCreateFetchFullRecordTool:
    def test_creates_tool(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        assert tool.name == "fetch_full_record"

    def test_creates_tool_with_optional_deps(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        tool = create_fetch_full_record_tool(
            virtual_record_id_to_result={},
            graph_provider=AsyncMock(),
            blob_store=AsyncMock(),
            org_id="org-1",
        )
        assert tool.name == "fetch_full_record"

    @pytest.mark.asyncio
    async def test_tool_invocation_success(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_tool_invocation_not_found(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": ["missing"], "reason": "test"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_tool_invocation_exception_returns_error_dict(self):
        """When _fetch_multiple_records_impl raises, the tool catches and returns error dict."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            side_effect=RuntimeError("unexpected failure"),
        ):
            result = await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})

        assert result["ok"] is False
        assert "Failed to fetch records" in result["error"]
        assert "unexpected failure" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_invocation_generic_exception(self):
        """Cover the except branch with a different exception type."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {}
        tool = create_fetch_full_record_tool(records_map)

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            side_effect=ValueError("bad value"),
        ):
            result = await tool.ainvoke({"record_ids": ["x"], "reason": "test"})

        assert result["ok"] is False
        assert "bad value" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_passes_graph_provider_and_blob_store(self):
        """Verify that optional deps are forwarded to _fetch_multiple_records_impl."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        mock_gp = AsyncMock()
        mock_bs = AsyncMock()
        records_map = {"vr1": {"id": "r1"}}

        tool = create_fetch_full_record_tool(
            records_map,
            graph_provider=mock_gp,
            blob_store=mock_bs,
            org_id="org-1",
        )

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            new_callable=AsyncMock,
            return_value={"ok": True, "records": [], "record_count": 0},
        ) as mock_impl:
            await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})

        mock_impl.assert_awaited_once()
        call_args = mock_impl.call_args
        assert call_args.kwargs["graph_provider"] is mock_gp
        assert call_args.kwargs["blob_store"] is mock_bs
        assert call_args.kwargs["org_id"] == "org-1"


