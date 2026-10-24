"""PP-341: Azure Files data source and client against the real SDK.

The SDK runs end to end over an in-process transport that answers like the Azure
Files REST API, so request shapes (paging, ranges, headers) and the SDK's own
parsing (aware vs naive datetimes) are what the service would see and return.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from azure.core.credentials import AccessToken, AccessTokenInfo
from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport
from azure.core.utils import CaseInsensitiveDict

from app.sources.client.azure.azure_files import (
    AzureFilesClient,
    AzureFilesConnectionStringConfig,
    AzureFilesEntraIdConfig,
    AzureFilesRESTClient,
)
from app.sources.external.azure.azure_files import AzureFilesDataSource

KEY_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=a2V5a2V5a2V5;EndpointSuffix=core.windows.net"
)
WRITTEN = "2024-05-01T10:00:00.1234567Z"
CONTENT = b"0123456789"


class _Body:
    def __init__(self, response) -> None:
        self.response = response
        self._parts = [response.body()] if response.body() else []

    def __aiter__(self) -> "_Body":
        return self

    async def __anext__(self) -> bytes:
        if not self._parts:
            raise StopAsyncIteration
        return self._parts.pop(0)


class _Response(AsyncHttpResponse):
    def __init__(self, request, status, headers, body) -> None:
        super().__init__(request, None)
        self.status_code = status
        self.headers = CaseInsensitiveDict(headers)
        self.reason = "OK"
        self.content_type = self.headers.get("Content-Type")
        self._body = body

    def body(self) -> bytes:
        return self._body

    async def load_body(self) -> None:
        return None

    async def read(self) -> bytes:
        return self._body

    @property
    def content(self) -> bytes:
        return self._body

    def stream_download(self, pipeline: object, **kwargs: object) -> _Body:
        return _Body(self)


class FakeFilesService(AsyncHttpTransport):
    """Answers list shares, list directory, file properties and ranged downloads."""

    def __init__(self) -> None:
        self.requests = []

    async def __aenter__(self) -> "FakeFilesService":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send(self, request: object, **kwargs: object) -> _Response:
        self.requests.append(request)
        query = parse_qs(urlparse(request.url).query)
        if query.get("comp") == ["list"] and "restype" not in query:
            marker = query.get("marker", [""])[0]
            names, next_marker = (["alpha", "beta"], "tok-2") if not marker else (["gamma"], "")
            shares = "".join(
                f"<Share><Name>{n}</Name><Properties><Last-Modified>Wed, 01 May 2024 10:00:00 GMT"
                f"</Last-Modified><Etag>\"0x1\"</Etag><Quota>5</Quota></Properties></Share>"
                for n in names
            )
            body = (
                '<?xml version="1.0" encoding="utf-8"?><EnumerationResults ServiceEndpoint="https://acct.file.core.windows.net/">'
                f"<Shares>{shares}</Shares><NextMarker>{next_marker}</NextMarker></EnumerationResults>"
            ).encode()
            return _Response(request, 200, {"Content-Type": "application/xml"}, body)
        if query.get("comp") == ["list"]:
            body = (
                '<?xml version="1.0" encoding="utf-8"?><EnumerationResults ServiceEndpoint="https://acct.file.core.windows.net/" ShareName="alpha" DirectoryPath="">'
                "<Entries><File><FileId>13835128424026341376</FileId><Name>a.txt</Name><Properties>"
                f"<Content-Length>{len(CONTENT)}</Content-Length><CreationTime>{WRITTEN}</CreationTime>"
                f"<LastAccessTime>{WRITTEN}</LastAccessTime><LastWriteTime>{WRITTEN}</LastWriteTime>"
                f"<ChangeTime>{WRITTEN}</ChangeTime><Last-Modified>Wed, 01 May 2024 10:00:00 GMT</Last-Modified>"
                "<Etag>\"0x9\"</Etag></Properties></File></Entries><NextMarker /></EnumerationResults>"
            ).encode()
            return _Response(request, 200, {"Content-Type": "application/xml"}, body)
        if query.get("restype") == ["share"]:
            return _Response(request, 200, {
                "ETag": '"0x1"', "Last-Modified": "Wed, 01 May 2024 10:00:00 GMT", "x-ms-share-quota": "5",
            }, b"")
        headers = {
            "ETag": '"0x9"',
            "Last-Modified": "Wed, 01 May 2024 10:00:00 GMT",
            "x-ms-type": "File",
            "x-ms-file-id": "13835128424026341376",
            "x-ms-file-last-write-time": WRITTEN,
            "x-ms-file-creation-time": WRITTEN,
            "x-ms-file-change-time": WRITTEN,
        }
        if request.method == "HEAD":
            return _Response(request, 200, {**headers, "Content-Length": str(len(CONTENT))}, b"")
        start, end = (int(x) for x in request.headers["x-ms-range"].split("=")[1].split("-"))
        part = CONTENT[start:end + 1]
        return _Response(request, 206, {
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(part)),
            "Content-Range": f"bytes {start}-{start + len(part) - 1}/{len(CONTENT)}",
        }, part)


def _data_source(service: "FakeFilesService", **client_kwargs: object) -> AzureFilesDataSource:
    config = AzureFilesConnectionStringConfig(connectionString=KEY_CONNECTION_STRING)
    from azure.storage.fileshare.aio import ShareServiceClient

    async def create() -> object:
        return ShareServiceClient.from_connection_string(KEY_CONNECTION_STRING, transport=service, **client_kwargs)

    config.create_async_share_service_client = create
    return AzureFilesDataSource(AzureFilesClient(AzureFilesRESTClient(config)))


class TestListSharesPage:
    async def test_pages_by_continuation_token(self) -> None:
        service = FakeFilesService()
        ds = _data_source(service)
        first = await ds.list_shares_page(results_per_page=2)
        assert [s["name"] for s in first.data["shares"]] == ["alpha", "beta"]
        assert first.data["next"] == "tok-2"
        query = parse_qs(urlparse(service.requests[-1].url).query)
        assert query["maxresults"] == ["2"]

        second = await ds.list_shares_page(results_per_page=2, continuation_token="tok-2", name_starts_with="ga")
        assert [s["name"] for s in second.data["shares"]] == ["gamma"]
        assert second.data["next"] is None
        query = parse_qs(urlparse(service.requests[-1].url).query)
        assert query["marker"] == ["tok-2"] and query["prefix"] == ["ga"]
        # One request per page: the dropdown no longer lists every share.
        assert len(service.requests) == 2


class TestDownloadFileStream:
    async def test_chunks_arrive_by_range_not_all_at_once(self) -> None:
        service = FakeFilesService()
        ds = _data_source(service, max_single_get_size=4, max_chunk_get_size=4)
        downloader = await ds.download_file_stream("alpha", "a.txt")
        chunks = [chunk async for chunk in downloader.chunks()]
        assert b"".join(chunks) == CONTENT
        assert max(len(c) for c in chunks) <= 4
        ranges = [r.headers["x-ms-range"] for r in service.requests if r.method == "GET"]
        assert ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]

    async def test_connector_streams_with_a_real_connection_string_client(self) -> None:
        """PP-351: a key connection string, the default setup, no longer reads the
        whole file into memory before the first byte is sent."""
        import logging
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.connectors.sources.azure_files.connector import AzureFilesConnector

        service = FakeFilesService()
        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            conn = AzureFilesConnector(
                logger=logging.getLogger("test"), data_entities_processor=MagicMock(),
                data_store_provider=MagicMock(), config_service=MagicMock(),
                connector_id="c", scope="personal", created_by="u",
            )
        conn.data_source = _data_source(service, max_single_get_size=4, max_chunk_get_size=4)
        record = SimpleNamespace(
            id="r", record_name="a.txt", mime_type="text/plain", is_file=True,
            external_record_group_id="alpha", external_record_id="alpha/a.txt",
        )
        with patch("azure.storage.fileshare.aio._download_async.StorageStreamDownloader.readall") as readall:
            response = await conn.stream_record(record)
            body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == CONTENT
        readall.assert_not_called()
        assert len([r for r in service.requests if r.method == "GET"]) == 3


class TestRevisionShapes:
    async def test_listing_and_properties_give_the_same_revision(self) -> None:
        """PP-363: the SDK returns an aware time from a listing and a naive one from
        get_file_properties; the revision must not depend on which was read."""
        import logging
        from unittest.mock import MagicMock

        from app.connectors.sources.azure_files.connector import AzureFilesConnector

        ds = _data_source(FakeFilesService())
        listed = (await ds.list_directories_and_files("alpha", "")).data[0]
        props = (await ds.get_file_properties("alpha", "a.txt")).data
        assert listed["last_write_time"].tzinfo is not None
        assert props["last_write_time"].tzinfo is None  # the SDK shape the ticket names

        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            conn = AzureFilesConnector(
                logger=logging.getLogger("test"), data_entities_processor=MagicMock(),
                data_store_provider=MagicMock(), config_service=MagicMock(),
                connector_id="c", scope="personal", created_by="u",
            )
        assert conn._get_azure_files_revision_id(listed) == conn._get_azure_files_revision_id(props)


class TestSasUrl:
    async def test_key_connection_string_signs_short_lived_encoded_urls(self) -> None:
        from datetime import datetime, timedelta, timezone

        ds = _data_source(FakeFilesService())
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        response = await ds.generate_file_sas_url("alpha", "dir/a #1?.txt", expiry=expiry)
        assert response.success, response.error
        url = urlparse(response.data["sas_url"])
        assert url.netloc == "acct.file.core.windows.net"
        assert url.path == "/alpha/dir/a%20%231%3F.txt"
        assert unquote(url.path) == "/alpha/dir/a #1?.txt"
        assert parse_qs(url.query)["sp"] == ["r"]

    async def test_sas_connection_string_has_no_key(self) -> None:
        config = AzureFilesConnectionStringConfig(
            connectionString="FileEndpoint=https://acct.file.core.usgovcloudapi.net/;SharedAccessSignature=sv=1&sig=x"
        )
        ds = AzureFilesDataSource(AzureFilesClient(AzureFilesRESTClient(config)))
        response = await ds.generate_file_sas_url("alpha", "a.txt")
        assert not response.success and "Account key required" in response.error
        # Sovereign cloud endpoint and account name come from the string, not core.windows.net.
        client = AzureFilesClient(AzureFilesRESTClient(config))
        assert client.get_account_url() == "https://acct.file.core.usgovcloudapi.net"
        assert client.get_account_name() == "acct"


class _FakeCredential:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.closed = False

    async def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        return AccessToken("entra-token", 4_102_444_800)

    async def get_token_info(self, *scopes: str, **kwargs: object) -> AccessTokenInfo:

        return AccessTokenInfo("entra-token", 4_102_444_800)

    async def close(self) -> None:
        self.closed = True


class TestEntraId:
    async def test_builds_from_config_and_sends_backup_intent_bearer(self) -> None:
        config = {"auth": {
            "authType": "ENTRA_ID", "accountUrl": "https://acct.file.core.windows.net/",
            "tenantId": "t", "clientId": "c", "clientSecret": "s",
        }}

        class _Config:
            async def get_config(self, path: str) -> dict:
                return config

        client = await AzureFilesClient.build_from_services(
            logger=__import__("logging").getLogger("t"), config_service=_Config(), connector_instance_id="x"
        )
        assert isinstance(client.get_client().config, AzureFilesEntraIdConfig)
        assert client.get_account_url() == "https://acct.file.core.windows.net"
        assert client.get_account_name() == "acct"
        assert client.get_account_key() is None

        service = FakeFilesService()
        with patch("azure.identity.aio.ClientSecretCredential", _FakeCredential):
            sdk_client = await client.get_async_share_service_client()
        sdk_client._client._config.transport = service
        sdk_client._pipeline._transport = service
        credential = sdk_client.credential
        assert credential.args == ("t", "c", "s")
        ds = AzureFilesDataSource(client)
        # Every call the connector makes: the share-level ones too, which take OAuth
        # from API version 2024-11-04 (the SDK's) with the backup intent.
        calls = [
            ds.list_directories_and_files("alpha", ""),
            ds.list_shares(),
            ds.list_shares_page(results_per_page=2),
            ds.get_share_properties("alpha"),
            ds.get_file_properties("alpha", "a.txt"),
        ]
        for call in calls:
            response = await call
            assert response.success, response.error
        assert len(service.requests) >= len(calls)
        for request in service.requests:
            assert request.headers["Authorization"] == "Bearer entra-token", request.url
            assert request.headers["x-ms-file-request-intent"] == "backup", request.url
            # Share and service operations take OAuth only from this version on.
            assert request.headers["x-ms-version"] >= "2024-11-04", request.url
        await ds.close_async_client()
        assert credential.closed is True

    def test_missing_fields_are_refused(self) -> None:
        from app.sources.client.azure.azure_files import AzureFilesConfigurationError

        with pytest.raises(AzureFilesConfigurationError, match="clientSecret"):
            AzureFilesEntraIdConfig(accountUrl="https://a.file.core.windows.net", tenantId="t", clientId="c", clientSecret="")
