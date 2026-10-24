"""Azure Blob sync behaviour for the PP-314 review (PP-315..PP-338).

The listing, sync points and record store are fakes; the connector's sync code runs for real.
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from object_store_behaviour_fakes import (
    BUCKET,
    FakeAzureBlobDataSource,
    FakeConfigService,
    FakeObjectStore,
    Response,
    make_connector,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.services.notification.types import NotificationType

OTHER = "bucket-2"


def path(key: str, container: str = BUCKET) -> str:
    return f"{container}/{key}"


class _AzureStore(FakeAzureBlobDataSource):
    """The fake Azure listing plus what these scenarios need: per-blob metadata and tier,
    deleted containers, and a hook run after each page is listed."""

    def __init__(self, store: FakeObjectStore) -> None:
        super().__init__(store)
        self.metadata: dict[str, dict[str, str]] = {}
        self.tiers: dict[str, str] = {}
        self.tier_changed: dict[str, datetime] = {}
        self.deleted: set[str] = set()
        # Containers whose listing Azure answers with this error.
        self.refused: dict[str, Exception] = {}
        self.after_page: Any = None
        self.list_calls: list[str] = []

    async def list_blobs(self, container_name: str, prefix: str | None = None, **kw: object) -> Response:
        self.list_calls.append(container_name)
        if container_name in self.deleted or container_name in self.refused:
            error = self.refused.get(container_name) or ConnectionError("ContainerNotFound")

            # The SDK pager fails only when it is iterated.
            async def gone() -> AsyncIterator[dict[str, Any]]:
                raise error
                yield  # pragma: no cover
            return Response(True, gone())
        inner = (await super().list_blobs(container_name, prefix, **kw)).data

        async def blobs() -> AsyncIterator[dict[str, Any]]:
            n = 0
            async for blob in inner:
                blob["metadata"] = self.metadata.get(blob["name"], {})
                blob["blob_tier"] = self.tiers.get(blob["name"])
                blob["blob_tier_change_time"] = self.tier_changed.get(blob["name"])
                yield blob
                n += 1
                if self.after_page and n % self.page_size == 0:
                    self.after_page()
        return Response(True, blobs())

    async def list_containers(self) -> Response:
        return Response(True, [{"name": n} for n in self.bucket_names() if n not in self.deleted])

    async def get_container_properties(self, container_name: str) -> Response:
        if container_name in self.deleted:
            r = Response(False, None, "The specified container does not exist.")
            r.error_code = "ContainerNotFound"
            return r
        return Response(True, None)


@pytest.fixture
def other() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def connector(store, other, db, checkpoints, config) -> AzureBlobConnector:
    connector = make_connector("azure_blob", store, db, checkpoints, config)
    connector.data_source = _AzureStore(store)
    connector.data_source.stores[OTHER] = other
    connector.notify = AsyncMock()
    return connector


def choose_containers(connector: AzureBlobConnector, config: FakeConfigService, names: list[str] | None) -> None:
    """Containers from the filter (``names``) or, with None, every listed one (personal scope)."""
    connector.container_name = None
    if names is None:
        connector.scope = "personal"
    else:
        config.set_selection("containers", names)


class TestDeletedContainer:
    """PP-315: a container deleted in Azure leaves the index."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("named", [False, True], ids=["empty-filter", "named-in-filter"])
    async def test_its_records_and_group_go(self, connector, store, other, db, config, named) -> None:
        choose_containers(connector, config, [BUCKET, OTHER] if named else None)
        store.put("keep.txt")
        other.put("secret.txt")
        await connector.run_sync()
        assert path("secret.txt", OTHER) in db.paths()

        connector.data_source.deleted.add(OTHER)
        await connector.run_sync()

        assert not any(p.startswith(f"{OTHER}/") for p in db.paths())
        assert OTHER not in db.record_groups
        assert path("keep.txt") in db.paths()

    @pytest.mark.asyncio
    async def test_an_unreachable_container_is_not_taken_for_deleted(self, connector, store, other, db, config) -> None:
        choose_containers(connector, config, None)
        other.put("secret.txt")
        await connector.run_sync()
        ds = connector.data_source
        ds.list_containers = AsyncMock(return_value=Response(True, [{"name": BUCKET}]))
        ds.get_container_properties = AsyncMock(return_value=Response(False, None, "AuthorizationFailure"))

        await connector.run_sync()

        assert path("secret.txt", OTHER) in db.paths()


