"""Unit tests for app.utils.storage_path.

Covers sanitize_path_segment, build_record_group_path, and the branches of
build_hierarchical_storage_path: record-group lookup (found/empty-name/None),
record-path success/failure/empty, and the record_name fallback.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.storage_path import (
    build_hierarchical_storage_path,
    build_record_group_path,
    sanitize_path_segment,
)


@dataclass
class _Record:
    connector_id: str | None = "conn-1"
    record_group_id: str | None = None
    id: str | None = "record-1"
    record_name: str | None = "My File.txt"
    virtual_record_id: str | None = "vrid-1"


def _make_graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.get_record_group_by_id = AsyncMock(return_value=None)
    gp.get_record_path = AsyncMock(return_value=None)
    return gp


# ---------------------------------------------------------------------------
# sanitize_path_segment / build_record_group_path
# ---------------------------------------------------------------------------


class TestSanitizePathSegment:
    def test_replaces_unsafe_characters(self) -> None:
        assert sanitize_path_segment('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_truncates_to_100_chars(self) -> None:
        long_name = "a" * 150
        result = sanitize_path_segment(long_name)
        assert len(result) == 100
        assert result == "a" * 100

    def test_leaves_safe_name_untouched(self) -> None:
        assert sanitize_path_segment("normal-name_1.txt") == "normal-name_1.txt"


class TestBuildRecordGroupPath:
    def test_returns_none_when_connector_id_missing(self) -> None:
        assert build_record_group_path(None, "group") is None

    def test_returns_none_when_group_name_missing(self) -> None:
        assert build_record_group_path("conn-1", None) is None

    def test_returns_none_when_group_name_empty(self) -> None:
        assert build_record_group_path("conn-1", "") is None

    def test_returns_none_when_group_name_not_str(self) -> None:
        assert build_record_group_path("conn-1", 123) is None  # type: ignore[arg-type]

    def test_builds_expected_path(self) -> None:
        assert (
            build_record_group_path("conn-1", "My Group")
            == "records/conn-1/My Group"
        )

    def test_sanitizes_group_name(self) -> None:
        assert (
            build_record_group_path("conn-1", "a/b")
            == "records/conn-1/a_b"
        )


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path
# ---------------------------------------------------------------------------


class TestBuildHierarchicalStoragePathEarlyExit:
    @pytest.mark.asyncio
    async def test_no_graph_provider_returns_flat_vrid_path(self) -> None:
        record = _Record()
        result = await build_hierarchical_storage_path(
            record, None, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"

    @pytest.mark.asyncio
    async def test_no_graph_provider_no_vrid_returns_none(self) -> None:
        record = _Record()
        result = await build_hierarchical_storage_path(record, None, virtual_record_id=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_connector_id_returns_flat_vrid_path(self) -> None:
        record = _Record(connector_id=None)
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"
        gp.get_record_group_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_connector_id_no_vrid_returns_none(self) -> None:
        record = _Record(connector_id=None)
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp, virtual_record_id=None)
        assert result is None


class TestRecordGroupLookup:
    @pytest.mark.asyncio
    async def test_no_record_group_id_skips_lookup(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name=None)
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        gp.get_record_group_by_id.assert_not_called()
        # No record_id and no record_name -> fallback to vrid
        assert result == "records/vrid-1"

    @pytest.mark.asyncio
    async def test_group_found_with_group_name_appended(self) -> None:
        record = _Record(record_group_id="grp-1", id=None, record_name=None)
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Finance"})
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")

    @pytest.mark.asyncio
    async def test_group_found_falls_back_to_name_key(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"name": "Legacy Group"})
        gp.get_record_path = AsyncMock(return_value=None)
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        # group name appended to parts, but record_path is None -> fallback path used,
        # and since record_name is None, the whole thing falls back to vrid.
        assert result == "records/vrid-1"

    @pytest.mark.asyncio
    async def test_group_found_but_empty_group_name(self) -> None:
        """Covers branch: group found, groupName/name both empty -> nothing appended."""
        record = _Record(record_group_id="grp-1", id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "", "name": ""})
        result = await build_hierarchical_storage_path(record, gp)
        # group name empty -> not appended; no record_id -> falls to record_name fallback
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_group_lookup_returns_none(self) -> None:
        """Covers branch: get_record_group_by_id returns None (group falsy)."""
        record = _Record(record_group_id="grp-1", id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value=None)
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_group_lookup_raises_exception_is_swallowed(self) -> None:
        record = _Record(record_group_id="grp-1", id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        logger = MagicMock()
        result = await build_hierarchical_storage_path(record, gp, logger=logger)
        assert result == "records/conn-1/fallback.txt"
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_group_lookup_raises_exception_without_logger(self) -> None:
        record = _Record(record_group_id="grp-1", id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        result = await build_hierarchical_storage_path(record, gp, logger=None)
        assert result == "records/conn-1/fallback.txt"


class TestRecordPathLookup:
    @pytest.mark.asyncio
    async def test_record_path_success_splits_into_segments(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="ignored.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="/Folder A/Sub Folder/")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Folder A/Sub Folder"
        gp.get_record_path.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_record_path_sanitizes_segments(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="a/b:c/d?e")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/a/b_c/d_e"

    @pytest.mark.asyncio
    async def test_record_path_none_falls_back_to_record_name(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value=None)
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_record_path_empty_string_falls_back_to_record_name(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_record_path_only_separators_falls_back_to_record_name(self) -> None:
        """record_path == '///' -> split yields no non-empty segments."""
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="///")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_record_path_raises_falls_back_to_vrid(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(side_effect=RuntimeError("boom"))
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1", logger=logger
        )
        assert result == "records/vrid-1"
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_path_raises_no_vrid_returns_none(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(side_effect=RuntimeError("boom"))
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_id_skips_record_path_lookup(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        gp.get_record_path.assert_not_called()
        assert result == "records/conn-1/fallback.txt"


class TestRecordNameFallback:
    @pytest.mark.asyncio
    async def test_no_record_name_and_no_vrid_returns_none(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name=None)
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_name_falls_back_to_vrid(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name=None)
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"

    @pytest.mark.asyncio
    async def test_record_name_sanitized_in_fallback(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name="a/b:c.txt")
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/a_b_c.txt"


class TestTransactionForwarding:
    @pytest.mark.asyncio
    async def test_transaction_forwarded_to_group_lookup(self) -> None:
        record = _Record(record_group_id="grp-1", id=None, record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "G"})
        await build_hierarchical_storage_path(record, gp, transaction="tx-1")
        gp.get_record_group_by_id.assert_awaited_once_with(
            "grp-1", transaction="tx-1"
        )

    @pytest.mark.asyncio
    async def test_transaction_forwarded_to_record_path_lookup(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Folder")
        await build_hierarchical_storage_path(record, gp, transaction="tx-1")
        gp.get_record_path.assert_awaited_once_with("rec-1", transaction="tx-1")

    @pytest.mark.asyncio
    async def test_no_transaction_omits_kwarg(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "G"})
        gp.get_record_path = AsyncMock(return_value="Folder")
        await build_hierarchical_storage_path(record, gp, transaction=None)
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")
        gp.get_record_path.assert_awaited_once_with("rec-1")


class TestFullHierarchy:
    @pytest.mark.asyncio
    async def test_group_and_record_path_both_present(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="ignored.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Finance"})
        gp.get_record_path = AsyncMock(return_value="Q1/Reports")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Finance/Q1/Reports"

    @pytest.mark.asyncio
    async def test_uses_magicmock_record_with_get_attributes(self) -> None:
        record = MagicMock()
        record.connector_id = "conn-2"
        record.record_group_id = None
        record.id = None
        record.record_name = "doc.pdf"
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-2/doc.pdf"
