# ruff: noqa: ANN201, ANN202
"""ShareWalker tests against an in-memory INetworkShareDataSource."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors
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
from app.connectors.sources.network_share.errors import DirectoryListingError
from app.connectors.sources.network_share.record_mapper import RecordMapper
from app.connectors.sources.network_share.walker import ShareWalker

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
    ) -> None:
        self.tree = tree or {}
        self.shares = shares if shares is not None else [ShareInfo(name=SHARE, share_type="disk")]
        self.fail_dirs = fail_dirs or set()
        self.files = files or {}
        self.stats = stats or {}
        self.closed = False
        self.list_calls: list[tuple[str, str]] = []

    async def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.list_calls.append((share, path))
        if (share, path) in self.fail_dirs:
            raise DirectoryListingError(share, path, "listing failed")
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

    async def stat(self, share: str, path: str) -> DirectoryEntry | None:
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
            async def stat(self, share: str, path: str) -> DirectoryEntry | None:
                raise DirectoryListingError(share, path, "stat failed")

        ds = FailingStat(tree={(SHARE, "keep"): [_entry("in.txt", file_id=4)]})
        result, _upserts, _moves = await _walk(
            ds, sync_filters=_filters(_folder_filter("keep"))
        )
        assert (SHARE, "keep") not in ds.list_calls
        assert result.complete is False

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
        ids = _ids(upserts)
        assert f"{SHARE}/ok.txt" in ids
        assert f"{SHARE}/good/nested.txt" in ids
        assert f"{SHARE}/ok.txt" in result.seen
        assert f"{SHARE}/good/nested.txt" in result.seen

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
