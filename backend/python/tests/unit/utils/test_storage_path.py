"""Unit tests for app.utils.storage_path.

Covers sanitize_path_segment, build_record_group_path, and the branches of
build_hierarchical_storage_path: record-group lookup (found/empty-name/None),
record-path success/failure/empty, and the record_name fallback.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.storage_path import (
    _build_web_storage_path,
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
    connector_name: str | None = None
    weburl: str | None = None


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
    async def test_record_path_splits_into_segments(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="Sub Folder")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Folder A/Sub Folder")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Folder A/Sub Folder"
        gp.get_record_path.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_slash_in_record_name_preserved_as_single_segment(self) -> None:
        """A record name containing '/' is kept whole and sanitized."""
        record = _Record(record_group_id=None, id="rec-1", record_name="API v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Docs/API v1/v2")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/API v1_v2"

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
        gp.get_record_path = AsyncMock(return_value="Folder/f.txt")
        await build_hierarchical_storage_path(record, gp, transaction="tx-1")
        gp.get_record_path.assert_awaited_once_with("rec-1", transaction="tx-1")

    @pytest.mark.asyncio
    async def test_no_transaction_omits_kwarg(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "G"})
        gp.get_record_path = AsyncMock(return_value="Folder/f.txt")
        await build_hierarchical_storage_path(record, gp, transaction=None)
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")
        gp.get_record_path.assert_awaited_once_with("rec-1")


class TestFullHierarchy:
    @pytest.mark.asyncio
    async def test_group_and_record_path_both_present(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="Reports")
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


class TestSlashInRecordName:
    """Edge cases for record names that contain '/' characters."""

    @pytest.mark.asyncio
    async def test_slash_in_name_with_ancestors(self) -> None:
        """'Docs/API v1/v2' with record_name='API v1/v2' -> ancestors=['Docs'], name sanitized."""
        record = _Record(id="rec-1", record_name="API v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Docs/API v1/v2")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/API v1_v2"

    @pytest.mark.asyncio
    async def test_slash_in_name_at_root(self) -> None:
        """Record with '/' in name at root level (no ancestors)."""
        record = _Record(id="rec-1", record_name="v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="v1/v2")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/v1_v2"

    @pytest.mark.asyncio
    async def test_slash_in_name_deep_hierarchy(self) -> None:
        """Multiple ancestor levels + record name with '/'."""
        record = _Record(id="rec-1", record_name="draft/final")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Team/Projects/2024/draft/final")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Team/Projects/2024/draft_final"

    @pytest.mark.asyncio
    async def test_colon_in_name_replaced(self) -> None:
        """':' is replaced with '_' for Windows local storage safety."""
        record = _Record(id="rec-1", record_name="Meeting:Notes")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Docs/Meeting:Notes")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/Meeting_Notes"

    @pytest.mark.asyncio
    async def test_record_name_none_falls_to_plain_split(self) -> None:
        """When record_name is None, path is split normally by '/'."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Docs/file.pdf")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/file.pdf"

    @pytest.mark.asyncio
    async def test_record_name_mismatch_falls_to_plain_split(self) -> None:
        """When record_name doesn't match path suffix, split normally."""
        record = _Record(id="rec-1", record_name="renamed.pdf")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Docs/original.pdf")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/original.pdf"

    @pytest.mark.asyncio
    async def test_record_name_appears_in_ancestor_too(self) -> None:
        """Record name appearing in both ancestor and leaf is handled correctly."""
        record = _Record(id="rec-1", record_name="test")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="test/subdir/test")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/test/subdir/test"

    @pytest.mark.asyncio
    async def test_multiple_slashes_in_name(self) -> None:
        """Record name with multiple '/' characters."""
        record = _Record(id="rec-1", record_name="a/b/c")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="Root/a/b/c")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root/a_b_c"

    @pytest.mark.asyncio
    async def test_slash_in_name_with_group(self) -> None:
        """Group + ancestors + record name with '/' all compose correctly."""
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="v1/v2")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Space"})
        gp.get_record_path = AsyncMock(return_value="Folder/v1/v2")
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Space/Folder/v1_v2"


