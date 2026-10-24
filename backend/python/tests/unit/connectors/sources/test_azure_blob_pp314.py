"""Azure Blob fixes from the PP-314 review: download links, endpoints, auth, reindex, streaming.

The SAS tests sign with the real SDK and an account key, which needs no network.
"""

import base64
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.models.entities import FileRecord, RecordType
from app.sources.client.azure.azure_blob import (
    AzureBlobClient,
    AzureBlobConnectionStringConfig,
    AzureBlobEntraIdConfig,
    AzureBlobRESTClient,
)
from app.sources.external.azure.azure_blob import AzureBlobDataSource

KEY = base64.b64encode(b"k" * 32).decode()
PUBLIC = f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={KEY};EndpointSuffix=core.windows.net"
CHINA = f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={KEY};EndpointSuffix=core.chinacloudapi.cn"
AZURITE = f"DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey={KEY};BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
SAS_ONLY = "BlobEndpoint=https://acct.blob.core.windows.net/;SharedAccessSignature=sv=2022-11-02&ss=b&srt=co&sp=rl&sig=abc"


def data_source(connection_string: str) -> AzureBlobDataSource:
    return AzureBlobDataSource(AzureBlobClient(AzureBlobRESTClient(AzureBlobConnectionStringConfig(connection_string))))


def connector(scope: str = "personal") -> AzureBlobConnector:
    proc = MagicMock()
    proc.org_id = "org-1"
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        c = AzureBlobConnector(logging.getLogger("test.pp314"), proc, MagicMock(), AsyncMock(), "az-1", scope, "user-1")
    c.notify = AsyncMock()
    return c


def record(name: str, container: str = "docs", **kw) -> FileRecord:
    return FileRecord(
        id="r1", record_name=name.split("/")[-1], record_type=RecordType.FILE, external_record_id=f"{container}/{name}",
        external_record_group_id=container, version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.AZURE_BLOB,
        connector_id="az-1", is_file=True, **kw,
    )


