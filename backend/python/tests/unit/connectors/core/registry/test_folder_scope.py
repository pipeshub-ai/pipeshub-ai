"""Tests for app.connectors.core.registry.folder_scope."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType, ListOperator
from app.connectors.core.registry.folder_scope import (
    _PAGE_SIZE,
    CleanupResult,
    FolderScope,
    clean_up_scope,
    remove_records_outside_scope,
)


def folders(values, operator=ListOperator.IN) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(key="folder_paths", value=values, type=FilterType.LIST, operator=operator),
    ])


class TestFromFilters:
    def test_no_filter_means_everything(self):
        scope = FolderScope.from_filters(FilterCollection())
        assert scope.is_everything
        assert scope.list_prefixes == [""]
        assert scope.includes_file("any/where.txt")

    def test_paths_are_normalised_to_folders(self):
        scope = FolderScope.from_filters(folders(["/reports/", " legal ", "a/b"]))
        assert scope.folders == ("a/b/", "legal/", "reports/")

    def test_a_folder_inside_another_chosen_one_adds_nothing(self):
        scope = FolderScope.from_filters(folders(["reports", "reports/2026"]))
        assert scope.folders == ("reports/",)

    def test_naming_the_root_includes_everything(self):
        assert FolderScope.from_filters(folders(["/"])).is_everything

    def test_excluding_the_root_excludes_everything(self):
        scope = FolderScope.from_filters(folders(["/"], ListOperator.NOT_IN))
        assert not scope.includes_file("a.txt")


class TestInclude:
    scope = FolderScope(("reports/",))

    def test_lists_only_the_chosen_folders(self):
        assert self.scope.list_prefixes == ["reports/"]

    def test_matches_folders_not_name_prefixes(self):
        assert self.scope.includes_file("reports/2026/q1.pdf")
        assert not self.scope.includes_file("reports-old/q1.pdf")
        assert not self.scope.includes_file("other.txt")

    def test_keeps_the_folders_leading_to_a_chosen_one(self):
        scope = FolderScope(("reports/2026/",))
        assert scope.includes_folder("reports")
        assert scope.includes_folder("reports/2026/q1")
        assert not scope.includes_folder("reports/2025")

    def test_a_folder_named_like_part_of_a_chosen_one_is_not_kept(self):
        # Both paths end in "/", so "reports/" never starts with "report/".
        assert not FolderScope(("reports/",)).includes_folder("report")
        assert not FolderScope(("reports/2026/",)).includes_folder("reports/202")
        assert not FolderScope(("reports/2026/",)).includes_folder("report")


class TestExclude:
    scope = FolderScope(("tmp/",), exclude=True)

    def test_lists_everything_and_skips(self):
        assert self.scope.list_prefixes == [""]

    def test_skips_the_excluded_folder_only(self):
        assert not self.scope.includes_file("tmp/cache.bin")
        assert self.scope.includes_file("tmpfile.txt")
        assert not self.scope.includes_folder("tmp")
        assert not self.scope.includes_folder("tmp/sub")
        assert self.scope.includes_folder("docs")


class TestRemoveRecordsOutsideScope:
    @staticmethod
    def record(record_id, external_id, folder=False):
        return MagicMock(
            id=record_id,
            external_record_id=external_id,
            mime_type=MimeTypes.FOLDER.value if folder else "application/pdf",
        )

    @pytest.mark.asyncio
    async def test_removes_what_the_scope_leaves_out(self):
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock(return_value=[
            self.record("keep-file", "b1/reports/2026/q1.pdf"),
            self.record("keep-parent", "b1/reports", folder=True),
            self.record("drop-file", "b1/legal/contract.pdf"),
            self.record("drop-folder", "b1/legal", folder=True),
            self.record("other-bucket", "b2/legal/contract.pdf"),
        ])
        processor.on_record_deleted = AsyncMock()

        removed = await remove_records_outside_scope(
            processor, "conn-1", "b1", FolderScope(("reports/2026/",)), logging.getLogger("t")
        )

        processor.get_records_in_record_group.assert_awaited_once_with("conn-1", "b1", 500, None)
        deleted = [c.args[0] for c in processor.on_record_deleted.await_args_list]
        assert sorted(deleted) == ["drop-file", "drop-folder"]
        assert removed == CleanupResult(removed=2, failed=0)

    @pytest.mark.asyncio
    async def test_reads_the_bucket_a_page_at_a_time(self, monkeypatch):
        monkeypatch.setattr("app.connectors.core.registry.folder_scope._PAGE_SIZE", 2)
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock(side_effect=[
            [self.record("r1", "b1/x/1.pdf"), self.record("r2", "b1/keep/2.pdf")],
            [self.record("r3", "b1/x/3.pdf")],
        ])
        processor.on_record_deleted = AsyncMock()

        removed = await remove_records_outside_scope(
            processor, "c", "b1", FolderScope(("keep/",)), logging.getLogger("t")
        )

        pages = [c.args for c in processor.get_records_in_record_group.await_args_list]
        assert pages == [("c", "b1", 2, None), ("c", "b1", 2, "r2")]
        assert removed == CleanupResult(removed=2, failed=0)

    @pytest.mark.asyncio
    async def test_does_nothing_without_a_folder_filter(self):
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock()

        assert await remove_records_outside_scope(processor, "c", "b1", FolderScope(), logging.getLogger("t")) == (0, 0)
        processor.get_records_in_record_group.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_delete_does_not_stop_the_rest(self):
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock(return_value=[
            self.record("a", "b1/x/1.pdf"), self.record("b", "b1/x/2.pdf"),
        ])
        processor.on_record_deleted = AsyncMock(side_effect=[Exception("db"), None])

        removed = await remove_records_outside_scope(
            processor, "c", "b1", FolderScope(("keep/",)), logging.getLogger("t")
        )

        assert processor.on_record_deleted.await_count == 2
        assert removed == CleanupResult(removed=1, failed=1)


class TestCleanUpScope:
    """The cleanup runs until a scope is cleaned without a failed delete, then not again."""

    @staticmethod
    def sync_points(saved=None):
        saved = {} if saved is None else saved
        sync_points = MagicMock()
        sync_points.read_sync_point = AsyncMock(side_effect=lambda key: saved.get(key))
        sync_points.update_sync_point = AsyncMock(side_effect=lambda key, data: saved.__setitem__(key, data))
        return sync_points, saved

    @staticmethod
    def processor(delete_side_effect=None):
        processor = MagicMock()
        processor.get_records_in_record_group = AsyncMock(side_effect=lambda *a: [
            MagicMock(id="r1", external_record_id="b1/other/x.pdf", mime_type="application/pdf"),
        ])
        processor.on_record_deleted = AsyncMock(side_effect=delete_side_effect)
        return processor

    @pytest.mark.asyncio
    async def test_records_the_scope_once_cleaned_and_skips_it_after(self):
        sync_points, saved = self.sync_points()
        processor = self.processor()
        scope = FolderScope(("reports/",))

        await clean_up_scope(processor, sync_points, "c", "b1", scope, logging.getLogger("t"))
        await clean_up_scope(processor, sync_points, "c", "b1", scope, logging.getLogger("t"))

        assert saved == {"FILE/folder_scope/b1": {"scope": scope.key()}}
        processor.get_records_in_record_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failed_delete_leaves_the_scope_unrecorded_so_it_retries(self):
        sync_points, saved = self.sync_points()
        processor = self.processor(delete_side_effect=[Exception("db"), None])
        scope = FolderScope(("reports/",))

        await clean_up_scope(processor, sync_points, "c", "b1", scope, logging.getLogger("t"))
        assert saved == {}
        await clean_up_scope(processor, sync_points, "c", "b1", scope, logging.getLogger("t"))

        assert processor.on_record_deleted.await_count == 2
        assert saved == {"FILE/folder_scope/b1": {"scope": scope.key()}}

    @staticmethod
    def real_processor(group_lookup=None, pages=None):
        """A real DataSourceEntitiesProcessor over a mocked store.

        Paging goes through the processor's own ``get_records_in_record_group``,
        which looks the record group up again on every page. A mock on the
        processor would skip that lookup, and the lookup is where a failure
        turns into an empty page.
        """
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        tx = AsyncMock()
        tx.get_record_group_by_external_id = AsyncMock(
            side_effect=group_lookup or (lambda **kw: MagicMock(id="rg-key"))
        )
        tx.get_records_by_status = AsyncMock(side_effect=pages or (lambda **kw: []))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        store = MagicMock()
        store.transaction.return_value = ctx
        processor = DataSourceEntitiesProcessor(MagicMock(), store, AsyncMock())
        processor.org_id = "org-1"
        processor.messaging_producer = AsyncMock()
        processor.on_record_deleted = AsyncMock()
        return processor

    @pytest.mark.asyncio
    async def test_a_lookup_that_could_not_be_read_does_not_record_the_scope(self):
        """The failure arrives where the record group is looked up, not at the listing.

        That lookup used to answer None on a database failure, which reads as
        "there is no such group", so the page came back empty, the bucket looked
        clean, and the scope was written. Every later sync then skipped it, and
        records outside the chosen folders stayed indexed for ever.
        """
        from app.exceptions.graph_db_exceptions import GraphQueryError

        sync_points, saved = self.sync_points()
        processor = self.real_processor(
            group_lookup=GraphQueryError("connection refused")
        )
        scope = FolderScope(("reports/",))

        with pytest.raises(GraphQueryError):
            await clean_up_scope(
                processor, sync_points, "c", "b1", scope, logging.getLogger("t")
            )

        assert saved == {}
        sync_points.update_sync_point.assert_not_awaited()
        processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_that_could_not_be_read_does_not_record_the_scope(self):
        """A full first page, then a failure: the scope is not finished."""
        from app.exceptions.graph_db_exceptions import GraphQueryError

        sync_points, saved = self.sync_points()
        full_page = [
            MagicMock(id=f"r{n}", external_record_id=f"b1/reports/{n}.pdf",
                      mime_type="application/pdf")
            for n in range(_PAGE_SIZE)
        ]
        processor = self.real_processor(
            pages=[full_page, GraphQueryError("connection refused")]
        )
        scope = FolderScope(("reports/",))

        with pytest.raises(GraphQueryError):
            await clean_up_scope(
                processor, sync_points, "c", "b1", scope, logging.getLogger("t")
            )

        assert saved == {}
        sync_points.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_again_when_the_scope_differs_from_the_recorded_one(self):
        sync_points, saved = self.sync_points(
            {"FILE/folder_scope/b1": {"scope": FolderScope(("reports/",)).key()}}
        )
        processor = self.processor()
        scope = FolderScope(("reports/",), exclude=True)

        await clean_up_scope(processor, sync_points, "c", "b1", scope, logging.getLogger("t"))

        processor.get_records_in_record_group.assert_awaited_once()
        assert saved["FILE/folder_scope/b1"] == {"scope": scope.key()}

    @pytest.mark.asyncio
    async def test_does_nothing_without_a_folder_filter(self):
        sync_points, saved = self.sync_points()
        processor = self.processor()

        await clean_up_scope(processor, sync_points, "c", "b1", FolderScope(), logging.getLogger("t"))

        processor.get_records_in_record_group.assert_not_awaited()
        sync_points.read_sync_point.assert_not_awaited()

    def test_scope_keys_tell_include_from_exclude(self):
        assert FolderScope(("a/", "b/")).key() == FolderScope(("b/", "a/")).key()
        # A folder name holding the separator must not collide with two folders.
        assert FolderScope(("a/", "b/")).key() != FolderScope(("a/|b/",)).key()
        assert FolderScope(("a/", "b/")).key() != FolderScope(("a/", "b/"), exclude=True).key()


class TestFilterField:
    def test_is_a_free_text_list_defaulting_to_include(self):
        from app.connectors.core.registry.connector_builder import CommonFields
        from app.connectors.core.registry.filters import FilterCategory, OptionSourceType

        field = CommonFields.folder_paths_filter("container")
        assert field.name == "folder_paths"
        assert field.filter_type == FilterType.LIST
        assert field.category == FilterCategory.SYNC
        assert field.option_source_type == OptionSourceType.MANUAL
        assert field.default_operator == ListOperator.IN.value
        assert "container" in field.description
