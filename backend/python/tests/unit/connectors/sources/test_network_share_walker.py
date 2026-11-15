# ruff: noqa: ANN201, ANN202
"""ShareWalker tests against an in-memory INetworkShareDataSource."""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.registry.filters import (
    DatetimeOperator,
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    AccessDeniedError,
    DirectoryListingError,
    NetworkShareAuthError,
)
from app.connectors.sources.network_share.record_mapper import RecordMapper, revision_id
from app.connectors.sources.network_share.walker import ShareWalker
from app.models.entities import Record, RecordType

if TYPE_CHECKING:
    from app.models.entities import FileRecord

SHARE = "docs"
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
NEW = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _entry(
    name: str,
    *,
    is_directory: bool = False,
    is_symlink: bool = False,
    is_reparse: bool = False,
    size: int = 4,
    file_id: int | None = None,
    created_time: datetime | None = NEW,
    last_write_time: datetime | None = NEW,
) -> DirectoryEntry:
    return DirectoryEntry(
        name=name,
        is_directory=is_directory,
        is_symlink=is_symlink,
        is_reparse=is_reparse,
        size=size,
        created_time=created_time,
        last_write_time=last_write_time,
        file_id=file_id,
    )


class FakeNetworkShareDataSource:
    def __init__(
        self,
        tree: dict[tuple[str, str], list[DirectoryEntry]] | None = None,
        shares: list[ShareInfo] | Exception | None = None,
        fail_dirs: set[tuple[str, str]] | None = None,
        files: dict[tuple[str, str], bytes | BaseException] | None = None,
        stats: dict[tuple[str, str], DirectoryEntry | None] | None = None,
        denied_dirs: set[tuple[str, str]] | None = None,
        refused_dirs: set[tuple[str, str]] | None = None,
    ) -> None:
        self.tree = tree or {}
        self.shares = shares if shares is not None else [ShareInfo(name=SHARE, share_type="disk")]
        self.fail_dirs = fail_dirs or set()
        self.denied_dirs = denied_dirs or set()
        # Listing these raises what a refused sign-in raises (the server rejected the account).
        self.refused_dirs = refused_dirs or set()
        self.files = files or {}
        self.stats = stats or {}
        self.closed = False
        self.list_calls: list[tuple[str, str]] = []

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.list_calls.append((share, path))
        if (share, path) in self.fail_dirs:
            raise DirectoryListingError(share, path, "listing failed")
        if (share, path) in self.denied_dirs:
            raise AccessDeniedError(share, path, "STATUS_ACCESS_DENIED")
        if (share, path) in self.refused_dirs:
            raise NetworkShareAuthError("files.example refused the user name or password.")
        return list(self.tree.get((share, path), []))

    async def read_file(self, share: str, path: str, chunk_size: int = 8192):
        payload = self.files.get((share, path), b"")
        if isinstance(payload, BaseException):
            raise payload
        yield payload

    async def list_shares(self) -> list[ShareInfo]:
        if isinstance(self.shares, Exception):
            raise self.shares
        return list(self.shares)

    async def stat(
        self, share: str, path: str, *, follow: bool = True
    ) -> DirectoryEntry | None:
        if (share, path) in self.stats:
            return self.stats[(share, path)]
        for entries in self.tree.values():
            for item in entries:
                if item.name == path.rsplit("/", 1)[-1]:
                    return item
        return None

    async def close(self) -> None:
        self.closed = True


def _filters(*filters: Filter) -> FilterCollection:
    return FilterCollection(filters=list(filters))


def _folder_filter(*paths: str, exclude: bool = False) -> Filter:
    return Filter(
        key=SyncFilterKey.FOLDER_PATHS.value,
        value=list(paths),
        type=FilterType.LIST,
        operator=ListOperator.NOT_IN if exclude else ListOperator.IN,
    )


def _extension_filter(*exts: str) -> Filter:
    return Filter(
        key=SyncFilterKey.FILE_EXTENSIONS.value,
        value=list(exts),
        type=FilterType.MULTISELECT,
        operator=MultiselectOperator.IN,
    )


def _modified_after(when: datetime) -> Filter:
    return Filter(
        key=SyncFilterKey.MODIFIED.value,
        value={"start": int(when.timestamp() * 1000), "end": None},
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_AFTER,
    )


def _created_after(when: datetime) -> Filter:
    return Filter(
        key=SyncFilterKey.CREATED.value,
        value={"start": int(when.timestamp() * 1000), "end": None},
        type=FilterType.DATETIME,
        operator=DatetimeOperator.IS_AFTER,
    )


async def _walk(
    data_source: FakeNetworkShareDataSource,
    *,
    sync_filters: FilterCollection | None = None,
    batch_size: int = 100,
    existing_by_id: dict[str, FileRecord] | None = None,
    existing_by_revision: dict[str, FileRecord] | None = None,
    fail_ids: set[str] | None = None,
):
    upserts: list[list[tuple[FileRecord, list]]] = []
    moves: list[list[tuple[str, FileRecord, list]]] = []
    by_id = existing_by_id or {}
    by_rev = existing_by_revision or {}

    broken = fail_ids or set()

    async def get_by_id(ext_id: str):
        if ext_id in broken:
            raise RuntimeError("lookup failed")
        return by_id.get(ext_id)

    async def get_by_rev(rev: str):
        return by_rev.get(rev)

    async def flush_upserts(batch):
        upserts.append(list(batch))

    async def flush_moves(batch):
        moves.append(list(batch))

    walker = ShareWalker(
        data_source=data_source,
        mapper=RecordMapper(),
        logger=logging.getLogger("test.walker"),
        connector_name=Connectors.SMB,
        connector_id="smb-1",
        batch_size=batch_size,
        get_by_external_id=get_by_id,
        get_by_revision=get_by_rev,
        flush_upserts=flush_upserts,
        flush_moves=flush_moves,
        permissions_for=lambda _record: [],
    )
    result = await walker.walk_share(
        SHARE,
        sync_filters or FilterCollection(),
        FilterCollection(),
    )
    return result, upserts, moves


