"""PP-341: Azure Files sync, security and performance fixes.

Hermetic: the real connector runs against an in-memory share tree (FakeDataSource)
and an in-memory record store (FakeProcessor), so each test drives the code path
the ticket names rather than mocking it away.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    MultiselectOperator,
)
from app.connectors.sources.azure_files.connector import AzureFilesConnector
from app.sources.client.azure.azure_files import AzureFilesResponse

CONNECTOR_ID = "az-files-pp341"
LIST_TIME = datetime(2024, 5, 1, 10, 0, 0, 123456, tzinfo=timezone.utc)


def _ok(data: object) -> AzureFilesResponse:
    return AzureFilesResponse(success=True, data=data)


def _err(error: str, code: str | None = None) -> AzureFilesResponse:
    return AzureFilesResponse(success=False, error=error, error_code=code)


class FakeDataSource:
    """Shares as {share: {path: item}}; directories are items with is_directory=True."""

    def __init__(self, shares) -> None:
        self.shares = shares
        self.fail_dirs: set[tuple[str, str]] = set()
        # (share, directory, page offset) whose page listing fails.
        self.fail_pages: set[tuple[str, str, int]] = set()
        self.fail_list_shares = False
        self.listed: list[tuple[str, str]] = []

    def add(self, share, path, *, is_dir=False, file_id=None, size=10, written=LIST_TIME) -> None:
        self.shares.setdefault(share, {})[path] = {
            "name": path.rsplit("/", 1)[-1],
            "path": path,
            "is_directory": is_dir,
            "file_id": file_id or f"id-{share}-{path}",
            "size": None if is_dir else size,
            "last_modified": written,
            "last_write_time": written,
            "creation_time": written,
            "etag": f'"etag-{path}"',
        }

    async def list_shares(self, **kwargs: object) -> AzureFilesResponse:
        if self.fail_list_shares:
            return _err("Azure Files API error: AuthenticationFailed", "AuthenticationFailed")
        return _ok([{"name": s, "last_modified": LIST_TIME} for s in self.shares])

    async def list_directories_and_files(
        self,
        share_name: str,
        directory_path: str = "",
        name_starts_with: str | None = None,
        results_per_page: int | None = None,
        continuation_token: str | None = None,
    ) -> AzureFilesResponse:
        self.listed.append((share_name, directory_path))
        if (share_name, directory_path) in self.fail_dirs:
            return _err("Azure Files API error: ServerBusy", "ServerBusy")
        tree = self.shares.get(share_name)
        if tree is None:
            return _err(f"Directory not found: {share_name}/{directory_path}", "ShareNotFound")
        if directory_path and directory_path not in tree:
            return _err(f"Directory not found: {share_name}/{directory_path}", "ResourceNotFound")
        items = []
        for path, item in tree.items():
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent == directory_path:
                items.append(dict(item))
        if not results_per_page:
            return _ok(items)
        # The marker is the offset of the page, as opaque to the connector as Azure's.
        start = int(continuation_token or 0)
        if (share_name, directory_path, start) in self.fail_pages:
            return _err("Azure Files API error: ServerBusy", "ServerBusy")
        response = _ok(items[start:start + results_per_page])
        if start + results_per_page < len(items):
            response.continuation_token = str(start + results_per_page)
        return response

    async def get_share_properties(self, share_name: str) -> AzureFilesResponse:
        if share_name not in self.shares:
            return _err(f"Share not found: {share_name}", "ShareNotFound")
        return _ok({"name": share_name})

    async def get_file_properties(self, share_name: str, file_path: str) -> AzureFilesResponse:
        item = self.shares.get(share_name, {}).get(file_path)
        if not item:
            return _err(f"File not found: {share_name}/{file_path}", "ResourceNotFound")
        # The SDK parses the properties headers into naive datetimes.
        naive = item["last_write_time"].replace(tzinfo=None)
        return _ok({**item, "last_modified": naive, "last_write_time": naive, "creation_time": naive})

    async def close_async_client(self) -> None:
        return None


class FakeProcessor:
    """Records by external id, with the lookups the connector makes."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.records: dict[str, object] = {}
        self.new_calls: list[list] = []
        self.moved_calls: list[list] = []
        self.deleted: list[str] = []
        self.kept: list[str] = []
        self.groups: set[str] = set()
        self.full_sync = False
        # The graph does not order get_nodes_by_filters.
        self.reverse_nodes = False

    def _store(self, record) -> None:
        for ext, existing in list(self.records.items()):
            if existing.id == record.id and ext != record.external_record_id:
                del self.records[ext]
        self.records[record.external_record_id] = record

    async def on_new_records(self, batch) -> None:
        self.new_calls.append([r.external_record_id for r, _ in batch])
        for record, _ in batch:
            self._store(record)

    async def on_records_moved(self, moves, **kwargs) -> None:
        self.moved_calls.append([(old, r.external_record_id) for old, r, _ in moves])
        for old, record, _ in moves:
            self.records.pop(old, None)
            self._store(record)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> object:
        return self.records.get(external_id)

    async def get_record_by_external_revision_id(self, connector_id: str, revision: str) -> object:
        # Like the graph query: any share, first match.
        return next((r for r in self.records.values() if r.external_revision_id == revision), None)

    async def get_records_by_record_type(self, connector_id: str, record_type: object) -> list:
        return list(self.records.values())

    async def get_nodes_by_filters(
        self, collection: str, filters: dict, return_fields: list | None = None, *, raise_on_error: bool = False
    ) -> list[dict]:
        if collection == CollectionNames.RECORD_GROUPS.value:
            return [{"externalGroupId": g} for g in sorted(self.groups)]
        assert collection == CollectionNames.RECORDS.value
        out = []
        for r in (reversed(list(self.records.values())) if self.reverse_nodes else self.records.values()):
            if "externalGroupId" in filters and r.external_record_group_id != filters["externalGroupId"]:
                continue
            out.append({
                "_key": r.id,
                "externalRecordId": r.external_record_id,
                "externalGroupId": r.external_record_group_id,
                "externalRevisionId": r.external_revision_id,
                "isDeleted": None,
            })
        return out

    def _delete_ids(self, ids) -> None:
        for ext, r in list(self.records.items()):
            if r.id in ids:
                del self.records[ext]
                self.deleted.append(ext)

    async def on_record_deleted(self, record_id) -> bool:
        self._delete_ids({record_id})
        return False

    async def on_records_deleted_cascade(self, record_ids: list, connector_id: str, **kwargs: object) -> dict:
        self._delete_ids(set(record_ids))
        return {"success": True}

    def note_kept_records(self, connector_id, record_ids) -> None:
        self.kept.extend(record_ids)

    def full_sync_running(self, connector_id: str) -> bool:
        return self.full_sync

    async def delete_parent_child_edge_to_record(self, record_id) -> int:
        return 0

    async def on_new_record_groups(self, groups) -> None:
        self.groups.update(g.external_group_id for g, _ in groups)

    async def on_record_group_deleted(self, external_group_id, connector_id) -> bool:
        self.groups.discard(external_group_id)
        self._delete_ids({r.id for r in self.records.values() if r.external_record_group_id == external_group_id})
        return True

    async def ensure_team_app_edge(self, connector_id) -> None:
        return None

    async def get_user_by_user_id(self, user_id) -> None:
        return None


