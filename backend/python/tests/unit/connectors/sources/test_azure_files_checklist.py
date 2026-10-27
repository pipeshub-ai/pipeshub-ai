"""Azure Files against the connector checklist: deletes only on a confirmed signal,
refused credentials, notifications, endpoint safety, hierarchy above a folder filter,
versions and reindex.

The real connector runs against the in-memory share tree and record store of the
earlier Azure Files review harness, so each test drives the code path it names.
"""

import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    MultiselectOperator,
)
from app.connectors.sources.azure_files import connector as connector_module
from app.connectors.sources.azure_files.connector import (
    AzureFilesConnector,
    CredentialsRejectedError,
    get_file_extension,
)
from app.models.entities import FileRecord
from app.sources.external.azure.azure_files import CREDENTIALS_REJECTED
from app.utils.url_fetcher import FetchError
from tests.unit.connectors.sources.test_azure_files_pp341 import (
    FakeDataSource,
    FakeProcessor,
    _connector,
    _err,
    _ok,
    _sync,
)

# Microsoft's own wording for a deleted app registration: "not found", yet not a gone share.
ENTRA_APP_GONE = (
    "Azure Files API error: ClientSecretCredential authentication failed: AADSTS700016: Application "
    "with identifier 'x' was not found in the directory 'y'."
)


def _tree() -> FakeDataSource:
    ds = FakeDataSource({})
    ds.add("s", "a.txt")
    ds.add("s", "docs", is_dir=True)
    ds.add("s", "docs/b.txt")
    return ds


def _titles(conn) -> list[str]:
    return [c.kwargs["title"] for c in conn.notify.await_args_list]