def _ids(upserts) -> set[str]:
    return {record.external_record_id for batch in upserts for record, _perms in batch}


def _stored(ext_id: str, revision: str, record_id: str) -> Record:
    return Record(
        id=record_id,
        record_name=ext_id.rsplit("/", 1)[-1],
        record_type=RecordType.FILE,
        external_record_id=ext_id,
        external_revision_id=revision,
        version=1,
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.SMB,
        connector_id="smb-1",
    )


class TestShareWalkerRenames:
    async def test_new_hard_link_walked_first_does_not_take_the_other_links_record(self):
        new_link = _entry("b.txt", file_id=7)
        kept_link = _entry("a.txt", file_id=7)
        rev = revision_id(SHARE, kept_link, "a.txt")
        assert rev == revision_id(SHARE, new_link, "b.txt")
        stored = _stored(f"{SHARE}/a.txt", rev, "kept")
        ds = FakeNetworkShareDataSource(tree={(SHARE, ""): [new_link, kept_link]})
        _result, upserts, moves = await _walk(
            ds,
            existing_by_id={stored.external_record_id: stored},
            existing_by_revision={rev: stored},
        )
        assert moves == []
        ids = {r.external_record_id: r.id for batch in upserts for r, _perms in batch}
        assert ids[f"{SHARE}/a.txt"] == "kept"
        assert ids[f"{SHARE}/b.txt"] != "kept"

    async def test_rename_whose_old_path_is_gone_is_still_a_move(self):
        renamed = _entry("b.txt", file_id=7)
        rev = revision_id(SHARE, renamed, "b.txt")
        stored = _stored(f"{SHARE}/a.txt", rev, "kept")
        ds = FakeNetworkShareDataSource(tree={(SHARE, ""): [renamed]})
        _result, upserts, moves = await _walk(ds, existing_by_revision={rev: stored})
        [(old_ext_id, record, _perms)] = [move for batch in moves for move in batch]
        assert (old_ext_id, record.id) == (f"{SHARE}/a.txt", "kept")
        assert f"{SHARE}/b.txt" not in _ids(upserts)


