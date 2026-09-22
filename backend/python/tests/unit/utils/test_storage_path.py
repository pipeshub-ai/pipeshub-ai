"""Unit tests for app.utils.storage_path.

Covers sanitize_path_segment, build_record_group_path, and the branches of
build_hierarchical_storage_path: record-group lookup (found/empty-name/None),
record-path-segments success/failure/empty, and the record_name fallback.

build_hierarchical_storage_path uses ``get_record_path_segments`` (returns a
list of individual record names) instead of ``get_record_path`` (returns a
``/``-joined string).  This eliminates the ambiguity when a record name
itself contains ``/`` — the old code split the joined string on ``/`` and
produced spurious nested directories for names like Jira ticket titles
containing URLs.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.storage_path import (
    _build_web_storage_path,
    build_hierarchical_storage_path,
    build_record_group_path,
    build_record_group_prefix_from_chain,
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
    gp.get_record_group_path = AsyncMock(return_value=[])
    gp.get_record_path = AsyncMock(return_value=None)
    gp.get_record_path_segments = AsyncMock(return_value=[])
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
        gp.get_record_path_segments = AsyncMock(return_value=[])
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/vrid-1"

    @pytest.mark.asyncio
    async def test_group_found_but_empty_group_name(self) -> None:
        """Covers branch: group found, groupName/name both empty -> nothing appended."""
        record = _Record(record_group_id="grp-1", id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "", "name": ""})
        result = await build_hierarchical_storage_path(record, gp)
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


class TestRecordPathSegmentsLookup:
    @pytest.mark.asyncio
    async def test_segments_used_directly(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="Sub Folder")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Folder A", "Sub Folder"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Folder A/Sub Folder"
        gp.get_record_path_segments.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_slash_in_record_name_sanitized(self) -> None:
        """A segment containing '/' is sanitized to '_'."""
        record = _Record(record_group_id=None, id="rec-1", record_name="API v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Docs", "API v1/v2"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/API v1_v2"

    @pytest.mark.asyncio
    async def test_segments_sanitized_individually(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["a", "b:c", "d?e"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/a/b_c/d_e"

    @pytest.mark.asyncio
    async def test_empty_segments_falls_back_to_record_name(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_segments_raises_falls_back_to_vrid(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(side_effect=RuntimeError("boom"))
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1", logger=logger
        )
        assert result == "records/vrid-1"
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_segments_raises_no_vrid_returns_none(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(side_effect=RuntimeError("boom"))
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_record_id_skips_segments_lookup(self) -> None:
        record = _Record(record_group_id=None, id=None, record_name="fallback.txt")
        gp = _make_graph_provider()
        result = await build_hierarchical_storage_path(record, gp)
        gp.get_record_path_segments.assert_not_called()
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
        gp.get_record_group_path.assert_awaited_once_with(
            "grp-1", transaction="tx-1"
        )
        gp.get_record_group_by_id.assert_awaited_once_with(
            "grp-1", transaction="tx-1"
        )

    @pytest.mark.asyncio
    async def test_transaction_forwarded_to_segments_lookup(self) -> None:
        record = _Record(record_group_id=None, id="rec-1", record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Folder", "f.txt"])
        await build_hierarchical_storage_path(record, gp, transaction="tx-1")
        gp.get_record_path_segments.assert_awaited_once_with("rec-1", transaction="tx-1")

    @pytest.mark.asyncio
    async def test_no_transaction_omits_kwarg(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="f.txt")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "G"})
        gp.get_record_path_segments = AsyncMock(return_value=["Folder", "f.txt"])
        await build_hierarchical_storage_path(record, gp, transaction=None)
        gp.get_record_group_path.assert_awaited_once_with("grp-1")
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")
        gp.get_record_path_segments.assert_awaited_once_with("rec-1")


class TestFullHierarchy:
    @pytest.mark.asyncio
    async def test_group_and_record_path_both_present(self) -> None:
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="Reports")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Finance"})
        gp.get_record_path_segments = AsyncMock(return_value=["Q1", "Reports"])
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
    """Edge cases for record names that contain '/' characters.

    With get_record_path_segments, each name is returned as a separate list
    element.  Names containing '/' are sanitized individually — no ambiguous
    split on '/' needed.
    """

    @pytest.mark.asyncio
    async def test_slash_in_name_with_ancestors(self) -> None:
        """Graph returns ['Docs', 'API v1/v2'] — the slash in 'API v1/v2' is sanitized."""
        record = _Record(id="rec-1", record_name="API v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Docs", "API v1/v2"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/API v1_v2"

    @pytest.mark.asyncio
    async def test_slash_in_name_at_root(self) -> None:
        """Record with '/' in name at root level (no ancestors)."""
        record = _Record(id="rec-1", record_name="v1/v2")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["v1/v2"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/v1_v2"

    @pytest.mark.asyncio
    async def test_slash_in_name_deep_hierarchy(self) -> None:
        """Multiple ancestor levels + record name with '/'."""
        record = _Record(id="rec-1", record_name="draft/final")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(
            return_value=["Team", "Projects", "2024", "draft/final"]
        )
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Team/Projects/2024/draft_final"

    @pytest.mark.asyncio
    async def test_colon_in_name_replaced(self) -> None:
        """':' is replaced with '_' for Windows local storage safety."""
        record = _Record(id="rec-1", record_name="Meeting:Notes")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Docs", "Meeting:Notes"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/Meeting_Notes"

    @pytest.mark.asyncio
    async def test_segments_independent_of_record_name(self) -> None:
        """Segments come from the graph, not from record_name matching."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Docs", "file.pdf"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/file.pdf"

    @pytest.mark.asyncio
    async def test_graph_name_differs_from_record_name(self) -> None:
        """Graph may have a different name than the record object — graph wins."""
        record = _Record(id="rec-1", record_name="renamed.pdf")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Docs", "original.pdf"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Docs/original.pdf"

    @pytest.mark.asyncio
    async def test_record_name_appears_in_ancestor_too(self) -> None:
        """Record name appearing in both ancestor and leaf is handled correctly."""
        record = _Record(id="rec-1", record_name="test")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["test", "subdir", "test"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/test/subdir/test"

    @pytest.mark.asyncio
    async def test_multiple_slashes_in_name(self) -> None:
        """Record name with multiple '/' characters."""
        record = _Record(id="rec-1", record_name="a/b/c")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Root", "a/b/c"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root/a_b_c"

    @pytest.mark.asyncio
    async def test_slash_in_name_with_group(self) -> None:
        """Group + ancestors + record name with '/' all compose correctly."""
        record = _Record(record_group_id="grp-1", id="rec-1", record_name="v1/v2")
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Space"})
        gp.get_record_path_segments = AsyncMock(return_value=["Folder", "v1/v2"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Space/Folder/v1_v2"


# ---------------------------------------------------------------------------
# THE BUG FIX: slash in ANCESTOR name (not just the leaf record)
#
# The old code used get_record_path (which returns a '/'-joined string),
# then split on '/' to recover segments.  When an ANCESTOR name contained
# '/' (e.g. a Jira ticket title with a URL), the split created spurious
# nested directories.  get_record_path_segments returns each name as a
# separate list element, eliminating this ambiguity.
# ---------------------------------------------------------------------------


class TestSlashInAncestorName:
    """Tests for the exact bug: parent record name contains '/', child
    (e.g. attachment) inherits the parent path.  The old split-on-'/'
    logic would create spurious nested directories from the parent name."""

    @pytest.mark.asyncio
    async def test_attachment_under_parent_with_url_in_title(self) -> None:
        """Jira ticket 'QUES-85 https://playground.intellysense.com/quizzes/review'
        has attachment 'image.png'.  Parent name must stay one segment."""
        record = _Record(id="att-1", record_name="image.png")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[
            "QUES-85 https://playground.intellysense.com/quizzes/review",
            "image.png",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == (
            "records/conn-1/"
            "QUES-85 https___playground.intellysense.com_quizzes_review/"
            "image.png"
        )

    @pytest.mark.asyncio
    async def test_parent_and_child_paths_consistent(self) -> None:
        """Parent issue and its attachment must share the same sanitized
        parent directory — no mismatch between a flat parent path and a
        deeply nested attachment path."""
        parent_gp = _make_graph_provider()
        parent_gp.get_record_path_segments = AsyncMock(return_value=[
            "QUES-85 https://example.com/path",
        ])
        parent_record = _Record(id="issue-1", record_name="QUES-85 https://example.com/path")
        parent_path = await build_hierarchical_storage_path(parent_record, parent_gp)

        child_gp = _make_graph_provider()
        child_gp.get_record_path_segments = AsyncMock(return_value=[
            "QUES-85 https://example.com/path",
            "attachment.pdf",
        ])
        child_record = _Record(id="att-1", record_name="attachment.pdf")
        child_path = await build_hierarchical_storage_path(child_record, child_gp)

        assert parent_path == "records/conn-1/QUES-85 https___example.com_path"
        assert child_path == "records/conn-1/QUES-85 https___example.com_path/attachment.pdf"
        assert child_path.startswith(parent_path + "/")

    @pytest.mark.asyncio
    async def test_slash_in_grandparent_name(self) -> None:
        """Three-level hierarchy where the GRANDPARENT has '/' in name."""
        record = _Record(id="rec-1", record_name="leaf.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[
            "Root/Folder",
            "Middle",
            "leaf.txt",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root_Folder/Middle/leaf.txt"

    @pytest.mark.asyncio
    async def test_slash_in_multiple_ancestors(self) -> None:
        """Both parent and grandparent contain '/'."""
        record = _Record(id="rec-1", record_name="file.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[
            "a/b",
            "c/d",
            "file.txt",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/a_b/c_d/file.txt"

    @pytest.mark.asyncio
    async def test_colon_and_slash_in_ancestor(self) -> None:
        """Ancestor with both ':' and '/' — both replaced with '_'."""
        record = _Record(id="rec-1", record_name="report.pdf")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[
            "http://internal.corp:8080/docs",
            "report.pdf",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/http___internal.corp_8080_docs/report.pdf"

    @pytest.mark.asyncio
    async def test_trailing_space_in_ancestor_stripped(self) -> None:
        """Ancestor name with trailing whitespace is stripped by sanitize."""
        record = _Record(id="rec-1", record_name="doc.json")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[
            "QUES-99 Title with trailing space ",
            "doc.json",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/QUES-99 Title with trailing space/doc.json"

    @pytest.mark.asyncio
    async def test_long_ancestor_name_truncated(self) -> None:
        """Ancestor name > 100 chars is truncated."""
        long_name = "A" * 150
        record = _Record(id="rec-1", record_name="child.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[long_name, "child.txt"])
        result = await build_hierarchical_storage_path(record, gp)
        expected_parent = "A" * 100
        assert result == f"records/conn-1/{expected_parent}/child.txt"

    @pytest.mark.asyncio
    async def test_with_record_group_and_slashy_ancestor(self) -> None:
        """Full path: group + ancestor-with-slash + child."""
        record = _Record(
            record_group_id="grp-1",
            id="rec-1",
            record_name="att.png",
        )
        gp = _make_graph_provider()
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Questionnaire's"})
        gp.get_record_path_segments = AsyncMock(return_value=[
            "QUES-108 https://site.com/page",
            "att.png",
        ])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == (
            "records/conn-1/Questionnaire's/"
            "QUES-108 https___site.com_page/"
            "att.png"
        )


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
        gp.get_record_path_segments.assert_not_called()

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

    def test_dot_and_dotdot_sanitized(self) -> None:
        assert sanitize_path_segment(".") == "_"
        assert sanitize_path_segment("..") == "__"

    def test_strip_then_replace(self) -> None:
        """Leading/trailing whitespace stripped BEFORE replacement."""
        assert sanitize_path_segment("  a/b  ") == "a_b"


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — deep hierarchy
# ---------------------------------------------------------------------------


class TestDeepHierarchy:
    @pytest.mark.asyncio
    async def test_many_ancestor_segments(self) -> None:
        """A record with many ancestors produces a valid deep path."""
        record = _Record(connector_id="conn-1", record_name="leaf.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(
            return_value=["root", "level1", "level2", "level3", "level4", "leaf.txt"]
        )
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1"
        )
        assert result == "records/conn-1/root/level1/level2/level3/level4/leaf.txt"

    @pytest.mark.asyncio
    async def test_segments_with_unsafe_chars(self) -> None:
        """Unsafe chars in ancestor names are sanitized."""
        record = _Record(connector_id="conn-1", record_name="file.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(
            return_value=["folder:A", "sub<B>", "file.txt"]
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
        gp.get_record_path_segments = AsyncMock(return_value=["sub", "doc.pdf"])
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
        gp.get_record_path_segments = AsyncMock(return_value=["folder", "doc.pdf"])
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


# ---------------------------------------------------------------------------
# sanitize_path_segment — control characters
# ---------------------------------------------------------------------------


class TestSanitizeControlChars:
    def test_newline_replaced(self) -> None:
        assert sanitize_path_segment("hello\nworld") == "hello_world"

    def test_tab_replaced(self) -> None:
        assert sanitize_path_segment("hello\tworld") == "hello_world"

    def test_carriage_return_replaced(self) -> None:
        assert sanitize_path_segment("hello\rworld") == "hello_world"

    def test_trailing_whitespace_stripped(self) -> None:
        assert sanitize_path_segment("Eclipse config\n\t\t") == "Eclipse config"

    def test_leading_whitespace_stripped(self) -> None:
        assert sanitize_path_segment("  hello  ") == "hello"

    def test_null_byte_replaced(self) -> None:
        assert sanitize_path_segment("a\x00b") == "a_b"

    def test_none_returns_empty(self) -> None:
        assert sanitize_path_segment(None) == ""  # type: ignore[arg-type]

    def test_integer_returns_empty(self) -> None:
        assert sanitize_path_segment(123) == ""  # type: ignore[arg-type]

    def test_whitespace_only_returns_empty(self) -> None:
        assert sanitize_path_segment("   ") == ""


# ---------------------------------------------------------------------------
# Defensive: whitespace-only / None segments don't produce double-slashes
# ---------------------------------------------------------------------------


class TestEmptySegmentFiltering:
    """Whitespace-only or None segments must be filtered AFTER sanitization
    to prevent double-slash paths like ``records/conn-1//child``."""

    @pytest.mark.asyncio
    async def test_whitespace_only_segment_filtered(self) -> None:
        """A whitespace-only recordName passes the graph query's != ''
        filter but becomes '' after sanitize — must not produce //."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["   ", "child.txt"])
        result = await build_hierarchical_storage_path(record, gp)
        assert "//" not in result
        assert result == "records/conn-1/child.txt"

    @pytest.mark.asyncio
    async def test_all_whitespace_segments_fall_back_to_record_name(self) -> None:
        """If every segment sanitizes to empty, fallback to record_name."""
        record = _Record(id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["   ", "\t", "\n"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_none_in_segments_list_filtered(self) -> None:
        """A buggy provider returning [None, 'name'] must not crash."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[None, "child.txt"])
        result = await build_hierarchical_storage_path(record, gp)
        assert "//" not in result
        assert result == "records/conn-1/child.txt"

    @pytest.mark.asyncio
    async def test_integer_in_segments_list_filtered(self) -> None:
        """Non-string segment values must not crash."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[42, "child.txt"])
        result = await build_hierarchical_storage_path(record, gp)
        assert "//" not in result
        assert result == "records/conn-1/child.txt"

    @pytest.mark.asyncio
    async def test_whitespace_only_group_name_filtered(self) -> None:
        """Whitespace-only group name from get_record_group_path must not
        produce a // in the path."""
        record = _Record(record_group_id="grp-1", id=None, record_name="file.txt")
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(return_value=["  ", "Valid"])
        result = await build_hierarchical_storage_path(record, gp)
        assert "//" not in result
        assert result == "records/conn-1/Valid/file.txt"

    @pytest.mark.asyncio
    async def test_mixed_valid_and_empty_segments(self) -> None:
        """Only non-empty sanitized segments appear in the path."""
        record = _Record(id="rec-1", record_name=None)
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(
            return_value=["Root", "   ", "Middle", "", "leaf.txt"]
        )
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root/Middle/leaf.txt"
        assert "//" not in result


class TestBuildRecordGroupPrefixFromChainDefensive:
    def test_whitespace_only_names_filtered(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["  ", "Valid"])
        assert result == "records/conn-1/Valid"
        assert "//" not in result

    def test_all_whitespace_names_returns_none(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["  ", "\t"])
        assert result is None

    def test_none_in_list_filtered(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", [None, "Valid"])  # type: ignore[list-item]
        assert result == "records/conn-1/Valid"


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — nested record group hierarchy
# ---------------------------------------------------------------------------


class TestNestedRecordGroupHierarchy:
    @pytest.mark.asyncio
    async def test_nested_groups_from_get_record_group_path(self) -> None:
        """ServiceNow-style: KB -> Category -> SubCategory."""
        record = _Record(
            record_group_id="grp-android",
            id="rec-1",
            record_name="Eclipse config",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(
            return_value=["IT", "IT", "Android"]
        )
        gp.get_record_path_segments = AsyncMock(return_value=["Eclipse config"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/IT/IT/Android/Eclipse config"

    @pytest.mark.asyncio
    async def test_single_group_from_get_record_group_path(self) -> None:
        """Top-level group with no parents returns single-element list."""
        record = _Record(
            record_group_id="grp-1",
            id="rec-1",
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(return_value=["Finance"])
        gp.get_record_path_segments = AsyncMock(return_value=["file.txt"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Finance/file.txt"

    @pytest.mark.asyncio
    async def test_empty_path_falls_back_to_single_group(self) -> None:
        """When get_record_group_path returns [], falls back to get_record_group_by_id."""
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(return_value=[])
        gp.get_record_group_by_id = AsyncMock(
            return_value={"groupName": "Legacy"}
        )
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Legacy/file.txt"

    @pytest.mark.asyncio
    async def test_group_path_skips_record_group_by_id(self) -> None:
        """When get_record_group_path succeeds, get_record_group_by_id is NOT called."""
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(return_value=["Root", "Child"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root/Child/file.txt"
        gp.get_record_group_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_path_names_are_sanitized(self) -> None:
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(
            return_value=["Root:A", "Child\nB"]
        )
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root_A/Child_B/file.txt"

    @pytest.mark.asyncio
    async def test_group_path_exception_is_swallowed(self) -> None:
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, logger=logger
        )
        assert result == "records/conn-1/file.txt"
        logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# build_record_group_prefix_from_chain
# ---------------------------------------------------------------------------


class TestBuildRecordGroupPrefixFromChain:
    def test_multi_level_chain(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["Root", "Child", "Leaf"])
        assert result == "records/conn-1/Root/Child/Leaf"

    def test_single_element(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["Only"])
        assert result == "records/conn-1/Only"

    def test_empty_list_returns_none(self) -> None:
        assert build_record_group_prefix_from_chain("conn-1", []) is None

    def test_empty_connector_id_returns_none(self) -> None:
        assert build_record_group_prefix_from_chain("", ["Group"]) is None

    def test_names_are_sanitized(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["Root/A", "Child\nB"])
        assert result == "records/conn-1/Root_A/Child_B"

    def test_control_chars_sanitized(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["Tab\there", "Null\x00byte"])
        assert result == "records/conn-1/Tab_here/Null_byte"

    def test_whitespace_stripped_from_names(self) -> None:
        result = build_record_group_prefix_from_chain("conn-1", ["  Root  ", "  Leaf  "])
        assert result == "records/conn-1/Root/Leaf"


# ---------------------------------------------------------------------------
# build_hierarchical_storage_path — traversal-failure fallback to single group
# ---------------------------------------------------------------------------


class TestTraversalFailureFallback:
    @pytest.mark.asyncio
    async def test_traversal_raises_falls_back_to_single_group(self) -> None:
        """When get_record_group_path raises, get_record_group_by_id is called as fallback."""
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(side_effect=RuntimeError("traversal boom"))
        gp.get_record_group_by_id = AsyncMock(return_value={"groupName": "Fallback"})
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, logger=logger
        )
        assert result == "records/conn-1/Fallback/file.txt"
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")
        assert logger.warning.call_count == 1

    @pytest.mark.asyncio
    async def test_both_traversal_and_fallback_raise(self) -> None:
        """When both get_record_group_path and get_record_group_by_id raise,
        the path still works using record_name."""
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(side_effect=RuntimeError("traversal boom"))
        gp.get_record_group_by_id = AsyncMock(side_effect=RuntimeError("fallback boom"))
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, logger=logger
        )
        assert result == "records/conn-1/file.txt"
        assert logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_traversal_raises_fallback_returns_none(self) -> None:
        """When traversal raises and fallback returns None, group is skipped."""
        record = _Record(
            record_group_id="grp-1",
            id=None,
            record_name="file.txt",
        )
        gp = _make_graph_provider()
        gp.get_record_group_path = AsyncMock(side_effect=RuntimeError("traversal boom"))
        gp.get_record_group_by_id = AsyncMock(return_value=None)
        logger = MagicMock()
        result = await build_hierarchical_storage_path(
            record, gp, logger=logger
        )
        assert result == "records/conn-1/file.txt"
        gp.get_record_group_by_id.assert_awaited_once_with("grp-1")


# ---------------------------------------------------------------------------
# Single-segment edge cases
# ---------------------------------------------------------------------------


class TestSingleSegment:
    @pytest.mark.asyncio
    async def test_root_record_single_segment(self) -> None:
        """A root-level record with no parent returns a single-element list."""
        record = _Record(id="rec-1", record_name="Root Doc")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=["Root Doc"])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/Root Doc"

    @pytest.mark.asyncio
    async def test_segments_with_only_empty_strings(self) -> None:
        """If graph returns only empty strings, segments are empty → fallback."""
        record = _Record(id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(return_value=[])
        result = await build_hierarchical_storage_path(record, gp)
        assert result == "records/conn-1/fallback.txt"

    @pytest.mark.asyncio
    async def test_segments_error_without_logger(self) -> None:
        """Exception without logger doesn't crash."""
        record = _Record(id="rec-1", record_name="fallback.txt")
        gp = _make_graph_provider()
        gp.get_record_path_segments = AsyncMock(side_effect=RuntimeError("boom"))
        result = await build_hierarchical_storage_path(
            record, gp, virtual_record_id="vrid-1", logger=None
        )
        assert result == "records/vrid-1"