class TestBuildWebStoragePath:
    def test_basic_url(self) -> None:
        result = _build_web_storage_path("conn-1", "https://www.example.com/assets/images/photo.jpg")
        assert result == "records/conn-1/www.example.com/assets/images/photo.jpg"

    def test_url_with_trailing_slash(self) -> None:
        result = _build_web_storage_path("conn-1", "https://www.example.com/assets/")
        assert result == "records/conn-1/www.example.com/assets"

    def test_url_root_path(self) -> None:
        result = _build_web_storage_path("conn-1", "https://www.example.com/")
        assert result == "records/conn-1/www.example.com"

    def test_url_no_path(self) -> None:
        result = _build_web_storage_path("conn-1", "https://www.example.com")
        assert result == "records/conn-1/www.example.com"

    def test_url_deep_path(self) -> None:
        result = _build_web_storage_path(
            "conn-1",
            "https://www.cityfinance.in/assets/images/homepage/spotlight/blueprint.pdf",
        )
        assert result == "records/conn-1/www.cityfinance.in/assets/images/homepage/spotlight/blueprint.pdf"

    def test_url_with_port(self) -> None:
        result = _build_web_storage_path("conn-1", "https://localhost:8080/page")
        assert result == "records/conn-1/localhost_8080/page"

    def test_no_host_returns_none(self) -> None:
        result = _build_web_storage_path("conn-1", "not-a-url")
        assert result is None

    def test_query_params_ignored(self) -> None:
        result = _build_web_storage_path("conn-1", "https://example.com/page?q=1&r=2")
        assert result == "records/conn-1/example.com/page"