def _filters(shares: list | None = None) -> FilterCollection:
    filters = []
    if shares is not None:
        filters.append(Filter(key="shares", value=shares, type=FilterType.MULTISELECT, operator=MultiselectOperator.IN))
    return FilterCollection(filters=filters)


def _connector(
    ds: FakeDataSource, proc: FakeProcessor, *, scope: str = "personal", shares: list | None = None
) -> AzureFilesConnector:
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        conn = AzureFilesConnector(
            logger=logging.getLogger("test.azure_files.pp341"),
            data_entities_processor=proc,
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            connector_id=CONNECTOR_ID,
            scope=scope,
            created_by="user-1",
        )
    conn.data_source = ds
    conn.account_name = "acct"
    conn.record_sync_point = SimpleNamespace(
        read_sync_point=AsyncMock(return_value=None), update_sync_point=AsyncMock()
    )
    conn._test_filters = _filters(shares)
    conn.notify = AsyncMock()
    return conn


async def _sync(conn) -> None:
    with patch(
        "app.connectors.sources.azure_files.connector.load_connector_filters",
        new=AsyncMock(return_value=(conn._test_filters, FilterCollection())),
    ):
        await conn.run_sync()


# ---------------------------------------------------------------------------
# PP-342 B1: an incomplete full sync must say so to the shared unlink
# ---------------------------------------------------------------------------
class TestB1IncompleteCoverage:
    async def test_failed_directory_listing_reports_incomplete(self) -> None:
        ds = FakeDataSource({})
        ds.add("docs", "A", is_dir=True)
        ds.add("docs", "A/a.txt")
        ds.add("docs", "b.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc)
        await _sync(conn)
        assert conn.full_sync_coverage().complete is True

        ds.fail_dirs.add(("docs", "A"))
        await _sync(conn)
        assert conn.full_sync_coverage().complete is False
        assert "docs/A/a.txt" in proc.records  # nor removed by the connector

    async def test_item_that_fails_to_process_reports_incomplete(self) -> None:
        ds = FakeDataSource({})
        ds.add("docs", "a.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc)
        with patch.object(conn, "_process_azure_files_item", AsyncMock(return_value=(None, []))):
            await _sync(conn)
        assert conn.full_sync_coverage().complete is False

    async def test_flag_resets_on_next_run(self) -> None:
        ds = FakeDataSource({})
        ds.add("docs", "a.txt")
        ds.fail_dirs.add(("docs", ""))
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["docs"])
        with pytest.raises(RuntimeError, match="No share could be read"):
            await _sync(conn)
        assert conn.full_sync_coverage().complete is False
        ds.fail_dirs.clear()
        await _sync(conn)
        assert conn.full_sync_coverage().complete is True


# ---------------------------------------------------------------------------
# PP-343 B2: per-share completeness; a selected share that is gone
# ---------------------------------------------------------------------------
class TestB2PerShareDeletes:
    async def test_gone_share_does_not_block_deletes_elsewhere(self) -> None:
        ds = FakeDataSource({})
        ds.add("keep", "secret.txt")
        ds.add("keep", "stay.txt")
        ds.add("gone", "old.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["keep", "gone"])
        await _sync(conn)
        assert {"keep/secret.txt", "keep/stay.txt", "gone/old.txt"} <= set(proc.records)

        del ds.shares["gone"]
        del ds.shares["keep"]["secret.txt"]
        await _sync(conn)
        assert "keep/secret.txt" not in proc.records
        assert "keep/stay.txt" in proc.records
        # The share itself is gone, so its records go too, and the admin is told.
        assert "gone/old.txt" not in proc.records
        assert any("gone" in str(c.kwargs.get("message", "")) for c in conn.notify.await_args_list)

    async def test_failing_share_keeps_its_records_but_others_delete(self) -> None:
        ds = FakeDataSource({})
        ds.add("keep", "secret.txt")
        ds.add("flaky", "f.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["keep", "flaky"])
        await _sync(conn)

        ds.fail_dirs.add(("flaky", ""))
        del ds.shares["flaky"]["f.txt"]
        del ds.shares["keep"]["secret.txt"]
        await _sync(conn)
        assert "keep/secret.txt" not in proc.records
        assert "flaky/f.txt" in proc.records  # unread, so kept
        assert conn.full_sync_coverage().complete is False

    async def test_records_of_share_taken_out_of_the_filter_are_removed(self) -> None:
        ds = FakeDataSource({})
        ds.add("a", "1.txt")
        ds.add("b", "2.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["a", "b"])
        await _sync(conn)
        conn._test_filters = _filters(["a"])
        await _sync(conn)
        assert "b/2.txt" not in proc.records
        assert "a/1.txt" in proc.records


# ---------------------------------------------------------------------------
# PP-344 B3: FileIds repeat across shares
# ---------------------------------------------------------------------------
class TestB3FileIdAcrossShares:
    async def test_same_file_id_in_two_shares_makes_two_records(self) -> None:
        ds = FakeDataSource({})
        ds.add("a-share", "Finance", is_dir=True, file_id="13835128424026341376")
        ds.add("b-share", "Docs", is_dir=True, file_id="13835128424026341376")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["a-share", "b-share"])
        await _sync(conn)
        await _sync(conn)
        assert {"a-share/Finance", "b-share/Docs"} <= set(proc.records)
        assert proc.records["a-share/Finance"].id != proc.records["b-share/Docs"].id
        assert proc.moved_calls == []

    async def test_two_copies_of_a_moved_file_get_one_record_each(self) -> None:
        import asyncio

        ds = FakeDataSource({})
        ds.add("s", "a.txt", file_id="7")
        for d in ("x", "y"):
            ds.add("s", d, is_dir=True, file_id=f"dir-{d}")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        original = proc.records["s/a.txt"].id
        item = ds.shares["s"].pop("a.txt")
        # Copied twice keeping FileId, size and time, original removed; the two
        # folders are walked at the same time.
        ds.add("s", "x/a.txt", file_id="7")
        ds.add("s", "y/a.txt", file_id="7")
        real = ds.list_directories_and_files

        async def slow(*args: object, **kwargs: object) -> AzureFilesResponse:
            await asyncio.sleep(0.01)
            return await real(*args, **kwargs)

        ds.list_directories_and_files = slow
        del item
        await _sync(conn)
        x, y = proc.records["s/x/a.txt"], proc.records["s/y/a.txt"]
        assert x.id != y.id
        assert original in {x.id, y.id}
        assert "s/a.txt" not in proc.records

    async def test_rename_is_found_when_another_share_holds_the_same_file_id(self) -> None:
        # The connector-wide revision lookup returns one row, which can be the
        # other share's; the move must still be found in this share.
        ds = FakeDataSource({})
        ds.add("a-share", "Finance", is_dir=True, file_id="13835128424026341376")
        ds.add("b-share", "Docs", is_dir=True, file_id="13835128424026341376")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["a-share", "b-share"])
        await _sync(conn)
        docs_id = proc.records["b-share/Docs"].id
        item = ds.shares["b-share"].pop("Docs")
        ds.shares["b-share"]["Papers"] = {**item, "name": "Papers", "path": "Papers"}
        await _sync(conn)
        assert proc.records["b-share/Papers"].id == docs_id
        assert proc.moved_calls == [[("b-share/Docs", "b-share/Papers")]]
        assert "a-share/Finance" in proc.records

    async def test_full_sync_still_finds_moves(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "old.txt", file_id="9")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        rid = proc.records["s/old.txt"].id
        item = ds.shares["s"].pop("old.txt")
        ds.shares["s"]["new.txt"] = {**item, "name": "new.txt", "path": "new.txt"}
        proc.full_sync = True
        await _sync(conn)
        assert proc.records["s/new.txt"].id == rid

    @pytest.mark.parametrize("reverse", [False, True])
    async def test_rename_beside_a_same_file_id_copy_keeps_both_records(self, reverse: bool) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt", file_id="7")
        ds.add("s", "b.txt", file_id="7")  # a copy that kept FileId, size and time
        proc = FakeProcessor()
        proc.reverse_nodes = reverse  # the survivor first, or the renamed one
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        a_id, b_id = proc.records["s/a.txt"].id, proc.records["s/b.txt"].id
        item = ds.shares["s"].pop("a.txt")
        ds.shares["s"]["c.txt"] = {**item, "name": "c.txt", "path": "c.txt"}
        await _sync(conn)
        assert proc.records["s/c.txt"].id == a_id
        assert proc.records["s/b.txt"].id == b_id
        assert "s/a.txt" not in proc.records
        assert proc.moved_calls == [[("s/a.txt", "s/c.txt")]]

    @pytest.mark.parametrize("reverse", [False, True])
    async def test_a_path_in_an_unread_folder_is_not_taken_as_moved(self, reverse: bool) -> None:
        ds = FakeDataSource({})
        for d in ("keep", "miss"):
            ds.add("s", d, is_dir=True, file_id=f"dir-{d}")
        ds.add("s", "keep/b.txt", file_id="7")
        ds.add("s", "miss/a.txt", file_id="7")
        proc = FakeProcessor()
        proc.reverse_nodes = reverse
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        a_id, b_id = proc.records["s/miss/a.txt"].id, proc.records["s/keep/b.txt"].id
        ds.fail_dirs.add(("s", "miss"))
        ds.add("s", "keep/c.txt", file_id="7")
        await _sync(conn)
        assert proc.records["s/miss/a.txt"].id == a_id
        assert proc.records["s/keep/b.txt"].id == b_id
        assert proc.records["s/keep/c.txt"].id not in {a_id, b_id}
        assert proc.moved_calls == []

        # Once "miss" is read again and a.txt is really gone, the next rename still moves.
        ds.fail_dirs.clear()
        del ds.shares["s"]["miss/a.txt"]
        ds.add("s", "keep/d.txt", file_id="8")
        await _sync(conn)
        assert "s/miss/a.txt" not in proc.records

    async def test_renamed_folder_keeps_its_files_records_beside_a_failed_listing(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "dir", is_dir=True, file_id="d1")
        ds.add("s", "dir/a.txt", file_id="f1")
        ds.add("s", "dir/sub", is_dir=True, file_id="d2")
        ds.add("s", "dir/sub/b.txt", file_id="f2")
        ds.add("s", "other", is_dir=True, file_id="d3")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        before = {k: proc.records[k].id for k in ("s/dir", "s/dir/a.txt", "s/dir/sub", "s/dir/sub/b.txt")}
        for old_path in ["dir", "dir/a.txt", "dir/sub", "dir/sub/b.txt"]:
            item = ds.shares["s"].pop(old_path)
            new_path = "dir2" + old_path[3:]
            ds.shares["s"][new_path] = {**item, "path": new_path, "name": new_path.rsplit("/", 1)[-1]}
        ds.fail_dirs.add(("s", "other"))
        await _sync(conn)
        for old_id, new_path in zip(before.values(), ("s/dir2", "s/dir2/a.txt", "s/dir2/sub", "s/dir2/sub/b.txt")):
            assert proc.records[new_path].id == old_id, new_path
        assert not [k for k in proc.records if k.startswith("s/dir/")]

    async def test_copy_keeping_file_id_in_same_share_is_not_a_move(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt", file_id="7")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        ds.add("s", "b.txt", file_id="7")  # same id, size and time: a copied tree
        await _sync(conn)
        assert {"s/a.txt", "s/b.txt"} <= set(proc.records)
        assert proc.records["s/a.txt"].id != proc.records["s/b.txt"].id


# ---------------------------------------------------------------------------
# PP-345 B4 / PP-346 B5: exact names
# ---------------------------------------------------------------------------
class TestB4B5Names:
    def test_percent_name_is_not_decoded(self) -> None:
        conn = _connector(FakeDataSource({}), FakeProcessor())
        record = SimpleNamespace(id="r", external_record_group_id="s", external_record_id="s/dir/100%25.pdf")
        assert conn._extract_file_path_info(record) == ("s", "dir/100%25.pdf")
        record.external_record_id = "s/a%20b.txt"
        assert conn._extract_file_path_info(record) == ("s", "a%20b.txt")

    async def test_leading_and_trailing_spaces_are_kept(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", " notes.txt", file_id="1")
        ds.add("s", "notes.txt", file_id="2")
        ds.add("s", "dir ", is_dir=True, file_id="3")
        ds.add("s", "dir / x.txt", file_id="4")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        assert {"s/ notes.txt", "s/notes.txt", "s/dir ", "s/dir / x.txt"} <= set(proc.records)
        assert proc.records["s/ notes.txt"].record_name == " notes.txt"
        assert proc.records["s/dir / x.txt"].parent_external_record_id == "s/dir "
        await _sync(conn)
        assert {"s/ notes.txt", "s/notes.txt"} <= set(proc.records)


# ---------------------------------------------------------------------------
# PP-359 O5: a rename goes through on_records_moved
# ---------------------------------------------------------------------------
class TestO5Moves:
    async def test_rename_uses_records_moved(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "old.txt", file_id="9")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        rid = proc.records["s/old.txt"].id
        item = ds.shares["s"].pop("old.txt")
        ds.shares["s"]["new.txt"] = {**item, "name": "new.txt", "path": "new.txt"}
        await _sync(conn)
        assert proc.moved_calls == [[("s/old.txt", "s/new.txt")]]
        assert proc.records["s/new.txt"].id == rid
        assert "s/old.txt" not in proc.records


# ---------------------------------------------------------------------------
# PP-360 O6 / PP-361 O7: failures fail the run and tell the admin
# ---------------------------------------------------------------------------
class TestEmptyAccount:
    async def test_shares_gone_from_an_empty_account_are_removed(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc)
        await _sync(conn)
        assert "s/a.txt" in proc.records
        ds.shares.clear()
        await _sync(conn)
        assert proc.records == {} and proc.groups == set()


class TestO6O7Failures:
    async def test_failed_list_shares_raises_and_notifies(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.fail_list_shares = True
        conn = _connector(ds, FakeProcessor())
        # The fake refuses with AuthenticationFailed: the owner is told to fix the credentials.
        with pytest.raises(ConnectionError, match="Listing file shares was refused"):
            await _sync(conn)
        conn.notify.assert_awaited()
        assert conn.display_name in conn.notify.await_args.kwargs["message"]
        assert "Credentials rejected" in conn.notify.await_args.kwargs["title"]

    async def test_every_share_failing_raises(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.fail_dirs.add(("s", ""))
        conn = _connector(ds, FakeProcessor(), shares=["s"])
        with pytest.raises(RuntimeError, match="No share could be read"):
            await _sync(conn)

    async def test_partial_failure_notifies(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.add("t", "b.txt")
        ds.fail_dirs.add(("t", ""))
        conn = _connector(ds, FakeProcessor(), shares=["s", "t"])
        await _sync(conn)
        conn.notify.assert_awaited()


# ---------------------------------------------------------------------------
# PP-363 O9: same revision from a listing and from get_file_properties
# ---------------------------------------------------------------------------
class TestO9Revision:
    def test_list_and_properties_revisions_match(self) -> None:
        conn = _connector(FakeDataSource({}), FakeProcessor())
        listed = {"file_id": "1", "size": 5, "last_write_time": LIST_TIME, "is_directory": False}
        props = {**listed, "last_write_time": LIST_TIME.replace(tzinfo=None)}
        assert conn._get_azure_files_revision_id(listed) == conn._get_azure_files_revision_id(props)

    async def test_reindex_of_unchanged_file_is_not_an_update(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        assert await conn._check_and_fetch_updated_record("org-1", proc.records["s/a.txt"]) is None


# ---------------------------------------------------------------------------
# PP-352 P2: unchanged items are not rewritten on a scheduled run
# ---------------------------------------------------------------------------
class TestP2SkipUnchanged:
    async def test_second_run_writes_nothing_when_nothing_changed(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "d", is_dir=True)
        ds.add("s", "d/a.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        proc.new_calls.clear()
        await _sync(conn)
        assert proc.new_calls == []
        assert {"s/d", "s/d/a.txt"} <= set(proc.records)

    async def test_full_sync_rewrites_and_changed_item_is_written(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.add("s", "b.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        proc.new_calls.clear()
        ds.shares["s"]["b.txt"]["size"] = 99
        await _sync(conn)
        assert proc.new_calls == [["s/b.txt"]]
        proc.new_calls.clear()
        proc.full_sync = True
        await _sync(conn)
        assert sorted(sum(proc.new_calls, [])) == ["s/a.txt", "s/b.txt"]


# ---------------------------------------------------------------------------
# PP-354 P4: stale records are deleted in batches
# ---------------------------------------------------------------------------
class TestP4BatchDelete:
    async def test_stale_records_deleted_in_one_batch(self) -> None:
        ds = FakeDataSource({})
        for i in range(5):
            ds.add("s", f"f{i}.txt")
        proc = FakeProcessor()
        proc.on_records_deleted_cascade = AsyncMock(wraps=proc.on_records_deleted_cascade)
        proc.on_record_deleted = AsyncMock(wraps=proc.on_record_deleted)
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        ds.shares["s"].clear()
        await _sync(conn)
        assert proc.on_records_deleted_cascade.await_count == 1
        proc.on_record_deleted.assert_not_awaited()
        assert not [k for k in proc.records if k.startswith("s/")]


# ---------------------------------------------------------------------------
# PP-347 S1: a team connector must name its shares
# ---------------------------------------------------------------------------
class TestS1TeamScope:
    async def test_team_connector_without_share_filter_refuses(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team")
        with pytest.raises(ValueError, match="team connector"):
            await _sync(conn)
        assert proc.records == {}
        conn.notify.assert_awaited()

    async def test_team_connector_with_a_not_in_filter_refuses(self) -> None:
        # Every share but a few, including ones created later, is still org-wide.
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.add("other", "b.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team")
        conn._test_filters = FilterCollection(filters=[
            Filter(key="shares", value=["other"], type=FilterType.MULTISELECT, operator=MultiselectOperator.NOT_IN),
        ])
        with pytest.raises(ValueError, match="team connector"):
            await _sync(conn)
        assert proc.records == {}

    async def test_personal_connector_honours_not_in(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.add("other", "b.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc)
        conn._test_filters = FilterCollection(filters=[
            Filter(key="shares", value=["other"], type=FilterType.MULTISELECT, operator=MultiselectOperator.NOT_IN),
        ])
        await _sync(conn)
        assert "s/a.txt" in proc.records and "other/b.txt" not in proc.records

    async def test_team_connector_with_share_filter_syncs(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.add("other", "b.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team", shares=["s"])
        await _sync(conn)
        assert "s/a.txt" in proc.records and "other/b.txt" not in proc.records


# ---------------------------------------------------------------------------
# PP-353 P3: directories are listed concurrently, but boundedly
# ---------------------------------------------------------------------------
class TestP3BoundedConcurrency:
    async def test_listings_overlap_up_to_the_limit(self) -> None:
        import asyncio

        from app.connectors.sources.azure_files import connector as module

        ds = FakeDataSource({})
        for i in range(20):
            ds.add("s", f"d{i}", is_dir=True)
            ds.add("s", f"d{i}/f.txt")
        active = peak = 0
        real = ds.list_directories_and_files

        async def slow_listing(*args: object, **kwargs: object) -> AzureFilesResponse:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return await real(*args, **kwargs)

        ds.list_directories_and_files = slow_listing
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        assert 1 < peak <= module.DIRECTORY_CONCURRENCY
        assert {f"s/d{i}/f.txt" for i in range(20)} <= set(proc.records)
        # Folders are written before the files inside them.
        order = sum(proc.new_calls, [])
        assert all(order.index(f"s/d{i}") < order.index(f"s/d{i}/f.txt") for i in range(20))


# ---------------------------------------------------------------------------
# PP-355 O1 / PP-347 S1 / PP-348 S2 / PP-349 S3 / PP-350 S4: registry metadata
# ---------------------------------------------------------------------------
class TestRegistryMetadata:
    def test_declares_resilience_info_and_auth_types(self) -> None:
        meta = AzureFilesConnector._connector_metadata
        assert meta["supportedAuthTypes"] == ["CONNECTION_STRING", "ENTRA_ID"]
        assert meta["resilienceConfig"]["rate_limit"] == 50
        info = meta["connectorInfo"]
        for phrase in ("Read and List", "NTFS", "team connector", "full read, write and delete"):
            assert phrase in info
        schemas = meta["config"]["auth"]["schemas"]
        entra = [f["name"] for f in schemas["ENTRA_ID"]["fields"]]
        assert entra == ["accountUrl", "tenantId", "clientId", "clientSecret"]
        conn_field = schemas["CONNECTION_STRING"]["fields"][0]
        assert "SharedAccessSignature" in conn_field["placeholder"]
        assert "AccountKey" not in conn_field["placeholder"]


# ---------------------------------------------------------------------------
# PP-356 O2: the connection test checks what a sync needs
# ---------------------------------------------------------------------------
class TestO2ConnectionTest:
    async def test_passes_with_list_and_read(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = _filters(["s"])
        assert await conn.test_connection_and_access() is True

    async def test_missing_selected_share_warns_but_does_not_block_the_connector(self) -> None:
        # Enabling sync runs this test; failing here would stop the sync that removes
        # a gone share's files (PP-343).
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.get_file_properties = AsyncMock(wraps=ds.get_file_properties)
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = _filters(["gone", "s"])
        assert await conn.test_connection_and_access() is True
        ds.get_file_properties.assert_awaited_once_with("s", "a.txt")

    async def test_list_only_sas_fails_on_read(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.get_file_properties = AsyncMock(return_value=_err("AuthorizationPermissionMismatch", "AuthorizationPermissionMismatch"))
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = FilterCollection()
        with pytest.raises(ConnectorInitError, match="needs the Read permission"):
            await conn.test_connection_and_access()
        ds.get_file_properties.assert_awaited_once_with("s", "a.txt")

    async def test_read_is_checked_on_a_folder_when_the_root_has_no_file(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "d", is_dir=True)
        ds.add("s", "d/a.txt")
        ds.get_directory_properties = AsyncMock(return_value=_err("AuthorizationPermissionMismatch", "AuthorizationPermissionMismatch"))
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = _filters(["s"])
        with pytest.raises(ConnectorInitError, match="needs the Read permission"):
            await conn.test_connection_and_access()
        ds.get_directory_properties.assert_awaited_once_with("s", "d")

    async def test_directory_listing_refused_fails(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.list_directories_and_files = AsyncMock(return_value=_err(
            "AuthorizationPermissionMismatch (HTTP 403): This request is not authorized.",
            "AuthorizationPermissionMismatch",
        ))
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = FilterCollection()
        with pytest.raises(ConnectorInitError, match="needs the List permission"):
            await conn.test_connection_and_access()

    async def test_transient_failure_names_no_permission(self) -> None:
        """A busy service is not a missing permission; telling the admin to change one misleads."""
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.fail_dirs.add(("s", ""))
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = FilterCollection()
        with pytest.raises(ConnectorInitError, match="Could not list share s. Azure said: .*ServerBusy") as refused:
            await conn.test_connection_and_access()
        assert "permission" not in str(refused.value)

    async def test_share_sas_needs_no_list_shares(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "a.txt")
        ds.fail_list_shares = True
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = _filters(["s"])
        assert await conn.test_connection_and_access() is True


# ---------------------------------------------------------------------------
# PP-351 P1: SAS URLs are short-lived, and a missing key is not an error
# ---------------------------------------------------------------------------
class TestP1SignedUrl:
    async def test_sas_expiry_is_minutes(self) -> None:
        from datetime import timedelta

        ds = FakeDataSource({})
        ds.generate_file_sas_url = AsyncMock(return_value=_ok({"sas_url": "https://x"}))
        conn = _connector(ds, FakeProcessor())
        record = SimpleNamespace(id="r", external_record_group_id="s", external_record_id="s/a.txt")
        assert await conn.get_signed_url(record) == "https://x"
        expiry = ds.generate_file_sas_url.await_args.kwargs["expiry"]
        assert expiry - datetime.now(timezone.utc) <= timedelta(minutes=5)

    async def test_no_key_is_logged_at_debug(self, caplog) -> None:
        ds = FakeDataSource({})
        ds.generate_file_sas_url = AsyncMock(return_value=_err("Account key required for SAS URL generation"))
        conn = _connector(ds, FakeProcessor())
        record = SimpleNamespace(id="r", external_record_group_id="s", external_record_id="s/a.txt")
        with caplog.at_level(logging.DEBUG):
            assert await conn.get_signed_url(record) is None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# PP-357 O3: URLs come from the account's endpoint
# ---------------------------------------------------------------------------
class TestO3Urls:
    def test_web_url_uses_the_client_endpoint(self) -> None:
        conn = _connector(FakeDataSource({}), FakeProcessor())
        conn.account_name = None
        conn.account_url = "https://acct.file.core.chinacloudapi.cn"
        assert conn._generate_web_url("s", "d/a b.txt") == "https://acct.file.core.chinacloudapi.cn/s/d/a%20b.txt"
        assert "None" not in conn._generate_directory_url("s", "")


class TestB1SharedUnlink:
    async def test_shared_unlink_is_skipped_after_a_failed_listing(self) -> None:
        """The step in sync_runner that removed the links of unwritten records."""
        from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
        from app.connectors.core.sync.sync_runner import _unlink_what_full_sync_missed

        ds = FakeDataSource({})
        ds.add("docs", "A", is_dir=True)
        ds.add("docs", "A/a.txt")
        ds.add("docs", "b.txt")
        ds.fail_dirs.add(("docs", "A"))
        conn = _connector(ds, FakeProcessor(), shares=["docs"])
        await _sync(conn)
        graph = MagicMock()
        graph.unlink_nodes_missed_by_full_sync = AsyncMock(return_value=(3, True))
        seen = FullSyncSeen()
        seen.add(CollectionNames.RECORDS.value, "some-record")
        # Nothing is unlinked; the first such run owes one retry full sync (False).
        with patch("app.connectors.core.sync.sync_runner._INCOMPLETE_FULL_SYNC_RETRYING", set()):
            assert await _unlink_what_full_sync_missed(conn, CONNECTOR_ID, graph, conn.logger, seen) is False
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
