"""Tests for app.utils.fetch_full_record — record fetching tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError


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


class TestFetchBlockGroupArgs:
    def test_valid_args(self):
        from app.utils.fetch_full_record import FetchBlockGroupArgs

        args = FetchBlockGroupArgs(block_group_number="3")
        assert args.block_group_number == "3"

    def test_missing_block_group_number_fails(self):
        from app.utils.fetch_full_record import FetchBlockGroupArgs

        with pytest.raises(ValidationError):
            FetchBlockGroupArgs()

    def test_custom_reason(self):
        from app.utils.fetch_full_record import FetchBlockGroupArgs

        args = FetchBlockGroupArgs(block_group_number="5", reason="Need context")
        assert args.reason == "Need context"


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
# _fetch_record_by_id
# ===========================================================================


class TestFetchRecordById:
    @pytest.mark.asyncio
    async def test_returns_none_without_graph_provider(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        result = await _fetch_record_by_id(
            "rec-1", None, AsyncMock(), "org-1", {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_blob_store(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        result = await _fetch_record_by_id(
            "rec-1", AsyncMock(), None, "org-1", {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_org_id(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        result = await _fetch_record_by_id(
            "rec-1", AsyncMock(), AsyncMock(), None, {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_resolves_via_vrid_and_fetches_from_blob(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value={
            "id": "rec-1",
            "virtual_record_id": "vrid-1",
            "record_name": "users",
            "record_type": "SQL_TABLE",
        })

        blob_store = AsyncMock()
        vrid_map = {}

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"content": "table data"}

        with patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ), patch("app.utils.chat_helpers.get_record", side_effect=fake_get_record):
            result = await _fetch_record_by_id(
                "rec-1", graph_provider, blob_store, "org-1", vrid_map
            )

        assert result is not None
        assert result["id"] == "rec-1"
        assert result["virtual_record_id"] == "vrid-1"
        assert "vrid-1" in vrid_map

    @pytest.mark.asyncio
    async def test_returns_cached_from_map_by_vrid(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        cached_record = {"id": "rec-1", "content": "cached"}
        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value={
            "id": "rec-1", "virtual_record_id": "vrid-1",
        })

        vrid_map = {"vrid-1": cached_record}
        result = await _fetch_record_by_id(
            "rec-1", graph_provider, AsyncMock(), "org-1", vrid_map
        )

        assert result is cached_record

    @pytest.mark.asyncio
    async def test_returns_none_when_graph_record_missing(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=None)

        result = await _fetch_record_by_id(
            "rec-1", graph_provider, AsyncMock(), "org-1", {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_vrid_in_meta(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value={"id": "rec-1"})

        result = await _fetch_record_by_id(
            "rec-1", graph_provider, AsyncMock(), "org-1", {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_blob_returns_nothing(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value={
            "id": "rec-1", "virtual_record_id": "vrid-1",
        })
        blob_store = AsyncMock()
        vrid_map = {}

        async def noop_get_record(vrid, map_, *args, **kwargs):
            return None

        with patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=noop_get_record,
        ), patch("app.utils.chat_helpers.get_record", side_effect=noop_get_record):
            result = await _fetch_record_by_id(
                "rec-1", graph_provider, blob_store, "org-1", vrid_map
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        result = await _fetch_record_by_id(
            "rec-1", graph_provider, AsyncMock(), "org-1", {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_graph_metadata_enrichment_with_pydantic_model(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        pydantic_record = MagicMock()
        pydantic_record.model_dump.return_value = {
            "id": "rec-1",
            "virtual_record_id": "vrid-1",
            "record_name": "orders",
            "record_type": "SQL_TABLE",
            "version": "2",
            "origin": "pg",
            "connector_name": "main-pg",
            "weburl": "http://example.com",
        }

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(return_value=pydantic_record)

        blob_store = AsyncMock()
        vrid_map = {}

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"content": "data"}

        with patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ), patch("app.utils.chat_helpers.get_record", side_effect=fake_get_record):
            result = await _fetch_record_by_id(
                "rec-1", graph_provider, blob_store, "org-1", vrid_map
            )

        assert result is not None
        assert result["id"] == "rec-1"
        assert result["virtual_record_id"] == "vrid-1"

    @pytest.mark.asyncio
    async def test_graph_metadata_exception_returns_none(self):
        from app.utils.fetch_full_record import _fetch_record_by_id

        graph_provider = AsyncMock()
        graph_provider.get_record_by_id = AsyncMock(
            side_effect=RuntimeError("graph error")
        )

        result = await _fetch_record_by_id(
            "rec-1", graph_provider, AsyncMock(), "org-1", {}
        )

        assert result is None


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
            )

        assert result["ok"] is True
        assert result["record_count"] == 1

    @pytest.mark.asyncio
    async def test_fetched_sql_table_enriched_with_fk(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
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


# ===========================================================================
# create_record_for_fetch_block_group
# ===========================================================================


class TestCreateRecordForFetchBlockGroup:
    def test_creates_record(self):
        from app.utils.fetch_full_record import create_record_for_fetch_block_group

        record = {"id": "r1", "name": "test"}
        block_group = {"group_number": 1}
        blocks = [{"text": "block1"}, {"text": "block2"}]
        result = create_record_for_fetch_block_group(record, block_group, blocks)
        assert "block_containers" in result
        assert len(result["block_containers"]["blocks"]) == 2
        assert result["block_containers"]["block_groups"] == [block_group]

    def test_empty_blocks(self):
        from app.utils.fetch_full_record import create_record_for_fetch_block_group

        record = {"id": "r1"}
        result = create_record_for_fetch_block_group(record, {"g": 1}, [])
        assert result["block_containers"]["blocks"] == []