class TestShareWalkerLinkedFolders:
    """Samba lists a followed directory symlink as a plain folder with the target's file id."""

    async def test_link_back_to_an_enclosing_folder_is_not_walked(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry("policies", is_directory=True, file_id=3)],
                (SHARE, "policies"): [
                    _entry("leave.txt", file_id=4),
                    _entry("2026", is_directory=True, file_id=5),
                ],
                (SHARE, "policies/2026"): [_entry("up", is_directory=True, file_id=3)],
                (SHARE, "policies/2026/up"): [_entry("leave.txt", file_id=4)],
            }
        )
        result, upserts, moves = await _walk(ds)
        assert result.complete is True
        assert moves == []
        assert (SHARE, "policies/2026/up") not in ds.list_calls
        assert f"{SHARE}/policies/2026/up" not in result.seen
        assert _ids(upserts) == {
            f"{SHARE}/policies",
            f"{SHARE}/policies/leave.txt",
            f"{SHARE}/policies/2026",
        }

    async def test_link_back_to_the_share_root_is_not_walked(self):
        root = _entry("", is_directory=True, file_id=1)
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry("policies", is_directory=True, file_id=3)],
                (SHARE, "policies"): [_entry("top", is_directory=True, file_id=1)],
            },
            stats={(SHARE, ""): root},
        )
        result, _upserts, _moves = await _walk(ds)
        assert (SHARE, "policies/top") not in ds.list_calls
        assert f"{SHARE}/policies/top" not in result.seen

    async def test_with_a_folder_filter_a_link_to_the_chosen_folder_is_not_walked(self):
        chosen = _entry("policies", is_directory=True, file_id=3)
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, "policies"): [
                    _entry("leave.txt", file_id=4),
                    _entry("self", is_directory=True, file_id=3),
                    _entry("2026", is_directory=True, file_id=5),
                ],
                (SHARE, "policies/2026"): [_entry("up", is_directory=True, file_id=3)],
            },
            stats={(SHARE, "policies"): chosen},
        )
        result, _upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("policies")))
        assert result.complete is True
        assert (SHARE, "policies/self") not in ds.list_calls
        assert (SHARE, "policies/2026/up") not in ds.list_calls
        assert {f"{SHARE}/policies/self", f"{SHARE}/policies/2026/up"}.isdisjoint(result.seen)
        assert f"{SHARE}/policies/leave.txt" in result.seen

    async def test_with_a_folder_filter_a_link_to_a_folder_above_it_is_not_walked(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, "a/b"): [
                    _entry("in-scope.txt", file_id=4),
                    _entry("to-a", is_directory=True, file_id=2),
                ],
                (SHARE, "a/b/to-a"): [_entry("outside-the-filter.txt", file_id=6)],
            },
            stats={
                (SHARE, "a"): _entry("a", is_directory=True, file_id=2),
                (SHARE, "a/b"): _entry("b", is_directory=True, file_id=3),
            },
        )
        result, _upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("a/b")))
        assert (SHARE, "a/b/to-a") not in ds.list_calls
        assert f"{SHARE}/a/b/to-a/outside-the-filter.txt" not in result.seen
        assert f"{SHARE}/a/b/in-scope.txt" in result.seen

    async def test_one_chosen_folder_is_not_an_enclosing_folder_of_another(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, "x"): [_entry("x.txt", file_id=4)],
                (SHARE, "y"): [_entry("same-as-x", is_directory=True, file_id=3)],
                (SHARE, "y/same-as-x"): [_entry("y.txt", file_id=6)],
            },
            stats={
                (SHARE, "x"): _entry("x", is_directory=True, file_id=3),
                (SHARE, "y"): _entry("y", is_directory=True, file_id=7),
            },
        )
        result, _upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("x", "y")))
        assert f"{SHARE}/y/same-as-x/y.txt" in result.seen

    async def test_second_path_elsewhere_with_the_same_id_and_times_is_walked(self):
        # A snapshot or cloned volume inside the share matches a live folder exactly.
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("#snapshot", is_directory=True, file_id=9),
                    _entry("policies", is_directory=True, file_id=3),
                ],
                (SHARE, "#snapshot"): [_entry("policies", is_directory=True, file_id=3)],
                (SHARE, "#snapshot/policies"): [_entry("leave.txt", file_id=4)],
                (SHARE, "policies"): [_entry("leave.txt", file_id=4)],
            }
        )
        result, _upserts, _moves = await _walk(ds)
        assert result.complete is True
        assert {
            f"{SHARE}/policies",
            f"{SHARE}/policies/leave.txt",
            f"{SHARE}/#snapshot/policies",
            f"{SHARE}/#snapshot/policies/leave.txt",
        } <= result.seen

    async def test_new_second_path_does_not_take_the_recorded_folders_record(self):
        target = _entry("policies", is_directory=True, file_id=3)
        link = _entry("a-link", is_directory=True, file_id=3)
        rev = revision_id(SHARE, target, "policies")
        stored = _stored(f"{SHARE}/policies", rev, "kept")
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [link, target], (SHARE, "a-link"): [_entry("leave.txt", file_id=4)]}
        )
        result, upserts, moves = await _walk(
            ds,
            existing_by_id={stored.external_record_id: stored},
            existing_by_revision={rev: stored},
        )
        assert moves == []
        ids = {r.external_record_id: r.id for batch in upserts for r, _perms in batch}
        assert ids[f"{SHARE}/policies"] == "kept"
        assert ids[f"{SHARE}/a-link"] != "kept"
        assert f"{SHARE}/a-link/leave.txt" in result.seen

    async def test_renamed_folder_is_still_a_move(self):
        renamed = _entry("policies-2026", is_directory=True, file_id=3)
        rev = revision_id(SHARE, renamed, "policies-2026")
        stored = _stored(f"{SHARE}/policies", rev, "kept")
        ds = FakeNetworkShareDataSource(tree={(SHARE, ""): [renamed]})
        result, _upserts, moves = await _walk(ds, existing_by_revision={rev: stored})
        [(old_ext_id, record, _perms)] = [move for batch in moves for move in batch]
        assert (old_ext_id, record.id) == (f"{SHARE}/policies", "kept")
        assert (SHARE, "policies-2026") in ds.list_calls
        assert f"{SHARE}/policies-2026" in result.seen

    async def test_nested_mount_reusing_its_parents_inode_number_is_walked(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("disk-a", is_directory=True, file_id=2, created_time=OLD, last_write_time=OLD)
                ],
                (SHARE, "disk-a"): [_entry("disk-b", is_directory=True, file_id=2)],
                (SHARE, "disk-a/disk-b"): [_entry("b.txt", file_id=5)],
            }
        )
        result, _upserts, _moves = await _walk(ds)
        assert f"{SHARE}/disk-a/disk-b/b.txt" in result.seen

    async def test_folders_without_timestamps_are_never_taken_for_a_loop(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("a", is_directory=True, file_id=2, created_time=None, last_write_time=None)
                ],
                (SHARE, "a"): [
                    _entry("b", is_directory=True, file_id=2, created_time=None, last_write_time=None)
                ],
                (SHARE, "a/b"): [_entry("b.txt", file_id=5)],
            }
        )
        result, _upserts, _moves = await _walk(ds)
        assert f"{SHARE}/a/b/b.txt" in result.seen


