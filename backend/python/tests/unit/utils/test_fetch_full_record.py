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


class TestFetchMultipleRecordsImplGraphFallback:
    """Covers the org_id + graph_provider fallback branch (lines 97-125 in source)."""

    def _make_graph_provider(self, *, document=None, raises=None, endpoints=None):
        gp = MagicMock()
        gp.config_service = MagicMock()
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
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
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
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
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
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
        )

        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_graph_fallback_document_not_found(self):
        """graph_provider.get_document returns None → record not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(document=None)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
        )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_exception_swallowed(self):
        """Exceptions inside the fallback block are swallowed; record marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(raises=RuntimeError("arango down"))

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
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
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
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
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
            )

        assert result["ok"] is True


class TestCreateFetchFullRecordTool:
    def test_creates_tool(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
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