class TestOverwriteDuringALongRun:
    """PP-316: the checkpoint is never later than the run's start (minus a safety window)."""

    @staticmethod
    def _clock(store: FakeObjectStore) -> AbstractContextManager[MagicMock]:
        return patch(
            "app.connectors.sources.azure_blob.connector.get_epoch_timestamp_in_ms",
            side_effect=lambda: int(store.clock.timestamp() * 1000),
        )

    @pytest.mark.asyncio
    async def test_a_blob_overwritten_after_it_was_listed_is_picked_up_next_run(self, connector, store, db) -> None:
        store.put("a/report.txt", "old text")
        store.put("m.txt")
        store.put("y.txt")  # so the listing has a second page, where z/new.txt shows up
        with self._clock(store):
            await connector.run_sync()
            store.clock += timedelta(hours=1)

            def overwrite_mid_run() -> None:
                connector.data_source.after_page = None
                store.put("a/report.txt", "new text")
                store.put("z/new.txt")
            connector.data_source.after_page = overwrite_mid_run
            await connector.run_sync()  # listed the old a/report.txt, then z/new.txt
            store.clock += timedelta(hours=1)
            await connector.run_sync()

        assert db.by_path()[path("a/report.txt")].external_revision_id == hashlib.md5(b"new text").hexdigest()

    @pytest.mark.asyncio
    async def test_an_old_copy_of_still_listed_content_gets_its_own_record(self, connector, store, db, config) -> None:
        # A blob first left out by a filter, older than the checkpoint, with the same bytes as a synced one.
        store.put("a.pdf", "same bytes")  # older than b.txt, so below the checkpoint the first sync saves
        store.put("b.txt", "same bytes")
        config.set_extensions("in", ["txt"])
        await connector.run_sync()
        assert path("a.pdf") not in db.paths()

        config.sync_filters.pop("file_extensions")
        await connector.run_sync()

        records = db.by_path()
        assert path("a.pdf") in records and path("b.txt") in records
        assert records[path("a.pdf")].id != records[path("b.txt")].id


class TestFolderBlobs:
    """PP-320 / PP-333: a folder marker or an ADLS Gen2 directory is the folder its files imply."""

    @pytest.mark.asyncio
    async def test_one_folder_record_per_folder_and_no_empty_files(self, connector, store, db) -> None:
        store.put("a/b/", "")
        store.put("a/b/c.txt")
        store.put("x/y", "")
        store.put("x/y/z.txt")
        store.put("x/empty", "")
        connector.data_source.metadata = {"x/y": {"hdi_isfolder": "true"}, "x/empty": {"Hdi_IsFolder": "True"}}

        await connector.run_sync()

        records = db.by_path()
        assert sorted(p for p, r in records.items() if r.is_file) == [path("a/b/c.txt"), path("x/y/z.txt")]
        assert sorted(p for p, r in records.items() if not r.is_file) == [
            path("a"), path("a/b"), path("x"), path("x/empty"), path("x/y"),
        ]

    @pytest.mark.asyncio
    async def test_an_unchanged_directory_blob_is_not_saved_again(self, connector, store, db) -> None:
        store.put("x/y", "")
        store.put("x/y/z.txt")
        store.put("later.txt")  # the checkpoint lands after both x/y and x/y/z.txt
        connector.data_source.metadata = {"x/y": {"hdi_isfolder": "true"}}
        await connector.run_sync()
        db.written.clear()

        await connector.run_sync()

        assert path("x/y") not in db.written
        assert path("x/y") in db.paths()

    @pytest.mark.asyncio
    async def test_a_stored_trailing_slash_duplicate_is_removed(self, connector, store, db) -> None:
        store.put("a/b/", "")
        store.put("a/b/c.txt")
        await connector.run_sync()
        # As the old code stored a marker: a second folder record under container/a/b/.
        folder = db.by_path()[path("a/b")]
        dup = folder.model_copy(update={"id": "dup", "external_record_id": path("a/b/")})
        db.records["dup"] = dup

        await connector.run_sync()

        assert path("a/b/") not in db.paths()
        assert path("a/b") in db.paths()