class TestShareWalker:
    async def test_reparse_file_is_upserted_and_directory_reparse_is_not_walked(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("deduped.bin", file_id=8, is_reparse=True),
                    _entry("junction", is_directory=True, file_id=9, is_reparse=True),
                ],
                (SHARE, "junction"): [_entry("secret.txt", file_id=10)],
            }
        )
        result, upserts, _moves = await _walk(ds)
        assert f"{SHARE}/deduped.bin" in _ids(upserts)
        assert f"{SHARE}/junction" in result.seen
        assert (SHARE, "junction") not in ds.list_calls
        assert f"{SHARE}/junction/secret.txt" not in result.seen

    async def test_folder_filter_does_not_list_a_reparse_prefix(self):
        junction = _entry("junction", is_directory=True, file_id=9, is_reparse=True)
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, "junction"): [_entry("secret.txt", file_id=10)]},
            stats={(SHARE, "junction"): junction},
        )
        result, upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("junction"))
        )
        assert (SHARE, "junction") not in ds.list_calls
        assert f"{SHARE}/junction/secret.txt" not in result.seen
        assert f"{SHARE}/junction/secret.txt" not in _ids(upserts)
        assert result.complete is True

    async def test_folder_filter_does_not_list_through_a_reparse_ancestor(self):
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, "junction/nested"): [_entry("secret.txt", file_id=10)]},
            stats={
                (SHARE, "junction"): _entry(
                    "junction", is_directory=True, file_id=9, is_reparse=True
                ),
            },
        )
        result, _upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("junction/nested"))
        )
        assert (SHARE, "junction/nested") not in ds.list_calls
        assert f"{SHARE}/junction/nested/secret.txt" not in result.seen
        assert result.complete is True

    async def test_folder_filter_stat_failure_does_not_prune(self):
        class FailingStat(FakeNetworkShareDataSource):
            async def stat(
                self, share: str, path: str, *, follow: bool = True
            ) -> DirectoryEntry | None:
                raise DirectoryListingError(share, path, "stat failed")

        ds = FailingStat(tree={(SHARE, "keep"): [_entry("in.txt", file_id=4)]})
        result, _upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("keep"))
        )
        assert (SHARE, "keep") not in ds.list_calls
        assert result.complete is False
        assert result.unread == {f"{SHARE}/keep"}

    async def test_recurses_into_nested_directories(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("nested", is_directory=True, file_id=1),
                    _entry("root.txt", file_id=2),
                ],
                (SHARE, "nested"): [_entry("inner.txt", file_id=3)],
            }
        )
        result, upserts, _moves = await _walk(ds)
        assert result.complete is True
        assert {f"{SHARE}/nested", f"{SHARE}/root.txt", f"{SHARE}/nested/inner.txt"} <= result.seen
        assert _ids(upserts) == {f"{SHARE}/nested", f"{SHARE}/root.txt", f"{SHARE}/nested/inner.txt"}

    async def test_folder_scope_include_and_exclude(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("keep", is_directory=True, file_id=1),
                    _entry("drop", is_directory=True, file_id=2),
                    _entry("root.txt", file_id=3),
                ],
                (SHARE, "keep"): [_entry("in.txt", file_id=4)],
                (SHARE, "drop"): [_entry("out.txt", file_id=5)],
            }
        )
        included, upserts, _ = await _walk(ds, sync_filters=_filters(_folder_filter("keep")))
        assert f"{SHARE}/keep/in.txt" in included.seen
        assert f"{SHARE}/drop/out.txt" not in included.seen
        assert f"{SHARE}/root.txt" not in included.seen

        excluded, _up, _ = await _walk(ds, sync_filters=_filters(_folder_filter("drop", exclude=True)))
        assert f"{SHARE}/drop/out.txt" not in excluded.seen
        assert f"{SHARE}/keep/in.txt" in excluded.seen
        assert f"{SHARE}/root.txt" in excluded.seen
        assert _ids(upserts)

    async def test_extension_filter_keeps_ancestor_directories(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry("reports", is_directory=True, file_id=1)],
                (SHARE, "reports"): [
                    _entry("q1.pdf", file_id=2),
                    _entry("notes.txt", file_id=3),
                ],
            }
        )
        result, upserts, _ = await _walk(ds, sync_filters=_filters(_extension_filter("pdf")))
        ids = _ids(upserts)
        assert f"{SHARE}/reports" in ids
        assert f"{SHARE}/reports/q1.pdf" in ids
        assert f"{SHARE}/reports/notes.txt" not in ids
        assert f"{SHARE}/reports" in result.seen

    async def test_date_filter_does_not_drop_older_parent(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry("archive", is_directory=True, file_id=1, last_write_time=OLD)],
                (SHARE, "archive"): [
                    _entry("old.txt", file_id=2, last_write_time=OLD, created_time=OLD),
                    _entry("new.txt", file_id=3, last_write_time=NEW, created_time=NEW),
                ],
            }
        )
        cutoff = OLD + timedelta(days=400)
        result, upserts, _ = await _walk(ds, sync_filters=_filters(_modified_after(cutoff)))
        ids = _ids(upserts)
        assert f"{SHARE}/archive" in ids
        assert f"{SHARE}/archive/new.txt" in ids
        assert f"{SHARE}/archive/old.txt" not in ids
        assert f"{SHARE}/archive" in result.seen

    async def test_created_filter_uses_last_write_when_created_is_none(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("dated.txt", file_id=8, created_time=None, last_write_time=NEW),
                    _entry("stale.txt", file_id=9, created_time=None, last_write_time=OLD),
                ]
            }
        )
        cutoff = OLD + timedelta(days=400)
        _result, upserts, _ = await _walk(ds, sync_filters=_filters(_created_after(cutoff)))
        ids = _ids(upserts)
        assert f"{SHARE}/dated.txt" in ids
        assert f"{SHARE}/stale.txt" not in ids

    async def test_batches_including_short_final_batch(self):
        files = [_entry(f"f{i}.txt", file_id=i + 1) for i in range(5)]
        ds = FakeNetworkShareDataSource(tree={(SHARE, ""): files})
        _result, upserts, _ = await _walk(ds, batch_size=2)
        sizes = [len(batch) for batch in upserts]
        assert sizes == [2, 2, 1]

    async def test_failed_listing_marks_incomplete_and_walks_siblings(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("bad", is_directory=True, file_id=1),
                    _entry("good", is_directory=True, file_id=2),
                    _entry("ok.txt", file_id=3),
                ],
                (SHARE, "good"): [_entry("nested.txt", file_id=4)],
            },
            fail_dirs={(SHARE, "bad")},
        )
        result, upserts, _ = await _walk(ds)
        assert result.complete is False
        assert result.unread == {f"{SHARE}/bad"}
        ids = _ids(upserts)
        assert f"{SHARE}/ok.txt" in ids
        assert f"{SHARE}/good/nested.txt" in ids
        assert f"{SHARE}/ok.txt" in result.seen
        assert f"{SHARE}/good/nested.txt" in result.seen

    async def test_a_folder_the_account_may_not_list_is_synced_empty(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [
                    _entry("private", is_directory=True, file_id=1),
                    _entry("ok.txt", file_id=2),
                ],
                (SHARE, "private"): [_entry("secret.txt", file_id=3)],
            },
            denied_dirs={(SHARE, "private")},
        )
        result, upserts, _ = await _walk(ds)
        assert result.complete is True
        assert result.unread == set()
        assert f"{SHARE}/private" in result.seen
        assert f"{SHARE}/private/secret.txt" not in result.seen
        assert f"{SHARE}/ok.txt" in _ids(upserts)

    async def test_a_denied_share_root_is_unread(self):
        ds = FakeNetworkShareDataSource(denied_dirs={(SHARE, "")})
        result, _upserts, _ = await _walk(ds)
        assert result.complete is False
        assert result.unread == {SHARE}

    async def test_a_denied_chosen_folder_is_unread(self):
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [_entry("keep", is_directory=True, file_id=1)]},
            stats={(SHARE, "keep"): _entry("keep", is_directory=True, file_id=1)},
            denied_dirs={(SHARE, "keep")},
        )
        result, _upserts, _ = await _walk(ds, sync_filters=_filters(_folder_filter("keep")))
        assert result.complete is False
        assert result.unread == {f"{SHARE}/keep"}

    async def test_unread_folder_is_named_by_its_nfc_identity(self):
        decomposed = "cafe\u0301"
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [_entry(decomposed, is_directory=True, file_id=1)]},
            fail_dirs={(SHARE, decomposed)},
        )
        result, _upserts, _ = await _walk(ds)
        assert result.unread == {f"{SHARE}/caf\u00e9"}

    async def test_seen_set_accumulates_across_directories(self):
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry("a", is_directory=True, file_id=1)],
                (SHARE, "a"): [_entry("b", is_directory=True, file_id=2)],
                (SHARE, "a/b"): [_entry("c.txt", file_id=3)],
            }
        )
        result, _upserts, _ = await _walk(ds)
        assert result.seen == {
            SHARE,
            f"{SHARE}/a",
            f"{SHARE}/a/b",
            f"{SHARE}/a/b/c.txt",
        }

    async def test_listing_uses_stored_name_and_identity_is_nfc(self):
        decomposed = "cafe\u0301"
        ds = FakeNetworkShareDataSource(
            tree={
                (SHARE, ""): [_entry(decomposed, is_directory=True, file_id=1)],
                (SHARE, decomposed): [_entry("a.txt", file_id=2)],
            }
        )
        result, upserts, _ = await _walk(ds)
        assert (SHARE, decomposed) in ds.list_calls
        assert (SHARE, "caf\u00e9") not in ds.list_calls
        ids = _ids(upserts)
        assert f"{SHARE}/caf\u00e9" in ids
        assert f"{SHARE}/caf\u00e9/a.txt" in ids
        assert result.complete is True

    async def test_entry_failure_marks_walk_incomplete(self):
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [_entry("docs", is_directory=True, file_id=1)]}
        )
        result, _upserts, _ = await _walk(ds, fail_ids={f"{SHARE}/docs"})
        assert result.complete is False
        assert result.unread == {f"{SHARE}/docs"}


