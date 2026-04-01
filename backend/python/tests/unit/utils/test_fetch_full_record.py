"""Tests for app.utils.fetch_full_record — record fetching tools."""

import uuid

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


class TestFetchMultipleRecordsImpl:
    @pytest.mark.asyncio
    async def test_found_records_by_graphdb_key(self):
        """Records found by matching value["id"] in the map."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        r1_id = str(uuid.uuid4())
        r2_id = str(uuid.uuid4())
        records_map = {
            "vr1": {"id": r1_id, "content": "Record 1 data"},
            "vr2": {"id": r2_id, "content": "Record 2 data"},
        }
        result = await _fetch_multiple_records_impl([r1_id, r2_id], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 2

    @pytest.mark.asyncio
    async def test_partial_found_adds_to_not_available_ids(self):
        """One record found; one not found → in not_available_ids list."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        r1_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())
        records_map = {
            "vr1": {"id": r1_id, "content": "data"},
        }
        result = await _fetch_multiple_records_impl([r1_id, missing_id], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1
        assert missing_id in result["not_available_ids"]

    @pytest.mark.asyncio
    async def test_none_found_returns_error(self):
        """No records found → ok=False with error message."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        missing_id = str(uuid.uuid4())
        result = await _fetch_multiple_records_impl([missing_id], {})
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_record_ids(self):
        """Empty record_ids list → ok=False."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        result = await _fetch_multiple_records_impl([], {"vr1": {"id": "r1"}})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_none_values_in_map_skipped(self):
        """None map values are skipped during lookup; non-None found correctly."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        r2_id = str(uuid.uuid4())
        records_map = {
            "vr1": None,
            "vr2": {"id": r2_id, "content": "data"},
        }
        result = await _fetch_multiple_records_impl([r2_id], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1

    @pytest.mark.asyncio
    async def test_not_found_id_goes_to_not_available_ids(self):
        """Any ID not resolved ends up in not_available_ids."""
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        found_id = str(uuid.uuid4())
        missing_id = "some-unresolved-id"
        records_map = {"vr1": {"id": found_id, "content": "data"}}

        result = await _fetch_multiple_records_impl(
            [found_id, missing_id],
            records_map,
        )
        assert result["ok"] is True
        assert missing_id in result["not_available_ids"]

    @pytest.mark.asyncio
    async def test_graph_provider_called_for_missing_id(self):
        """When org_id + graph_provider are provided, get_document is called for missing IDs."""
        from unittest.mock import AsyncMock, MagicMock

        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        record_id = str(uuid.uuid4())
        records_map = {}

        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value=None)

        result = await _fetch_multiple_records_impl(
            [record_id],
            records_map,
            org_id="org-123",
            graph_provider=graph_provider,
        )

        graph_provider.get_document.assert_called_once()
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_provider_completed_record_fetched(self):
        """Completed graphDb record triggers blob_store fetch and is added to results."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.utils.fetch_full_record import _fetch_multiple_records_impl
        from app.config.constants.arangodb import ProgressStatus

        record_id = str(uuid.uuid4())
        vrid = str(uuid.uuid4())
        records_map = {}
        blob_record = {"id": record_id, "content": "fetched"}

        graphDb_record = {
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": vrid,
        }

        graph_provider = MagicMock()
        graph_provider.get_document = AsyncMock(return_value=graphDb_record)
        graph_provider.config_service = MagicMock()

        mock_blob_store = MagicMock()
        mock_blob_store.get_record_from_storage = AsyncMock(return_value=blob_record)
        mock_blob_store.config_service = graph_provider.config_service
        mock_blob_store.config_service.get_config = AsyncMock(return_value={})

        with patch("app.utils.fetch_full_record.BlobStorage", return_value=mock_blob_store):
            result = await _fetch_multiple_records_impl(
                [record_id],
                records_map,
                org_id="org-123",
                graph_provider=graph_provider,
            )

        assert result["ok"] is True
        assert len(result["records"]) == 1


class TestCreateFetchFullRecordTool:
    def test_creates_tool(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        r_id = str(uuid.uuid4())
        records_map = {"vr1": {"id": r_id, "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        assert tool.name == "fetch_full_record"

    @pytest.mark.asyncio
    async def test_tool_invocation_success(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        r_id = str(uuid.uuid4())
        records_map = {"vr1": {"id": r_id, "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": [r_id], "reason": "test"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_tool_invocation_not_found(self):
        """ID not in map → ok=False."""
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": ["missing"], "reason": "test"})
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_invocation_exception_returns_error_dict(self):
        """When _fetch_multiple_records_impl raises, the tool catches and returns error dict."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        r_id = str(uuid.uuid4())
        records_map = {"vr1": {"id": r_id, "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            side_effect=RuntimeError("unexpected failure"),
        ):
            result = await tool.ainvoke({"record_ids": [r_id], "reason": "test"})

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