class TestFailuresAreReported:
    """PP-321 / PP-334 / PP-335: a failed container fails the run, says so, and a full sync unlinks nothing."""

    @pytest.mark.asyncio
    async def test_a_failed_container_fails_the_run_after_the_others_sync(self, connector, store, other, db, config) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("a.txt")
        other.put("b.txt")
        other.fail_page = 0

        with pytest.raises(RuntimeError, match=OTHER):
            await connector.run_sync()

        assert path("a.txt") in db.paths()
        assert connector.full_sync_coverage().complete is False
        connector.notify.assert_awaited()
        assert "Azure Blob" in connector.notify.await_args.kwargs["title"]

    @pytest.mark.asyncio
    async def test_a_failed_blob_marks_the_read_incomplete(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("b.txt")
        real = connector._process_azure_blob

        async def fail_b(blob: dict, *args: object, **kwargs: object) -> tuple:
            if blob["name"] == "b.txt":
                return None, []
            return await real(blob, *args, **kwargs)
        connector._process_azure_blob = fail_b

        await connector.run_sync()

        assert connector.full_sync_coverage().complete is False

    @pytest.mark.asyncio
    async def test_a_clean_run_is_complete(self, connector, store) -> None:
        store.put("a.txt")
        await connector.run_sync()
        assert connector.full_sync_coverage().complete is True

    @pytest.mark.asyncio
    async def test_a_failed_container_listing_fails_the_run(self, connector, config) -> None:
        choose_containers(connector, config, None)
        connector.data_source.list_containers = AsyncMock(return_value=Response(False, None, "AuthenticationFailed"))

        with pytest.raises(ConnectionError, match="AuthenticationFailed"):
            await connector.run_sync()
        connector.notify.assert_awaited()


class TestTeamScope:
    """PP-322: a team connector syncs only containers it names."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selection", [None, ("not_in", [OTHER])], ids=["empty", "not-in"])
    async def test_without_named_containers_it_refuses(self, connector, store, db, config, selection) -> None:
        connector.container_name = None
        if selection:
            config.set_selection("containers", selection[1], selection[0])
        store.put("a.txt")

        with pytest.raises(ValueError, match="Container Names"):
            await connector.run_sync()

        assert db.paths() == set()
        connector.notify.assert_awaited()

    @pytest.mark.asyncio
    async def test_with_named_containers_it_syncs_only_those(self, connector, store, other, db, config) -> None:
        connector.container_name = None
        config.set_selection("containers", [BUCKET])
        store.put("a.txt")
        other.put("b.txt")

        await connector.run_sync()

        assert db.paths() == {path("a.txt")}


class TestLessWork:
    """PP-326 / PP-327 / PP-328."""

    @pytest.mark.asyncio
    async def test_records_are_read_once_per_container(self, connector, store, db) -> None:
        for key in ("a.txt", "b.txt", "c.txt"):
            store.put(key)
        await connector.run_sync()
        store.delete("a.txt")
        reads = AsyncMock(side_effect=db.get_records_in_record_group)
        db.get_records_in_record_group = reads

        await connector.run_sync()

        assert reads.await_count == 1
        assert path("a.txt") not in db.paths()

    @pytest.mark.asyncio
    async def test_a_moved_record_is_not_removed_with_its_old_name(self, connector, store, db) -> None:
        store.put("old/a.txt", "content")
        await connector.run_sync()
        moved = db.by_path()[path("old/a.txt")].id

        store.rename("old/a.txt", "new/a.txt")
        await connector.run_sync()

        assert db.by_path()[path("new/a.txt")].id == moved

    @pytest.mark.asyncio
    async def test_a_move_to_a_container_still_listing_keeps_its_record(
        self, connector, store, other, db, config,
    ) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("old/a.txt", "moved content")
        other.put("b.txt")
        await connector.run_sync()
        original = db.by_path()[path("old/a.txt")].id

        other.put("new/a.txt", "moved content")
        store.delete("old/a.txt")
        source_done = asyncio.Event()
        real = connector._sync_container

        async def destination_lists_last(name: str) -> None:
            if name == OTHER:
                await asyncio.wait_for(source_done.wait(), timeout=2)
            await real(name)
            if name == BUCKET:
                source_done.set()
        connector._sync_container = destination_lists_last

        await connector.run_sync()

        assert db.by_path()[path("new/a.txt", OTHER)].id == original
        assert path("old/a.txt") not in db.paths()

    @pytest.mark.asyncio
    async def test_a_move_into_a_container_that_fails_keeps_its_record(
        self, connector, store, other, db, config,
    ) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("old/a.txt", "moved content")
        other.put("b.txt")
        await connector.run_sync()
        original = db.by_path()[path("old/a.txt")].id

        other.put("new/a.txt", "moved content")
        store.delete("old/a.txt")
        other.fail_page = 0
        with pytest.raises(RuntimeError, match=OTHER):
            await connector.run_sync()
        other.fail_page = None
        await connector.run_sync()

        assert db.by_path()[path("new/a.txt", OTHER)].id == original

    @pytest.mark.asyncio
    async def test_each_folder_is_saved_once_per_sync(self, connector, store, db) -> None:
        for i in range(5):
            store.put(f"a/b/c/{i}.txt")

        await connector.run_sync()

        assert db.written.count(path("a")) == 1
        assert db.written.count(path("a/b/c")) == 1

    @pytest.mark.asyncio
    async def test_containers_are_listed_at_the_same_time(self, connector, store, other, db, config) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("a.txt")
        other.put("b.txt")
        both_started = asyncio.Event()
        started: set[str] = set()
        real = connector._sync_container

        async def wait_for_both(name: str) -> None:
            started.add(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=2)
            await real(name)
        connector._sync_container = wait_for_both

        await connector.run_sync()

        # Listed one after the other, the first would time out waiting for the second.
        assert {path("a.txt"), path("b.txt", OTHER)} <= db.paths()


class TestArchiveTier:
    """PP-338: an Archive-tier blob is recorded but not sent to indexing."""

    @pytest.mark.asyncio
    async def test_it_is_not_auto_indexed(self, connector, store, db) -> None:
        store.put("cold.txt")
        store.put("hot.txt")
        connector.data_source.tiers = {"cold.txt": "Archive", "hot.txt": "Hot"}

        await connector.run_sync()

        records = db.by_path()
        assert records[path("cold.txt")].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert records[path("hot.txt")].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


class TestTierChange:
    """Setting a tier (rehydrating from Archive) changes neither Last-Modified nor the etag."""

    @pytest.mark.asyncio
    async def test_a_rehydrated_blob_is_sent_to_indexing(self, connector, store, db) -> None:
        store.put("cold.txt")
        store.put("other.txt")
        connector.data_source.tiers = {"cold.txt": "Archive"}
        await connector.run_sync()
        assert db.by_path()[path("cold.txt")].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

        connector.data_source.tiers = {"cold.txt": "Hot"}
        connector.data_source.tier_changed = {"cold.txt": store.tick()}
        await connector.run_sync()

        assert db.by_path()[path("cold.txt")].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_an_old_tier_change_does_not_resync_the_blob(self, connector, store, db) -> None:
        connector.data_source.tier_changed = {"a.txt": store.tick()}
        store.put("a.txt")
        store.put("newest.txt")  # the newest blob sits on the checkpoint, which is inclusive
        await connector.run_sync()
        db.written.clear()

        await connector.run_sync()

        assert path("a.txt") not in db.written

    @pytest.mark.asyncio
    async def test_a_resynced_tier_change_moves_the_checkpoint(self, connector, store, db) -> None:
        store.put("a.txt")
        store.put("newest.txt")
        await connector.run_sync()
        connector.data_source.tier_changed = {"a.txt": store.tick()}
        await connector.run_sync()
        db.written.clear()

        await connector.run_sync()

        assert path("a.txt") not in db.written


# Azure's text for a request signed with the wrong key, as the SDK raises it.
_AUTH_FAILURE = (
    "Server failed to authenticate the request. Make sure the value of Authorization header is formed "
    "correctly including the signature.\nRequestId:62830b83\nTime:2026-10-23T10:24:58Z\n"
    "ErrorCode:AuthenticationFailed\nauthenticationerrordetail:The MAC signature found in the HTTP request "
    "'wJS3D2tZeiD9AeonSNQJ22XsR0xujOTKCFOjMXPW+k8=' is not the same as any computed signature. Server used "
    "following string to sign: 'GET\n/acct/bucket-1\ncomp:list'.\nContent: <?xml version=\"1.0\"?><Error>"
    "<Code>AuthenticationFailed</Code></Error>"
)


def azure_error(cls: type[HttpResponseError], text: str, status: int, code: str) -> HttpResponseError:
    error = cls(message=text)
    error.status_code, error.error_code = status, code
    return error


def assert_nothing_secret(text: str) -> None:
    for leak in ("wJS3D2tZeiD9AeonSNQJ22XsR0xujOTKCFOjMXPW", "string to sign", "<?xml", "RequestId"):
        assert leak not in text


class TestFailureReports:
    """A failed sync tells the user why, and an auth failure says it is one; logs keep only status, code and message."""

    @pytest.mark.asyncio
    async def test_refused_credentials_are_reported_as_an_auth_error(self, connector, store, db, caplog) -> None:
        store.put("a.txt")
        await connector.run_sync()
        connector.data_source.refused[BUCKET] = azure_error(ClientAuthenticationError, _AUTH_FAILURE, 403, "AuthenticationFailed")

        with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match=BUCKET):
            await connector.run_sync()

        sent = connector.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        assert sent["title"].endswith("Credentials rejected")
        assert "HTTP 403 AuthenticationFailed: Server failed to authenticate the request." in sent["message"]
        assert_nothing_secret(sent["message"])
        assert_nothing_secret(caplog.text)
        assert path("a.txt") in db.paths()

    @pytest.mark.asyncio
    async def test_another_listing_failure_names_its_reason(self, connector, store, caplog) -> None:
        store.put("a.txt")
        connector.data_source.refused[BUCKET] = azure_error(
            HttpResponseError, "The server is busy.\nRequestId:1\nContent: <?xml?>", 503, "ServerBusy",
        )

        with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
            await connector.run_sync()

        sent = connector.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_SYNC_ERROR
        assert f"{BUCKET}: HTTP 503 ServerBusy: The server is busy." in sent["message"]
        assert_nothing_secret(caplog.text)

    @pytest.mark.asyncio
    async def test_files_that_failed_are_counted_in_a_notice(self, connector, store, db) -> None:
        store.put("bad.txt")
        store.put("good.txt")
        real = connector._process_azure_blob

        async def fail_bad(blob: dict, *args: object) -> tuple:
            if blob["name"] == "bad.txt":
                raise ValueError("unreadable properties")
            return await real(blob, *args)
        connector._process_azure_blob = fail_bad

        await connector.run_sync()

        sent = connector.notify.await_args.kwargs
        assert sent["title"].endswith("Some files failed to sync")
        assert sent["message"].startswith("1 file(s) could not be synced")
        assert path("good.txt") in db.paths()

    @pytest.mark.asyncio
    async def test_files_refused_for_their_credentials_are_reported_as_an_auth_error(
        self, connector, store, db, caplog,
    ) -> None:
        store.put("bad.txt")
        store.put("good.txt")
        real = connector._process_azure_blob

        async def refuse_bad(blob: dict, *args: object) -> tuple:
            if blob["name"] == "bad.txt":
                raise azure_error(HttpResponseError, _AUTH_FAILURE, 403, "AuthorizationPermissionMismatch")
            return await real(blob, *args)
        connector._process_azure_blob = refuse_bad

        with caplog.at_level(logging.DEBUG):
            await connector.run_sync()

        sent = connector.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        assert sent["title"].endswith("Credentials rejected")
        assert "HTTP 403 AuthorizationPermissionMismatch" in sent["message"]
        assert "1 file(s) could not be synced" in sent["message"]
        assert_nothing_secret(sent["message"])
        assert_nothing_secret(caplog.text)
        assert path("good.txt") in db.paths()

    @pytest.mark.asyncio
    async def test_a_refused_container_list_is_an_auth_error(self, connector, config) -> None:
        choose_containers(connector, config, None)
        refused = Response(False, None, "HTTP 403 AuthenticationFailed: Server failed to authenticate the request.")
        refused.error_code = "AuthenticationFailed"
        connector.data_source.list_containers = AsyncMock(return_value=refused)

        with pytest.raises(ConnectionError):
            await connector.run_sync()

        sent = connector.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        assert sent["title"].endswith("Credentials rejected")
        assert "HTTP 403 AuthenticationFailed" in sent["message"]


class TestClearedTeamSelection:
    """A team connector whose Container Names are cleared stops syncing; what it synced before must not stay readable by the org."""

    @pytest.mark.asyncio
    async def test_its_containers_are_removed(self, connector, store, other, db, config) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("a.txt")
        other.put("b.txt")
        await connector.run_sync()
        assert {path("a.txt"), path("b.txt", OTHER)} <= db.paths()

        config.sync_filters.pop("containers")
        with pytest.raises(ValueError, match="Container Names"):
            await connector.run_sync()

        assert not db.paths()
        assert BUCKET not in db.record_groups and OTHER not in db.record_groups
        assert "The 2 container(s) it synced before were removed" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fail_mode", ["raise", "empty"])
    async def test_an_unreadable_config_removes_nothing(self, connector, store, db, config, fail_mode) -> None:
        choose_containers(connector, config, [BUCKET])
        store.put("a.txt")
        await connector.run_sync()
        # The run's own filter load then sees no filters, as it would after a failed read.
        config.fail_mode, config.fail_from = fail_mode, config.reads

        with pytest.raises(ValueError):
            await connector.run_sync()

        assert path("a.txt") in db.paths()
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    @pytest.mark.parametrize("excluded", [[OTHER], []], ids=["naming-one", "naming-none"])
    async def test_a_not_in_filter_removes_nothing(self, connector, store, db, config, excluded) -> None:
        choose_containers(connector, config, [BUCKET])
        store.put("a.txt")
        await connector.run_sync()

        config.set_selection("containers", excluded, "not_in")
        with pytest.raises(ValueError, match="Container Names"):
            await connector.run_sync()

        assert path("a.txt") in db.paths()
        assert BUCKET in db.record_groups

    @pytest.mark.asyncio
    async def test_the_notice_counts_only_containers_actually_removed(self, connector, store, other, db, config) -> None:
        choose_containers(connector, config, [BUCKET, OTHER])
        store.put("a.txt")
        other.put("b.txt")
        await connector.run_sync()
        db.refused_group_deletes.add(OTHER)

        config.set_selection("containers", [])
        with pytest.raises(ValueError, match="Container Names"):
            await connector.run_sync()

        assert BUCKET not in db.record_groups and OTHER in db.record_groups
        assert "The 1 container(s) it synced before were removed" in connector.notify.await_args.kwargs["message"]


class TestFolderTimes:
    """Azure keeps no folder times: a new folder is dated by the blob that implies it, and a stored one keeps its dates."""

    @pytest.mark.asyncio
    async def test_a_new_folder_takes_its_blobs_time_and_a_stored_one_keeps_its_own(self, connector, store, db) -> None:
        store.put("a/x.txt")
        first_ms = int(store.objects["a/x.txt"].modified.timestamp() * 1000)
        await connector.run_sync()
        folder = db.by_path()[path("a")]
        assert (folder.source_created_at, folder.source_updated_at) == (first_ms, first_ms)
        stored = folder.model_copy()

        store.put("a/y.txt")
        await connector.run_sync()

        # The fake store replaces records whole; the real processor is what keeps stored times.
        sent = db.by_path()[path("a")]
        graph = AsyncMock()
        graph.get_record_by_external_id = AsyncMock(return_value=stored)
        graph.get_record_group_by_external_id = AsyncMock(return_value=None)
        processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
        processor.org_id = stored.org_id
        saved, _ = await processor._process_record(sent, [], graph)
        assert (saved.source_created_at, saved.source_updated_at) == (first_ms, first_ms)