class TestShareWalkerRefusedSignIn:
    async def test_a_refused_sign_in_in_a_subfolder_stops_the_walk(self):
        ds = FakeNetworkShareDataSource(
            tree={(SHARE, ""): [_entry("a", is_directory=True, file_id=3), _entry("b", is_directory=True, file_id=4)]},
            refused_dirs={(SHARE, "a")},
        )
        with pytest.raises(NetworkShareAuthError):
            await _walk(ds)
        # Not read as one unreadable folder and walked past: every folder after it fails the same way.
        assert (SHARE, "b") not in ds.list_calls

    async def test_a_refused_sign_in_at_the_root_stops_the_walk(self):
        ds = FakeNetworkShareDataSource(refused_dirs={(SHARE, "")})
        with pytest.raises(NetworkShareAuthError):
            await _walk(ds)


# ---------------------------------------------------------------------------
# Folder patterns
# ---------------------------------------------------------------------------


def _share_tree(*paths: str, share: str = SHARE) -> dict[tuple[str, str], list[DirectoryEntry]]:
    """A tree from file paths (``a/b/c.txt``) and empty folders (``a/b/``), in the order given."""
    tree: dict[tuple[str, str], list[DirectoryEntry]] = {(share, ""): []}
    ids = iter(range(100, 10**6))
    for path in paths:
        parts = [p for p in path.split("/") if p]
        is_folder = path.endswith("/")
        for depth, name in enumerate(parts):
            parent = "/".join(parts[:depth])
            here = "/".join(parts[: depth + 1])
            directory = is_folder or depth < len(parts) - 1
            listing = tree.setdefault((share, parent), [])
            if any(e.name == name for e in listing):
                continue
            stamp = NEW + timedelta(minutes=next(ids))
            listing.append(
                _entry(name, is_directory=directory, file_id=next(ids), created_time=stamp, last_write_time=stamp)
            )
            if directory:
                tree.setdefault((share, here), [])
    return tree