class TestDownloadLinks:
    """PP-317 / PP-319 / PP-325."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["Invoice #123.pdf", "what?.txt", "100%25.txt", "a b/ü.txt"])
    async def test_the_blob_name_is_encoded_in_the_path_and_the_sas_kept_whole(self, name) -> None:
        response = await data_source(PUBLIC).generate_blob_sas_url(
            container_name="docs", blob_name=name, permission="r", expiry=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        url = urlsplit(response.data["sas_url"])
        assert url.path == f"/docs/{quote(name)}"
        assert url.fragment == ""
        assert "sig" in parse_qs(url.query) and parse_qs(url.query)["sp"] == ["r"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("connection_string, base", [
        (PUBLIC, "https://acct.blob.core.windows.net"),
        (CHINA, "https://acct.blob.core.chinacloudapi.cn"),
        (AZURITE, "http://127.0.0.1:10000/devstoreaccount1"),
    ])
    async def test_links_and_web_urls_use_the_accounts_own_endpoint(self, connection_string, base) -> None:
        response = await data_source(connection_string).generate_blob_sas_url(
            container_name="docs", blob_name="a.txt", permission="r", expiry=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        assert response.data["sas_url"].startswith(f"{base}/docs/a.txt?")
        client = AzureBlobClient(AzureBlobRESTClient(AzureBlobConnectionStringConfig(connection_string)))
        assert client.get_account_url() == base

    def test_the_account_name_comes_from_a_blob_endpoint_only_string(self) -> None:
        client = AzureBlobClient(AzureBlobRESTClient(AzureBlobConnectionStringConfig(SAS_ONLY)))
        assert client.get_account_name() == "acct"

    @pytest.mark.asyncio
    async def test_get_signed_url_passes_the_stored_name_as_is_with_a_short_https_link(self) -> None:
        c = connector()
        c.account_url = "https://acct.blob.core.windows.net"
        c.data_source = MagicMock()
        c.data_source.generate_blob_sas_url = AsyncMock(return_value=SimpleNamespace(success=True, data={"sas_url": "u", "expiry": "t"}))

        await c.get_signed_url(record("x/100%25 #1.txt"))

        kwargs = c.data_source.generate_blob_sas_url.await_args.kwargs
        assert kwargs["blob_name"] == "x/100%25 #1.txt"
        assert kwargs["protocol"] == "https"
        assert kwargs["expiry"] - datetime.now(timezone.utc) <= timedelta(minutes=15)

    @pytest.mark.asyncio
    async def test_an_http_endpoint_does_not_get_an_https_only_link(self) -> None:
        c = connector()
        c.account_url = "http://127.0.0.1:10000/devstoreaccount1"
        c.data_source = MagicMock()
        c.data_source.generate_blob_sas_url = AsyncMock(return_value=SimpleNamespace(success=True, data={"sas_url": "u", "expiry": "t"}))

        await c.get_signed_url(record("a.txt"))

        assert c.data_source.generate_blob_sas_url.await_args.kwargs["protocol"] is None


class TestLeastPrivilegeCredentials:
    """PP-323 / PP-324 / PP-330."""

    @pytest.mark.asyncio
    async def test_a_sas_connection_string_downloads_with_its_own_token(self) -> None:
        response = await data_source(SAS_ONLY).generate_blob_sas_url(
            container_name="docs", blob_name="a #1.txt", permission="r", expiry=datetime.now(timezone.utc),
        )

        assert response.success
        url = urlsplit(response.data["sas_url"])
        assert url.path == f"/docs/{quote('a #1.txt')}"
        assert parse_qs(url.query)["sig"] == ["abc"]

    @pytest.mark.asyncio
    async def test_entra_id_signs_with_one_cached_user_delegation_key(self) -> None:
        ds = AzureBlobDataSource(MagicMock())
        key = SimpleNamespace(
            signed_oid="o", signed_tid="t", signed_start="2026-01-01T00:00:00Z", signed_service="b", signed_version="2022-11-02",
            signed_expiry=(datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            value=KEY, signed_delegated_user_tid=None,
        )
        service = MagicMock(account_name="acct", credential=SimpleNamespace(get_token=lambda *a: None))
        service.get_blob_client.return_value = SimpleNamespace(url="https://acct.blob.core.windows.net/docs/a.txt")
        service.get_user_delegation_key = AsyncMock(return_value=key)
        ds._async_blob_service_client = service
        expiry = datetime.now(timezone.utc) + timedelta(minutes=15)

        first = await ds.generate_blob_sas_url(container_name="docs", blob_name="a.txt", permission="r", expiry=expiry)
        second = await ds.generate_blob_sas_url(container_name="docs", blob_name="a.txt", permission="r", expiry=expiry)

        assert first.success and second.success, first.error
        assert "skoid=o" in first.data["sas_url"]
        service.get_user_delegation_key.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_from_services_builds_an_entra_id_client(self) -> None:
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"auth": {
            "authType": "ENTRA_ID", "accountUrl": "https://acct.blob.core.windows.net/",
            "tenantId": "t", "clientId": "c", "clientSecret": "s",
        }})

        client = await AzureBlobClient.build_from_services(logging.getLogger("t"), config, "az-1")

        assert client.get_authentication_method() == "entra_id"
        assert client.get_account_name() == "acct"
        assert client.get_account_url() == "https://acct.blob.core.windows.net"

    def test_entra_id_needs_every_field(self) -> None:
        from app.sources.client.azure.azure_blob import AzureBlobConfigurationError

        with pytest.raises(AzureBlobConfigurationError, match="clientSecret"):
            AzureBlobEntraIdConfig(accountUrl="https://a.blob.core.windows.net", tenantId="t", clientId="c", clientSecret="")

    def test_setup_offers_entra_id_warns_about_the_account_key_and_declares_limits(self) -> None:
        meta = AzureBlobConnector._connector_metadata
        assert meta["supportedAuthTypes"] == ["CONNECTION_STRING", "ENTRA_ID"]
        field = meta["config"]["auth"]["schemas"]["CONNECTION_STRING"]["fields"][0]
        assert "Read and List" in field["description"] and "full" in field["description"]
        assert "Storage Blob Data Reader" in meta["connectorInfo"]
        assert meta["resilienceConfig"]["rate_limit"] > 0


class TestReindex:
    """PP-318: reindex compares the revision sync stores (Content-MD5), not the etag."""

    @staticmethod
    def props(md5: bytes, etag: str = '"0x8D"') -> SimpleNamespace:
        return SimpleNamespace(
            name="a.txt", etag=etag, size=4, last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), creation_time=None,
            content_settings=SimpleNamespace(content_type="text/plain", content_md5=bytearray(md5)), metadata={}, blob_tier="Hot",
        )

    @pytest.mark.asyncio
    async def test_an_unchanged_blob_is_not_rewritten(self) -> None:
        c = connector()
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock(return_value=SimpleNamespace(success=True, data=self.props(b"m" * 16)))
        md5 = base64.b64encode(b"m" * 16).decode()

        assert await c._check_and_fetch_updated_record("org-1", record("a.txt", external_revision_id=md5)) is None

    @pytest.mark.asyncio
    async def test_a_changed_blob_keeps_md5_and_content_type(self) -> None:
        c = connector()
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock(return_value=SimpleNamespace(success=True, data=self.props(b"n" * 16)))

        updated, _ = await c._check_and_fetch_updated_record(
            "org-1", record("a.txt", external_revision_id=base64.b64encode(b"m" * 16).decode()),
        )

        new_md5 = base64.b64encode(b"n" * 16).decode()
        assert updated.external_revision_id == new_md5
        assert updated.md5_hash == new_md5
        assert updated.mime_type == "text/plain"
        assert updated.etag == "0x8D"

    @pytest.mark.asyncio
    async def test_a_missing_blob_publishes_no_reindex(self) -> None:
        c = connector()
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock(return_value=SimpleNamespace(success=False, data=None, error="BlobNotFound"))
        c.data_entities_processor.reindex_existing_records = AsyncMock()
        c.data_entities_processor.on_new_records = AsyncMock()

        await c.reindex_records([record("gone.txt")])

        c.data_entities_processor.reindex_existing_records.assert_not_awaited()
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_data_source_reads_blob_properties(self) -> None:
        # It passed `versionid=`, which the SDK rejects, so every reindex lookup failed.
        ds = AzureBlobDataSource(MagicMock())
        blob = MagicMock()
        blob.get_blob_properties = AsyncMock(return_value="props")
        ds._async_blob_service_client = MagicMock()
        ds._async_blob_service_client.get_blob_client.return_value = blob

        response = await ds.get_blob_properties(container_name="docs", blob_name="a.txt")

        assert response.success and response.data == "props"
        assert ds._async_blob_service_client.get_blob_client.call_args.kwargs["version_id"] is None


class TestConnectionTest:
    """PP-331: the test checks blob listing and download links, not only the container list."""

    @staticmethod
    def wired(sign_ok: bool = True, list_ok: bool = True, containers=("docs",)) -> AzureBlobConnector:
        c = connector()
        ds = MagicMock()
        ds.list_containers = AsyncMock(return_value=SimpleNamespace(success=True, data=[{"name": n} for n in containers]))

        class Pages:
            def by_page(self) -> AsyncIterator[list]:
                async def pages() -> AsyncIterator[list]:
                    yield []
                return pages()
        ds.list_blobs = AsyncMock(return_value=SimpleNamespace(success=list_ok, data=Pages(), error="AuthorizationPermissionMismatch"))
        ds.generate_blob_sas_url = AsyncMock(return_value=SimpleNamespace(success=sign_ok, data={}, error="Account key required"))
        c.data_source = ds
        return c

    @pytest.mark.asyncio
    async def test_it_passes_when_everything_works(self) -> None:
        assert await self.wired().test_connection_and_access()

    @pytest.mark.asyncio
    async def test_it_fails_when_no_download_link_can_be_made(self) -> None:
        c = self.wired(sign_ok=False)
        with pytest.raises(ConnectorInitError, match="download links"):
            await c.test_connection_and_access()
        assert "download links" in c.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_it_fails_when_blobs_cannot_be_listed(self) -> None:
        with pytest.raises(ConnectorInitError, match="Cannot list blobs in docs"):
            await self.wired(list_ok=False).test_connection_and_access()

    @pytest.mark.asyncio
    async def test_a_deleted_named_container_does_not_fail_it(self) -> None:
        from app.connectors.core.registry.filters import FilterCollection

        class Gone(Exception):
            error_code = "ContainerNotFound"

        class Pages:
            def __init__(self, gone: bool) -> None:
                self.gone = gone

            def by_page(self) -> AsyncIterator[list]:
                async def pages() -> AsyncIterator[list]:
                    if self.gone:
                        raise Gone("The specified container does not exist.")
                    yield []
                return pages()
        c = self.wired()
        c.data_source.list_blobs = AsyncMock(side_effect=lambda container_name, **_: SimpleNamespace(
            success=True, data=Pages(container_name == "old"),
        ))
        c.sync_filters = FilterCollection.from_dict(
            {"containers": {"operator": "in", "value": ["old", "docs"], "type": "multiselect"}}, logging.getLogger("t"),
        )

        assert await c.test_connection_and_access()
        assert c.data_source.list_blobs.await_count == 2

    @pytest.mark.asyncio
    async def test_named_containers_need_no_container_list(self) -> None:
        from app.connectors.core.registry.filters import FilterCollection

        c = self.wired()
        c.data_source.list_containers = AsyncMock(side_effect=AssertionError("not needed"))
        c.sync_filters = FilterCollection.from_dict(
            {"containers": {"operator": "in", "value": ["docs"], "type": "multiselect"}}, logging.getLogger("t"),
        )

        assert await c.test_connection_and_access()
        assert c.data_source.list_blobs.await_args.kwargs == {"container_name": "docs", "maxresults": 1}


class TestContainerOptions:
    """PP-336: options come a page at a time from Azure, filtered there by prefix."""

    @pytest.mark.asyncio
    async def test_cursor_and_search_go_to_azure(self) -> None:
        c = connector()
        c.data_source = MagicMock()
        c.data_source.list_containers_page = AsyncMock(
            return_value=SimpleNamespace(success=True, data={"names": ["fin-a", "fin-b"], "continuation_token": "next"}),
        )

        response = await c.get_filter_options("containers", page=2, limit=2, search="fin", cursor="tok")

        c.data_source.list_containers_page.assert_awaited_once_with(prefix="fin", results_per_page=2, continuation_token="tok")
        assert [o.id for o in response.options] == ["fin-a", "fin-b"]
        assert response.cursor == "next" and response.has_more


class TestStreaming:
    """PP-329 / PP-325 / PP-338."""

    @staticmethod
    def session(status: int, headers: dict | None = None) -> MagicMock:
        response = MagicMock(status=status, headers=headers or {})
        response.text = AsyncMock(return_value="")

        async def chunks(_size: int) -> AsyncIterator[bytes]:
            yield b"data"
        response.content.iter_chunked = chunks
        get_cm = MagicMock()
        get_cm.__aenter__ = AsyncMock(return_value=response)
        get_cm.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = get_cm
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=False)
        return session_cm

    @pytest.mark.asyncio
    async def test_a_slow_large_download_is_not_cut_off_and_the_sas_is_not_logged(self, caplog) -> None:
        from app.utils import streaming

        with patch.object(streaming.aiohttp, "ClientSession", return_value=self.session(200)) as cls, \
                caplog.at_level(logging.DEBUG):
            chunks = [c async for c in streaming.stream_content("https://a.blob.core.windows.net/c/b?sig=SECRET")]

        assert chunks == [b"data"]
        timeout = cls.call_args.kwargs["timeout"]
        assert timeout.total is None and timeout.sock_read and timeout.connect
        assert "SECRET" not in caplog.text

    @pytest.mark.asyncio
    async def test_an_archived_blob_says_so(self) -> None:
        from app.utils import streaming

        with patch.object(streaming.aiohttp, "ClientSession", return_value=self.session(409, {"x-ms-error-code": "BlobArchived"})):
            with pytest.raises(HTTPException) as e:
                [c async for c in streaming.stream_content("https://a/c/b?sig=x", connector="Azure Blob")]

        assert e.value.status_code == 422
        assert "Archive" in e.value.detail and "Rehydrate" in e.value.detail


class TestRenameKeepsIndexing:
    """PP-337: a move that keeps its record id and content is not indexed again."""

    @staticmethod
    def tx(stored: dict) -> AsyncMock:
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        tx.get_record_by_key = AsyncMock(return_value=stored)
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx.get_edges_from_node = AsyncMock(return_value=[])
        return tx

    @staticmethod
    def stored(**overrides) -> dict:
        doc = record("old/a.txt", external_revision_id="md5==").to_arango_base_record()
        doc.update({"indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": "vr-1"}, **overrides)
        return doc

    @staticmethod
    async def process(stored: dict) -> FileRecord:
        proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
        proc.org_id = "org-1"
        moved = record("new/a.txt", external_revision_id="md5==")
        with patch.object(proc, "_handle_record_group", AsyncMock(return_value=None)):
            await proc._process_record(moved, [], TestRenameKeepsIndexing.tx(stored))
        return moved

    @pytest.mark.asyncio
    async def test_same_content_stays_completed_with_its_vectors(self) -> None:
        moved = await self.process(self.stored())

        assert moved.indexing_status == ProgressStatus.COMPLETED.value
        assert moved.virtual_record_id == "vr-1"

    @pytest.mark.asyncio
    async def test_changed_content_is_indexed_again(self) -> None:
        moved = await self.process(self.stored(externalRevisionId="other=="))

        assert moved.indexing_status != ProgressStatus.COMPLETED.value


class TestReviewFollowUps:
    """Defects found reviewing the PP-314 change itself."""

    @pytest.mark.asyncio
    async def test_container_groups_link_to_the_account_endpoint(self) -> None:
        c = connector()
        c.account_url = "https://acct.blob.core.chinacloudapi.cn"
        c.data_entities_processor.on_new_record_groups = AsyncMock()

        await c._create_record_groups_for_containers(["docs"])

        group = c.data_entities_processor.on_new_record_groups.await_args.args[0][0][0]
        assert group.web_url == "https://acct.blob.core.chinacloudapi.cn/docs"

    @pytest.mark.asyncio
    async def test_two_containers_cannot_both_take_one_record_for_a_move(self) -> None:
        import asyncio

        c = connector()
        c._reset_run_state()
        held = record("gone.txt", container="a", external_revision_id="md5==")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c.data_entities_processor.get_record_by_external_revision_id = AsyncMock(return_value=held)
        c.data_entities_processor.delete_parent_child_edge_to_record = AsyncMock()

        async def slow_check(_record) -> bool:
            await asyncio.sleep(0.01)
            return False
        c._still_in_container = slow_check
        blob = {"name": "x.txt", "content_md5": "md5==", "etag": "e", "size": 1}

        (first, _), (second, _) = await asyncio.gather(
            c._process_azure_blob(dict(blob), "b"), c._process_azure_blob(dict(blob), "c"),
        )

        assert sorted([first.id == held.id, second.id == held.id]) == [False, True]

    @pytest.mark.asyncio
    async def test_a_folder_record_is_not_refetched_on_reindex(self) -> None:
        c = connector()
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock()
        folder = record("a/b")
        folder.is_file = False

        assert await c._check_and_fetch_updated_record("org-1", folder) is None
        c.data_source.get_blob_properties.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_closing_the_data_source_closes_the_entra_id_credential(self) -> None:
        rest = AzureBlobRESTClient(AzureBlobEntraIdConfig(
            accountUrl="https://acct.blob.core.windows.net", tenantId="t", clientId="c", clientSecret="s",
        ))
        ds = AzureBlobDataSource(AzureBlobClient(rest))
        service = await ds._get_async_blob_service_client()
        credential = service.credential
        credential.close = AsyncMock()

        await ds.close_async_client()

        credential.close.assert_awaited_once()
        assert rest._async_blob_service_client is None

    @pytest.mark.asyncio
    async def test_a_move_to_another_group_is_indexed_again(self) -> None:
        stored = TestRenameKeepsIndexing.stored(externalGroupId="other-container")

        moved = await TestRenameKeepsIndexing.process(stored)

        assert moved.indexing_status != ProgressStatus.COMPLETED.value


class TestCodeRabbitFollowUps:
    """PR #4393 review."""

    @pytest.mark.asyncio
    async def test_a_reused_connection_string_sas_is_streamed_but_never_handed_out(self) -> None:
        c = connector()
        c.data_source = data_source(SAS_ONLY)

        assert await c.get_signed_url(record("a.txt")) is None
        url, minted = await c._download_url(record("a.txt"))
        assert url.startswith("https://acct.blob.core.windows.net/docs/a.txt?") and not minted

    @pytest.mark.asyncio
    async def test_a_rename_to_another_file_type_is_indexed_again(self) -> None:
        stored = TestRenameKeepsIndexing.stored(recordName="a.md", mimeType="text/markdown")

        moved = await TestRenameKeepsIndexing.process(stored)

        assert moved.indexing_status != ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_each_listing_page_takes_a_rate_limit_token(self) -> None:
        from azure.core.async_paging import AsyncItemPaged, AsyncList

        pages = [["a", "b"], ["c"]]

        async def get_next(token: str | None) -> int:
            return int(token or 0)

        async def extract(i: int) -> tuple[str | None, AsyncList]:
            return (str(i + 1) if i + 1 < len(pages) else None), AsyncList(pages[i])
        c = connector()
        tokens = []

        class Guard:
            async def __aenter__(self) -> None:
                tokens.append(1)

            async def __aexit__(self, *exc: object) -> bool:
                return False
        c._resilience, c._resilience_loaded = SimpleNamespace(guard=Guard), True

        blobs = [b async for b in c._paced(AsyncItemPaged(get_next, extract))]

        assert blobs == ["a", "b", "c"]
        assert len(tokens) == 3  # two pages, then the call that finds the listing done

    @pytest.mark.asyncio
    async def test_a_copy_being_checked_does_not_stop_a_real_move_elsewhere(self) -> None:
        import asyncio

        c = connector()
        c._reset_run_state()
        held = record("gone.txt", container="a", external_revision_id="md5==")
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c.data_entities_processor.get_record_by_external_revision_id = AsyncMock(return_value=held)
        c.data_entities_processor.delete_parent_child_edge_to_record = AsyncMock()
        answers = iter([True, False])  # first check: the original is still there (a copy); then it is gone

        async def check(_record) -> bool:
            await asyncio.sleep(0.01)
            return next(answers)
        c._still_in_container = check
        blob = {"name": "x.txt", "content_md5": "md5==", "etag": "e", "size": 1}

        (copy, _), (move, _) = await asyncio.gather(
            c._process_azure_blob(dict(blob), "b"), c._process_azure_blob(dict(blob), "c"),
        )

        assert copy.id != held.id
        assert move.id == held.id