class TestWebConnectorHierarchicalPath:
    @pytest.mark.asyncio
    async def test_web_record_uses_weburl(self) -> None:
        record = _Record(
            connector_name="WEB",
            weburl="https://www.cityfinance.in/assets/images/photo.jpg",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/www.cityfinance.in/assets/images/photo.jpg"
        gp.get_record_group_by_id.assert_not_called()
        gp.get_record_path.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_record_enum_connector_name(self) -> None:
        """connector_name may be an enum with .value == 'WEB'."""
        from enum import Enum

        class MockConnectors(Enum):
            WEB = "WEB"

        record = _Record(
            connector_name=MockConnectors.WEB,
            weburl="https://example.com/page",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/example.com/page"

    @pytest.mark.asyncio
    async def test_web_record_no_weburl_falls_through(self) -> None:
        record = _Record(
            connector_name="WEB",
            weburl=None,
            id=None,
            record_name="fallback.txt",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_web_record_invalid_weburl_falls_through(self) -> None:
        record = _Record(
            connector_name="WEB",
            weburl="not-a-url",
            id=None,
            record_name="fallback.txt",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_non_web_connector_ignores_weburl(self) -> None:
        record = _Record(
            connector_name="DRIVE",
            weburl="https://example.com/page",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/file.txt"


# ---------------------------------------------------------------------------
# sanitize_path_segment — additional edge cases
# ---------------------------------------------------------------------------


class TestSanitizePathSegmentEdgeCases:
    def test_double_quote_replaced(self) -> None:
        assert sanitize_path_segment('file"name') == "file_name"

    def test_all_unsafe_chars_in_one(self) -> None:
        result = sanitize_path_segment('a/b\\c:d*e?f"g<h>i|j')
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert "*" not in result
        assert "?" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result
        assert "|" not in result

    def test_unicode_characters_preserved(self) -> None:
        assert sanitize_path_segment("日本語ファイル") == "日本語ファイル"

    def test_empty_string(self) -> None:
        assert sanitize_path_segment("") == ""

    def test_only_unsafe_chars(self) -> None:
        assert sanitize_path_segment('/:*?"<>|') == "________"

    def test_spaces_preserved(self) -> None:
        assert sanitize_path_segment("my file name") == "my file name"

    def test_dots_preserved(self) -> None:
        assert sanitize_path_segment("file.txt") == "file.txt"

    def test_exact_100_chars_untouched(self) -> None:
        name = "a" * 100
        assert sanitize_path_segment(name) == name
        assert len(sanitize_path_segment(name)) == 100


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — deep hierarchy
# ---------------------------------------------------------------------------


class TestDeepHierarchy:
    @pytest.mark.asyncio
    async def test_many_ancestor_segments(self) -> None:
        """A record with many ancestors produces a valid deep path."""
        record = _Record(connector_id="conn-1", record_name="leaf.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(
            return_value="root/level1/level2/level3/level4/leaf.txt"
        )
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/root/level1/level2/level3/level4/leaf.txt"

    @pytest.mark.asyncio
    async def test_path_with_unsafe_chars_in_ancestors(self) -> None:
        """Unsafe chars in ancestor names are sanitized."""
        record = _Record(connector_id="conn-1", record_name="file.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(
            return_value="folder:A/sub<B>/file.txt"
        )
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/folder_A/sub_B_/file.txt"


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — group + path combined
# ---------------------------------------------------------------------------


class TestGroupPlusPathCombined:
    @pytest.mark.asyncio
    async def test_group_and_record_path_both_present(self) -> None:
        record = _Record(
            connector_id="conn-1",
            record_group_id="grp-1",
            record_name="doc.pdf",
        )
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(
            return_value={"groupName": "Sales"}
        )
        gp.get_record_path = AsyncMock(return_value="sub/doc.pdf")
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/Sales/sub/doc.pdf"

    @pytest.mark.asyncio
    async def test_group_lookup_fails_path_still_works(self) -> None:
        record = _Record(
            connector_id="conn-1",
            record_group_id="grp-1",
            record_name="doc.pdf",
        )
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(side_effect=Exception("DB error"))
        gp.get_record_path = AsyncMock(return_value="folder/doc.pdf")
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1", logger=logger
        )
        assert result == "records/conn-1/folder/doc.pdf"
        assert logger.warning.called


# ---------------------------------------------------------------------------
# _build_web_storage_path — additional edge cases
# ---------------------------------------------------------------------------


class TestBuildWebStoragePathEdgeCases:
    def test_query_params_stripped(self) -> None:
        """Query params are not part of the parsed path."""
        result = _build_web_storage_path(
            "conn-1", "https://example.com/page?utm=abc"
        )
        assert result == "records/conn-1/example.com/page"
        assert "utm" not in result

    def test_fragment_stripped(self) -> None:
        result = _build_web_storage_path(
            "conn-1", "https://example.com/page#section"
        )
        assert result == "records/conn-1/example.com/page"

    def test_encoded_chars_in_path(self) -> None:
        result = _build_web_storage_path(
            "conn-1", "https://example.com/path%20with%20spaces/file"
        )
        assert result is not None
        assert "conn-1" in result

    def test_url_with_only_host(self) -> None:
        result = _build_web_storage_path("conn-1", "https://example.com")
        assert result == "records/conn-1/example.com"

    def test_scheme_only_returns_none(self) -> None:
        result = _build_web_storage_path("conn-1", "not-a-url")
        assert result is None


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — record_path does not end with name
# ---------------------------------------------------------------------------


class TestRecordPathNotEndingWithName:
    @pytest.mark.asyncio
    async def test_path_without_name_suffix(self) -> None:
        """When record_path doesn't end with record_name, the entire path
        is split into segments."""
        record = _Record(
            connector_id="conn-1",
            record_name="renamed.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="folder/original.txt")
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/folder/original.txt"

    @pytest.mark.asyncio
    async def test_path_with_only_separators(self) -> None:
        """Path that is only slashes results in no segments added."""
        record = _Record(connector_id="conn-1", record_name="file.txt")
        gp = _make_graph_provider()
        gp.get_record_path = AsyncMock(return_value="///")
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/file.txt"


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — enum connector_name
# ---------------------------------------------------------------------------


class TestEnumConnectorName:
    @pytest.mark.asyncio
    async def test_enum_web_connector_uses_weburl(self) -> None:
        """When connector_name is an enum with value 'WEB', weburl path is used."""
        from enum import Enum

        class CN(Enum):
            WEB = "WEB"

        record = _Record(
            connector_id="conn-web",
            connector_name=CN.WEB,
            weburl="https://docs.example.com/guide",
        )
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-web/docs.example.com/guide"