def _key(path: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", path).casefold()


class InsensitiveShare(FakeNetworkShareDataSource):
    """Resolves paths as Windows and Samba do, whatever their case or Unicode form; a missing folder is not found."""

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.list_calls.append((share, path))
        for (s, stored), entries in self.tree.items():
            if s == share and _key(stored) == _key(path):
                if (s, stored) in self.fail_dirs:
                    raise DirectoryListingError(share, path, "listing failed")
                if (s, stored) in self.denied_dirs:
                    raise AccessDeniedError(share, path, "STATUS_ACCESS_DENIED")
                return list(entries)
        raise FileNotFoundError(path)

    async def stat(self, share: str, path: str, *, follow: bool = True) -> DirectoryEntry | None:
        if not path:
            return _entry("", is_directory=True, file_id=1)
        parent, _, name = path.rpartition("/")
        for (s, stored), entries in self.tree.items():
            if s == share and _key(stored) == _key(parent):
                return next((e for e in entries if _key(e.name) == _key(name)), None)
        return None


def _written(upserts) -> list[FileRecord]:
    return [record for batch in upserts for record, _perms in batch]


def _parents_come_first(records: list[FileRecord]) -> bool:
    written: set[str] = set()
    for record in records:
        if record.parent_external_record_id is not None and record.parent_external_record_id not in written:
            return False
        written.add(record.external_record_id)
    return True


CUSTOMERS = ("acme", "beta", "gamma")


def _customers_share(cls=FakeNetworkShareDataSource, **kwargs) -> FakeNetworkShareDataSource:
    paths = ["top.txt", "other/x.pdf"]
    for name in CUSTOMERS:
        paths += [
            f"customer/{name}/notes.txt",
            f"customer/{name}/quotes/q-{name}.pdf",
            f"customer/{name}/quotes/2026/q.pdf",
            f"customer/{name}/invoices/i.pdf",
            f"customer/{name}/Archive/old.pdf",
        ]
    return cls(tree=_share_tree(*paths), **kwargs)


QUOTE_IDS = {
    f"{SHARE}/{path}"
    for name in CUSTOMERS
    for path in (
        "customer",
        f"customer/{name}",
        f"customer/{name}/quotes",
        f"customer/{name}/quotes/q-{name}.pdf",
        f"customer/{name}/quotes/2026",
        f"customer/{name}/quotes/2026/q.pdf",
    )
}


class TestShareWalkerPatterns:
    async def test_one_folder_per_customer_lists_only_the_way_to_each_quotes_folder(self):
        ds = _customers_share()
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("customer/*/quotes")))
        assert ds.list_calls == [
            (SHARE, path)
            for path in ["customer"]
            + [p for name in CUSTOMERS for p in (f"customer/{name}", f"customer/{name}/quotes", f"customer/{name}/quotes/2026")]
        ]
        # One listing for the start, one per customer, one per folder in a quotes subtree.
        assert len(ds.list_calls) == 1 + len(CUSTOMERS) + 2 * len(CUSTOMERS)
        records = _written(upserts)
        assert {r.external_record_id for r in records} == QUOTE_IDS
        assert len(records) == len(QUOTE_IDS)
        assert _parents_come_first(records)
        assert result.seen == QUOTE_IDS | {SHARE}
        assert result.complete
        assert result.matched == frozenset({"customer/*/quotes"})

    async def test_backslashes_separate_folders(self):
        ds = _customers_share()
        _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("customer\\*\\quotes")))
        assert {r.external_record_id for r in _written(upserts)} == QUOTE_IDS
        assert (SHARE, "customer") in ds.list_calls

    async def test_names_match_whatever_their_case_on_a_share(self):
        ds = _customers_share(InsensitiveShare)
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("customer/*/QUOTES")))
        assert {r.external_record_id for r in _written(upserts)} == QUOTE_IDS
        assert result.matched == frozenset({"customer/*/QUOTES"})

    async def test_any_depth_writes_no_record_for_a_branch_without_a_match(self):
        ds = FakeNetworkShareDataSource(tree=_share_tree(
            "a/b/c/deep.txt", "a/b/near.txt", "x/quotes/q.txt", "y/z/quotes/sub/q.txt", "quotes/q.txt", "r.txt",
            "a/quotesx/y.txt",
        ))
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("**/quotes")))
        records = _written(upserts)
        assert {r.external_record_id for r in records} == {
            f"{SHARE}/{p}" for p in (
                "x", "x/quotes", "x/quotes/q.txt", "y", "y/z", "y/z/quotes", "y/z/quotes/sub",
                "y/z/quotes/sub/q.txt", "quotes", "quotes/q.txt",
            )
        }
        assert _parents_come_first(records)
        assert not {f"{SHARE}/a", f"{SHARE}/a/b", f"{SHARE}/a/b/c"} & result.seen

    async def test_not_in_an_archive_never_lists_it(self):
        ds = FakeNetworkShareDataSource(tree=_share_tree("archive/x.txt", "archive/sub/y.txt", "docs/z.txt", "r.txt"))
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("archive/**", exclude=True)))
        assert {r.external_record_id for r in _written(upserts)} == {
            f"{SHARE}/docs", f"{SHARE}/docs/z.txt", f"{SHARE}/r.txt",
        }
        assert not [path for _share, path in ds.list_calls if path.startswith("archive")]
        assert result.matched == frozenset({"archive/**"})

    async def test_not_in_any_archive_leaves_out_each_customers_archive_whatever_its_case(self):
        ds = _customers_share(InsensitiveShare)
        _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("**/archive", exclude=True)))
        ids = {r.external_record_id for r in _written(upserts)}
        assert not [i for i in ids if "/Archive" in i]
        assert f"{SHARE}/customer/acme/invoices/i.pdf" in ids
        assert not [path for _share, path in ds.list_calls if path.endswith("Archive")]

    async def test_a_pattern_and_a_saved_folder_inside_its_start_list_that_folder_once(self):
        for values in (("customer/acme/x", "customer/*/quotes"), ("customer/*/quotes", "customer/acme/x")):
            ds = FakeNetworkShareDataSource(tree=_share_tree("customer/acme/x/f.txt", "customer/acme/quotes/q.txt"))
            _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter(*values)))
            assert ds.list_calls.count((SHARE, "customer/acme/x")) == 1, values
            assert ds.list_calls[0] == (SHARE, "customer"), values
            assert {f"{SHARE}/customer/acme/x/f.txt", f"{SHARE}/customer/acme/quotes/q.txt"} <= _ids(upserts)

    async def test_a_link_loop_inside_a_folder_passed_through_is_not_walked(self, caplog):
        tree = _share_tree("a/quotes/q.txt")
        folder_a = tree[(SHARE, "")][0]
        tree[(SHARE, "a")].append(
            _entry("loop", is_directory=True, file_id=folder_a.file_id,
                   created_time=folder_a.created_time, last_write_time=folder_a.last_write_time)
        )
        tree[(SHARE, "a/loop")] = list(tree[(SHARE, "a")])
        ds = FakeNetworkShareDataSource(tree=tree)
        with caplog.at_level(logging.WARNING, logger="test.walker"):
            result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("**/quotes")))
        assert (SHARE, "a/loop") not in ds.list_calls
        assert f"{SHARE}/a/quotes/q.txt" in _ids(upserts)
        assert result.complete
        assert "link loop" in caplog.text

    async def test_a_misspelled_pattern_matches_nothing(self):
        ds = _customers_share()
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("custmer/*/quotes")))
        assert result.complete
        assert result.matched == frozenset()
        assert _written(upserts) == []

    async def test_a_matched_folder_whose_files_are_all_filtered_out_still_counts(self):
        ds = _customers_share()
        result, upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("customer/*/quotes"), _extension_filter("docx"))
        )
        assert result.matched == frozenset({"customer/*/quotes"})
        assert not [r for r in _written(upserts) if r.is_file]

    async def test_a_missing_fixed_start_is_named_and_does_not_make_the_walk_incomplete(self, caplog):
        ds = _customers_share(InsensitiveShare)
        with caplog.at_level(logging.WARNING, logger="test.walker"):
            result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("nothere/*/quotes")))
        assert result.complete
        assert result.unread == set()
        assert result.matched == frozenset()
        assert _written(upserts) == []
        assert f'Folder {SHARE}/nothere, the fixed start of folder pattern "nothere/*/quotes", does not exist' in caplog.text

    async def test_the_folders_above_an_unmatched_pattern_are_not_kept(self):
        ds = FakeNetworkShareDataSource(tree=_share_tree("customer/acme/invoices/i.pdf", "customer/beta/notes.txt"))
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("customer/*/quotes")))
        assert result.seen == {SHARE}
        assert _written(upserts) == []

    async def test_an_unread_folder_keeps_the_folders_above_it(self):
        ds = FakeNetworkShareDataSource(
            tree=_share_tree("customer/alpha/quotes/q.txt", "customer/beta/notes.txt"),
            fail_dirs={(SHARE, "customer/alpha")},
        )
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("customer/*/quotes")))
        assert not result.complete
        assert result.unread == {f"{SHARE}/customer/alpha"}
        # Not written, but kept: what is stored under the unread folder hangs from it.
        assert f"{SHARE}/customer" in result.seen
        assert _written(upserts) == []

    async def test_a_folder_above_a_match_that_fails_to_write_is_kept_and_the_match_still_written(self):
        ds = _customers_share()
        result, upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("customer/*/quotes")), fail_ids={f"{SHARE}/customer"}
        )
        assert not result.complete
        assert f"{SHARE}/customer" in result.unread
        assert QUOTE_IDS - {f"{SHARE}/customer"} == _ids(upserts)

    async def test_two_saved_folders_write_their_shared_folder_once_each(self):
        ds = FakeNetworkShareDataSource(tree=_share_tree("a/b/x.txt", "a/c/y.txt", "z/d/q/f.txt"))
        _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("a/b", "a/c", "z/*/q")))
        # Each start writes the folders above it, as it did before patterns.
        assert [r.external_record_id for r in _written(upserts)].count(f"{SHARE}/a") == 2
        assert f"{SHARE}/z/d/q/f.txt" in _ids(upserts)

    async def test_a_saved_folder_keeps_its_ids_when_a_pattern_is_added(self):
        ds = InsensitiveShare(tree=_share_tree("reports/x.txt", "reports/sub/y.txt", "b/archive/z.txt"))
        _alone, before, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("Reports")))
        ds.list_calls.clear()
        result, after, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("Reports", "*/archive")))
        under = [
            (r.external_record_id, r.parent_external_record_id) for r in _written(after)
            if r.external_record_id.startswith(f"{SHARE}/Reports")
        ]
        assert under == [(r.external_record_id, r.parent_external_record_id) for r in _written(before)]
        assert f"{SHARE}/Reports/x.txt" in result.seen
        assert {f"{SHARE}/b", f"{SHARE}/b/archive", f"{SHARE}/b/archive/z.txt"} <= _ids(after)

    async def test_a_respelled_folder_keeps_its_file_id_and_times(self):
        tree = _share_tree("reports/x.txt", "b/archive/z.txt")
        server = tree[(SHARE, "")][0]
        tree[(SHARE, "reports")].append(
            _entry("loop", is_directory=True, file_id=server.file_id,
                   created_time=server.created_time, last_write_time=server.last_write_time)
        )
        tree[(SHARE, "reports/loop")] = []
        ds = InsensitiveShare(tree=tree)
        _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("Reports", "*/archive")))
        [folder] = [r for r in _written(upserts) if r.external_record_id == f"{SHARE}/Reports"]
        spelled = dataclasses.replace(server, name="Reports")
        assert folder.external_revision_id == revision_id(SHARE, spelled, "Reports")
        assert folder.source_updated_at == int(server.last_write_time.timestamp() * 1000)
        # The link check still knows the folder by its file id and times.
        assert (SHARE, "Reports/loop") not in ds.list_calls

    async def test_a_saved_folder_spells_the_folders_above_it_however_the_server_does(self):
        ds = InsensitiveShare(tree=_share_tree("A/reports/x.txt", "b/archive/z.txt"))
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("a/Reports", "*/archive")))
        ids = _ids(upserts)
        assert {f"{SHARE}/a", f"{SHARE}/a/Reports", f"{SHARE}/a/Reports/x.txt"} <= ids
        assert not [i for i in ids if i.startswith(f"{SHARE}/A")]
        assert f"{SHARE}/b/archive/z.txt" in ids
        for record in _written(upserts):
            assert record.parent_external_record_id in result.seen | {None}

    async def test_a_value_inside_another_one_is_walked_as_part_of_it(self):
        ds = InsensitiveShare(tree=_share_tree("reports/2026/x.txt", "reports/y.txt"))
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("Reports", "reports/2026")))
        assert [path for _share, path in ds.list_calls] == ["Reports", "Reports/2026"]
        by_id = {r.external_record_id: r.parent_external_record_id for r in _written(upserts)}
        assert by_id[f"{SHARE}/Reports/2026"] == f"{SHARE}/Reports"
        assert by_id[f"{SHARE}/Reports/2026/x.txt"] == f"{SHARE}/Reports/2026"
        assert not [i for i in result.seen if i.startswith(f"{SHARE}/reports")]

    async def test_the_first_saved_value_spells_a_folder_two_values_share(self):
        ds = InsensitiveShare(tree=_share_tree("a/y/f.txt", "a/x/g.txt", "q/z/h.txt"))
        _result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("A/x", "a/y", "*/z")))
        by_id = {r.external_record_id: r.parent_external_record_id for r in _written(upserts)}
        assert by_id[f"{SHARE}/A/y/f.txt"] == f"{SHARE}/A/y"
        assert by_id[f"{SHARE}/A/y"] == f"{SHARE}/A"
        assert f"{SHARE}/A/x/g.txt" in by_id

    async def test_a_saved_folder_under_a_never_synced_name_is_reached_from_a_pattern(self):
        ds = FakeNetworkShareDataSource(tree=_share_tree("node_modules/keep/a.txt", "node_modules/other/b.txt", "c/archive/z.txt"))
        _alone, before, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("node_modules/keep")))
        _result, after, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("node_modules/keep", "*/archive")))
        under = [
            (r.external_record_id, r.parent_external_record_id) for r in _written(after)
            if r.external_record_id.startswith(f"{SHARE}/node_modules")
        ]
        assert under == [(r.external_record_id, r.parent_external_record_id) for r in _written(before)]
        assert f"{SHARE}/node_modules/other/b.txt" not in _ids(after)
        assert f"{SHARE}/c/archive/z.txt" in _ids(after)

    async def test_a_denied_saved_folder_reached_from_a_pattern_is_unread(self):
        ds = FakeNetworkShareDataSource(
            tree=_share_tree("secret/a.txt", "c/archive/z.txt"), denied_dirs={(SHARE, "secret")}
        )
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("secret", "*/archive")))
        assert result.unread == {f"{SHARE}/secret"}
        assert not result.complete
        assert f"{SHARE}/c/archive/z.txt" in _ids(upserts)

    async def test_a_denied_folder_above_a_saved_folder_keeps_what_was_stored_under_it(self):
        ds = FakeNetworkShareDataSource(
            tree=_share_tree("dept/secret/keep/a.txt", "c/archive/z.txt"), denied_dirs={(SHARE, "dept/secret")}
        )
        result, upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("dept/secret/keep", "*/archive")))
        assert not result.complete
        assert f"{SHARE}/dept/secret/keep" in result.unread
        assert f"{SHARE}/c/archive/z.txt" in _ids(upserts)

    async def test_a_folder_above_a_saved_folder_that_fails_to_list_keeps_it_in_either_spelling(self):
        # The saved folders spell `a` two ways; what an earlier walk stored under either is kept.
        ds = InsensitiveShare(
            tree=_share_tree("a/x/f.txt", "a/y/g.txt", "c/archive/z.txt"), fail_dirs={(SHARE, "a")}
        )
        result, _upserts, _moves = await _walk(ds, sync_filters=_filters(_folder_filter("A/x", "a/y", "*/archive")))
        assert not result.complete
        # Main walked each saved folder from its own spelling; this walk spells `a` as `A`.
        for kept in (f"{SHARE}/A/x/f.txt", f"{SHARE}/a/y/g.txt", f"{SHARE}/A/y/g.txt"):
            assert any(kept.startswith(f"{u}/") for u in result.unread), (kept, result.unread)