# ---------------------------------------------------------------------------
# §8.4: records are deleted only on a confirmed signal from the source
# ---------------------------------------------------------------------------
class TestDeletesOnlyOnConfirmedSignal:
    async def test_entra_error_saying_not_found_deletes_nothing(self) -> None:
        ds = _tree()
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team", shares=["s"])
        await _sync(conn)
        before = set(proc.records)
        assert {"s/a.txt", "s/docs/b.txt"} <= before

        refused = _err(ENTRA_APP_GONE, CREDENTIALS_REJECTED)
        ds.list_directories_and_files = AsyncMock(return_value=refused)
        ds.get_share_properties = AsyncMock(return_value=refused)
        with pytest.raises(CredentialsRejectedError):
            await _sync(conn)

        assert set(proc.records) == before
        assert proc.deleted == []
        assert "s" in proc.groups
        assert conn.full_sync_coverage().complete is False
        assert any("Credentials rejected" in t for t in _titles(conn))
        assert not any("not found" in t for t in _titles(conn))

    @pytest.mark.parametrize(
        "code", ["AuthorizationPermissionMismatch", "AuthorizationFailure", "ServerBusy", "OperationTimedOut"]
    )
    async def test_refused_or_failing_root_deletes_nothing(self, code: str) -> None:
        ds = _tree()
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        before = set(proc.records)

        del ds.shares["s"]["a.txt"]
        ds.list_directories_and_files = AsyncMock(return_value=_err(f"Azure Files API error: {code}", code))
        ds.get_share_properties = AsyncMock(return_value=_err(f"Azure Files API error: {code}", code))
        with pytest.raises(RuntimeError, match="No share could be read"):
            await _sync(conn)
        assert set(proc.records) == before

    async def test_deleted_folder_takes_its_subtree(self) -> None:
        ds = _tree()
        ds.add("s", "docs/deep", is_dir=True)
        ds.add("s", "docs/deep/c.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)

        for path in ["docs", "docs/b.txt", "docs/deep", "docs/deep/c.txt"]:
            del ds.shares["s"][path]
        await _sync(conn)
        assert set(proc.records) == {"s/a.txt"}
        assert {"s/docs", "s/docs/b.txt", "s/docs/deep", "s/docs/deep/c.txt"} <= set(proc.deleted)


# ---------------------------------------------------------------------------
# §8.2: a selected share that is gone goes with its group, and the owner is told once
# ---------------------------------------------------------------------------
class TestGoneSelectedShare:
    async def test_group_and_records_go_and_the_owner_is_told_once(self) -> None:
        ds = _tree()
        ds.add("keep", "k.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team", shares=["s", "keep"])
        await _sync(conn)
        assert proc.groups == {"s", "keep"}

        del ds.shares["s"]
        await _sync(conn)
        assert proc.groups == {"keep"}
        assert set(proc.records) == {"keep/k.txt"}
        assert _titles(conn).count(f"{conn.display_name}: File share s not found") == 1

        await _sync(conn)
        # Not written again, so no empty group comes back and nothing is announced again.
        assert proc.groups == {"keep"}
        assert _titles(conn).count(f"{conn.display_name}: File share s not found") == 1

    async def test_a_share_deleted_during_the_run_goes_in_that_run(self) -> None:
        ds = _tree()
        ds.add("keep", "k.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, scope="team", shares=["s", "keep"])
        await _sync(conn)

        # Still there when the run checks the selected shares, gone by the time it walks it.
        real = ds.get_share_properties
        checked: list[str] = []

        async def gone_after_first_check(share_name: str) -> object:
            checked.append(share_name)
            return _ok({"name": share_name}) if checked.count(share_name) == 1 else await real(share_name)

        ds.get_share_properties = gone_after_first_check
        del ds.shares["s"]
        await _sync(conn)
        assert proc.groups == {"keep"}
        assert set(proc.records) == {"keep/k.txt"}
        assert _titles(conn).count(f"{conn.display_name}: File share s not found") == 1

    async def test_a_share_listed_by_name_is_not_walked_once_gone(self) -> None:
        ds = _tree()
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s", "never"])
        await _sync(conn)
        assert ("never", "") not in ds.listed
        assert "never" not in proc.groups


# ---------------------------------------------------------------------------
# §2.4 / §7.7: a refused credential stops the run and asks for new credentials
# ---------------------------------------------------------------------------
class TestRefusedCredentials:
    async def test_mid_walk_refusal_stops_listing_and_fails_the_run(self) -> None:
        ds = _tree()
        for i in range(5):
            ds.add("s", f"docs/d{i}", is_dir=True)
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        before = set(proc.records)

        real = ds.list_directories_and_files

        async def expire_after_root(share_name: str, directory_path: str = "", **kwargs: object) -> object:
            if directory_path:
                ds.listed.append((share_name, directory_path))
                return _err("Azure Files API error: AuthenticationFailed (HTTP 403)", "AuthenticationFailed")
            return await real(share_name, directory_path)

        ds.listed.clear()
        ds.list_directories_and_files = expire_after_root
        with pytest.raises(CredentialsRejectedError):
            await _sync(conn)
        assert set(proc.records) == before
        # The walk stopped: not every one of the six folders below docs was tried.
        assert len([d for _, d in ds.listed if d.startswith("docs/")]) < 6
        assert conn.notify.await_args.kwargs["title"] == f"{conn.display_name}: Credentials rejected"

    async def test_refusal_on_a_later_share_keeps_an_earlier_share_deletes(self) -> None:
        ds = FakeDataSource({})
        ds.add("a", "gone.txt")
        ds.add("b", "x.txt")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["a", "b"])
        await _sync(conn)
        before = set(proc.records)

        del ds.shares["a"]["gone.txt"]
        real = ds.list_directories_and_files

        async def refuse_b(share_name: str, directory_path: str = "", **kwargs: object) -> object:
            if share_name == "b":
                return _err("Azure Files API error: AuthenticationFailed (HTTP 403)", "AuthenticationFailed")
            return await real(share_name, directory_path, **kwargs)

        ds.list_directories_and_files = refuse_b
        with pytest.raises(CredentialsRejectedError):
            await _sync(conn)
        assert set(proc.records) == before
        assert proc.deleted == []

    async def test_refused_share_listing_names_the_credentials(self) -> None:
        ds = _tree()
        ds.fail_list_shares = True
        conn = _connector(ds, FakeProcessor())
        with pytest.raises(CredentialsRejectedError):
            await _sync(conn)
        assert "credentials" in conn.notify.await_args.kwargs["message"]

    async def test_other_share_listing_failure_is_a_sync_error_with_a_stable_text(self) -> None:
        ds = _tree()
        ds.list_shares = AsyncMock(return_value=_err("ServerBusy (HTTP 503): The server is busy.", "ServerBusy"))
        conn = _connector(ds, FakeProcessor())
        for _ in range(2):
            with pytest.raises(ConnectionError, match="Failed to list shares"):
                await _sync(conn)
        first, second = (c.kwargs for c in conn.notify.await_args_list)
        # The base class backs off repeats by their text; a request id in it would defeat that.
        assert first == second
        assert first["type"].value == "CONNECTOR_SYNC_ERROR"


# ---------------------------------------------------------------------------
# §2.2 / §2.3: the connection test says what is wrong
# ---------------------------------------------------------------------------
class TestConnectionTestReasons:
    async def test_rejected_credentials(self) -> None:
        ds = _tree()
        ds.list_directories_and_files = AsyncMock(
            return_value=_err("AuthenticationFailed (HTTP 403): Server failed to authenticate.", "AuthenticationFailed")
        )
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = FilterCollection(filters=[
            Filter(key="shares", value=["s"], type=FilterType.MULTISELECT, operator=MultiselectOperator.IN)
        ])
        with pytest.raises(ConnectorInitError, match="rejected the credentials") as raised:
            await conn.test_connection_and_access()
        assert conn.display_name in str(raised.value)
        assert "Connection test failed" in conn.notify.await_args.kwargs["title"]

    async def test_entra_not_found_is_not_taken_for_a_missing_share(self) -> None:
        ds = _tree()
        ds.list_directories_and_files = AsyncMock(return_value=_err(ENTRA_APP_GONE, CREDENTIALS_REJECTED))
        conn = _connector(ds, FakeProcessor())
        conn.sync_filters = FilterCollection(filters=[
            Filter(key="shares", value=["s"], type=FilterType.MULTISELECT, operator=MultiselectOperator.IN)
        ])
        with pytest.raises(ConnectorInitError, match="rejected the credentials"):
            await conn.test_connection_and_access()


# ---------------------------------------------------------------------------
# §13.5: the file endpoint is public and https
# ---------------------------------------------------------------------------
class TestEndpoint:
    def _conn(self, url: str) -> AzureFilesConnector:
        conn = _connector(FakeDataSource({}), FakeProcessor())
        conn.config_service.get_config = AsyncMock(return_value={"auth": {"connectionString": "x"}})
        conn._load_creator_email = AsyncMock()
        client = MagicMock()
        client.get_account_name.return_value = "acct"
        client.get_account_url.return_value = url
        conn._client = client
        return conn

    async def _init(self, conn) -> bool:
        with patch(
            "app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services",
            new=AsyncMock(return_value=conn._client),
        ), patch(
            "app.connectors.sources.azure_files.connector.load_connector_filters",
            new=AsyncMock(return_value=(FilterCollection(), FilterCollection())),
        ):
            return await conn.init()

    async def test_http_endpoint_is_refused(self) -> None:
        conn = self._conn("http://acct.file.core.windows.net")
        with pytest.raises(ConnectorInitError, match="https"):
            await self._init(conn)
        assert conn.data_source is None or isinstance(conn.data_source, FakeDataSource)

    @pytest.mark.parametrize("url", ["https://127.0.0.1:8443", "https://169.254.169.254", "https://10.0.0.5"])
    async def test_internal_endpoint_is_refused(self, url: str) -> None:
        with pytest.raises(ConnectorInitError, match="not a public address"):
            await self._init(self._conn(url))

    async def test_public_https_endpoint_is_accepted(self) -> None:
        with patch("app.connectors.sources.azure_files.connector.validate_public_http_url") as validate:
            assert await self._init(self._conn("https://acct.file.core.windows.net")) is True
        validate.assert_called_once_with("https://acct.file.core.windows.net")

    async def test_endpoint_that_cannot_be_read_is_refused(self) -> None:
        """A SAS connection string whose FileEndpoint is no Azure host has no account name;
        that must not skip the endpoint check."""
        conn = self._conn("")
        conn._client.get_account_name.side_effect = ValueError("no account name")
        conn._client.get_account_url.side_effect = ValueError("no endpoint")
        with pytest.raises(ConnectorInitError, match="could not read the file endpoint"):
            await self._init(conn)
        assert isinstance(conn.data_source, FakeDataSource)

    async def test_missing_account_name_still_checks_the_endpoint(self) -> None:
        conn = self._conn("https://10.0.0.5")
        conn._client.get_account_name.side_effect = ValueError("no account name")
        with pytest.raises(ConnectorInitError, match="not a public address"):
            await self._init(conn)

    async def test_unresolvable_endpoint_is_refused_with_the_reason(self) -> None:
        with patch(
            "app.connectors.sources.azure_files.connector.validate_public_http_url",
            side_effect=FetchError("Could not resolve hostname 'nope.example'"),
        ), pytest.raises(ConnectorInitError, match="Could not resolve"):
            await self._init(self._conn("https://nope.example"))


# ---------------------------------------------------------------------------
# §4.1: a refused credential while opening a file asks the user to reconnect
# ---------------------------------------------------------------------------
class TestStreamErrors:
    def _record(self) -> SimpleNamespace:
        return SimpleNamespace(
            id="r", record_name="a.txt", mime_type="text/plain", is_file=True,
            external_record_group_id="s", external_record_id="s/a.txt",
        )

    @pytest.mark.parametrize(
        "error",
        [
            ClientAuthenticationError(message="AADSTS7000215: Invalid client secret provided."),
            (lambda e: (setattr(e, "error_code", "AuthenticationFailed"), e)[1])(
                ClientAuthenticationError(message="Server failed to authenticate the request.")
            ),
        ],
    )
    async def test_refused_credential_is_a_409(self, error: Exception) -> None:
        ds = FakeDataSource({})
        ds.download_file_stream = AsyncMock(side_effect=error)
        conn = _connector(ds, FakeProcessor())
        with patch("app.connectors.sources.azure_files.connector.FileRecord", SimpleNamespace):
            with pytest.raises(HTTPException) as raised:
                await conn.stream_record(self._record())
        assert raised.value.status_code == HttpStatusCode.CONFLICT.value

    async def test_permission_refusal_stays_a_403(self) -> None:
        error = HttpResponseError(message="This request is not authorized to perform this operation.")
        error.status_code = 403
        error.error_code = "AuthorizationPermissionMismatch"
        ds = FakeDataSource({})
        ds.download_file_stream = AsyncMock(side_effect=error)
        conn = _connector(ds, FakeProcessor())
        with patch("app.connectors.sources.azure_files.connector.FileRecord", SimpleNamespace):
            with pytest.raises(HTTPException) as raised:
                await conn.stream_record(self._record())
        assert raised.value.status_code == HttpStatusCode.FORBIDDEN.value


# ---------------------------------------------------------------------------
# §5.2 / §5.3: folders above a folder filter are real folders, not stubs
# ---------------------------------------------------------------------------
class TestFoldersAboveTheFilter:
    def _ds(self) -> FakeDataSource:
        ds = FakeDataSource({})
        ds.add("s", "a", is_dir=True, file_id="fid-a")
        ds.add("s", "a/b", is_dir=True, file_id="fid-b")
        ds.add("s", "a/b/c.txt")
        ds.add("s", "a/other.txt")

        async def directory_properties(share_name: str, directory_path: str) -> object:
            item = ds.shares[share_name].get(directory_path)
            if not item:
                return _err(f"Directory not found: {share_name}/{directory_path}", "ResourceNotFound")
            return SimpleNamespace(success=True, data={**item, "name": None}, error=None, error_code=None)

        ds.get_directory_properties = directory_properties
        return ds

    def _conn(self, ds, proc) -> AzureFilesConnector:
        conn = _connector(ds, proc, shares=["s"])
        conn._test_filters = FilterCollection(filters=[
            Filter(key="shares", value=["s"], type=FilterType.MULTISELECT, operator=MultiselectOperator.IN),
            Filter(key="folder_paths", value=["a/b"], type=FilterType.LIST, operator=ListOperator.IN),
        ])
        return conn

    async def test_chosen_folder_and_its_parents_are_written_by_name(self) -> None:
        ds = self._ds()
        proc = FakeProcessor()
        await _sync(self._conn(ds, proc))

        a, b = proc.records["s/a"], proc.records["s/a/b"]
        assert (a.record_name, b.record_name) == ("a", "b")
        assert a.parent_external_record_id is None
        assert b.parent_external_record_id == "s/a"
        assert proc.records["s/a/b/c.txt"].parent_external_record_id == "s/a/b"
        assert "s/a/other.txt" not in proc.records
        # Parents first, so the child never lands under a stub.
        first_batch = proc.new_calls[0]
        assert first_batch.index("s/a") < first_batch.index("s/a/b") < first_batch.index("s/a/b/c.txt")

    async def test_unchanged_parents_are_not_rewritten_outside_a_full_sync(self) -> None:
        ds = self._ds()
        proc = FakeProcessor()
        conn = self._conn(ds, proc)
        await _sync(conn)
        proc.new_calls.clear()
        await _sync(conn)
        assert proc.new_calls == []

    async def test_unreadable_parent_is_noted_as_kept(self) -> None:
        ds = self._ds()
        proc = FakeProcessor()
        conn = self._conn(ds, proc)
        await _sync(conn)
        stored = proc.records["s/a"].id
        ds.get_directory_properties = AsyncMock(return_value=_err("denied", "AuthorizationPermissionMismatch"))
        proc.full_sync = True
        await _sync(conn)
        assert stored in proc.kept


# ---------------------------------------------------------------------------
# §3.2: version is the processor's, so an unchanged rewrite does not raise it
# ---------------------------------------------------------------------------
class TestVersion:
    async def test_full_syncs_send_version_zero(self) -> None:
        ds = _tree()
        proc = FakeProcessor()
        proc.full_sync = True
        conn = _connector(ds, proc, shares=["s"])
        for _ in range(3):
            await _sync(conn)
        assert {r.version for r in proc.records.values()} == {0}


# ---------------------------------------------------------------------------
# §11.2 / §12.1: reindex agrees with the sync and logs the vendor's reason
# ---------------------------------------------------------------------------
class TestReindex:
    def _record(self, path: str, *, is_file: bool = True) -> FileRecord:
        return FileRecord(
            id=f"rec-{path}", record_name=path.rsplit("/", 1)[-1], record_type="FILE",
            external_record_group_id="s", external_record_id=f"s/{path}", external_revision_id="old",
            version=0, origin="CONNECTOR", connector_name="AZURE FILES", connector_id="c",
            is_file=is_file, extension=None, mime_type="application/pdf",
        )

    async def test_changed_file_keeps_the_mime_the_sync_gives(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "report.pdf")
        real = ds.get_file_properties

        async def with_octet_stream(share_name: str, file_path: str) -> object:
            response = await real(share_name, file_path)
            response.data["content_type"] = "application/octet-stream"
            return response

        ds.get_file_properties = with_octet_stream
        conn = _connector(ds, FakeProcessor())
        updated, _ = await conn._check_and_fetch_updated_record("org", self._record("report.pdf"))
        assert updated.mime_type == "application/pdf"
        assert updated.version == 0

    async def test_changed_directory_is_a_folder(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "docs.v2", is_dir=True)
        ds.get_directory_properties = AsyncMock(return_value=SimpleNamespace(
            success=True, error=None, error_code=None,
            data={"name": "docs.v2", "file_id": "new-id", "etag": '"e"',
                  "last_modified": datetime(2024, 5, 1, tzinfo=timezone.utc)},
        ))
        conn = _connector(ds, FakeProcessor())
        updated, _ = await conn._check_and_fetch_updated_record("org", self._record("docs.v2", is_file=False))
        assert updated.mime_type == "text/directory"

    async def test_unreadable_item_logs_azure_reason(self, caplog) -> None:
        ds = FakeDataSource({})
        ds.get_file_properties = AsyncMock(return_value=_err(
            "AuthenticationFailed (HTTP 403): Server failed to authenticate the request.", "AuthenticationFailed"
        ))
        conn = _connector(ds, FakeProcessor())
        with caplog.at_level(logging.WARNING):
            assert await conn._check_and_fetch_updated_record("org", self._record("a.txt")) is None
        assert "AuthenticationFailed (HTTP 403)" in caplog.text
        assert "not found" not in caplog.text


    async def test_file_gone_at_source_is_not_requeued(self) -> None:
        ds = FakeDataSource({})
        proc = FakeProcessor()
        proc.reindex_existing_records = AsyncMock()
        conn = _connector(ds, proc)
        await conn.reindex_records([self._record("gone.pdf")])
        proc.reindex_existing_records.assert_not_awaited()


# ---------------------------------------------------------------------------
# §6.11: each directory is listed page by page
# ---------------------------------------------------------------------------
class TestListingPages:
    @pytest.fixture(autouse=True)
    def _small_pages(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_module, "LISTING_PAGE_SIZE", 2)

    async def test_every_page_is_synced(self) -> None:
        ds = FakeDataSource({})
        for name in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]:
            ds.add("s", name)
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        assert {f"s/{n}" for n in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]} <= set(proc.records)
        assert ds.listed.count(("s", "")) == 3

    async def test_a_failed_later_page_deletes_nothing(self) -> None:
        ds = FakeDataSource({})
        ds.add("s", "docs", is_dir=True)
        for name in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]:
            ds.add("s", f"docs/{name}")
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        before = set(proc.records)

        del ds.shares["s"]["docs/a.txt"]
        ds.fail_pages.add(("s", "docs", 2))
        await _sync(conn)
        assert set(proc.records) == before
        assert proc.deleted == []
        assert conn.full_sync_coverage().complete is False

    async def test_a_failed_later_page_moves_nothing(self) -> None:
        """A file on the unread page is not gone, so a same-FileId copy on page 1 must not take its record."""
        ds = FakeDataSource({})
        ds.add("s", "docs", is_dir=True)
        for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
            ds.add("s", f"docs/{name}", file_id="7" if name == "d.txt" else None)
        proc = FakeProcessor()
        conn = _connector(ds, proc, shares=["s"])
        await _sync(conn)
        d_id = proc.records["s/docs/d.txt"].id

        # x.txt replaces a.txt on page 1 as a copy of d.txt; d.txt's page (offset 2) fails.
        tree = ds.shares["s"]
        d = tree["docs/d.txt"]
        ds.shares["s"] = {
            "docs": tree["docs"],
            "docs/x.txt": {**d, "name": "x.txt", "path": "docs/x.txt"},
            **{k: tree[k] for k in ["docs/b.txt", "docs/c.txt", "docs/d.txt"]},
        }
        ds.fail_pages.add(("s", "docs", 2))
        await _sync(conn)
        assert proc.records["s/docs/d.txt"].id == d_id
        assert proc.moved_calls == []


WRITTEN_UTC = datetime(2024, 5, 1, 10, 0, tzinfo=timezone.utc)


class TestNaiveTimes:
    async def test_share_time_without_zone_is_utc(self, monkeypatch) -> None:
        """The SDK parses properties headers into naive datetimes; .timestamp() would read
        them as host-local time and shift them by the host's offset."""
        monkeypatch.setenv("TZ", "Asia/Kolkata")
        time.tzset()
        try:
            ds = FakeDataSource({})
            ds.add("s", "a.txt")
            ds.get_share_properties = AsyncMock(
                return_value=_ok({"name": "s", "last_modified": WRITTEN_UTC.replace(tzinfo=None)})
            )
            conn = _connector(ds, FakeProcessor())
            timestamps, gone = await conn._selected_share_timestamps_and_gone(["s"])
        finally:
            monkeypatch.undo()
            time.tzset()
        assert gone == set()
        assert timestamps["s"] == int(WRITTEN_UTC.timestamp() * 1000)


class TestExtension:
    @pytest.mark.parametrize(
        ("path", "extension"),
        [("reports.2024/README", None), ("reports.2024/a.PDF", "pdf"), ("a.tar.gz", "gz"), ("Makefile", None)],
    )
    def test_extension_comes_from_the_name(self, path: str, extension: str | None) -> None:
        assert get_file_extension(path) == extension


class TestDocumentationLink:
    def test_pipeshub_docs_link_is_the_published_page(self) -> None:
        links = AzureFilesConnector._connector_metadata["config"]["documentationLinks"]
        urls = [link["url"] for link in links]
        assert "https://docs.pipeshub.com/connectors/azure-files/azure-files" in urls
