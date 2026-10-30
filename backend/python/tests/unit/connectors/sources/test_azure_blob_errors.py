"""Azure Blob errors: what reaches the log, the user and the connection test.

Azure's error text for a request signed with the wrong key holds the request's SharedKey
signature, the string it signed and the XML response body. Only the HTTP status, Azure's
error code and the first line of the message may leave the data source.
"""

import base64
import logging
import socket
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
)
from azure.storage.blob._generated.models import AccessTier, StorageErrorCode

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.azure_blob.connector import AzureBlobConnector
from app.models.entities import FileRecord, RecordType
from app.sources.client.azure.azure_blob import (
    AzureBlobClient,
    AzureBlobConnectionStringConfig,
    AzureBlobRESTClient,
)
from app.sources.client.azure.connection_string import (
    ACCOUNT_NOT_FOUND,
    CREDENTIALS_REFUSED,
    INVALID_ACCOUNT_KEY,
)
from app.sources.external.azure.azure_blob import (
    AzureBlobDataSource,
    azure_error_code,
    azure_error_text,
)

SIGNATURE = "wJS3D2tZeiD9AeonSNQJ22XsR0xujOTKCFOjMXPW+k8="
AUTH_FAILURE = (
    "Server failed to authenticate the request. Make sure the value of Authorization header is formed "
    "correctly including the signature.\nRequestId:62830b83-c01e-0003-1cd8-62b499000000\n"
    "Time:2026-10-23T10:24:58.0798308Z\nErrorCode:AuthenticationFailed\n"
    f"authenticationerrordetail:The MAC signature found in the HTTP request '{SIGNATURE}' is not the same "
    "as any computed signature. Server used following string to sign: 'GET\n\nx-ms-date:Fri, 23 Oct 2026 "
    "10:24:57 GMT\n/acct/docs\ncomp:list\nrestype:container'.\nContent: <?xml version=\"1.0\" "
    "encoding=\"utf-8\"?><Error><Code>AuthenticationFailed</Code><Message>Server failed to authenticate "
    "the request.</Message></Error>"
)
KEY = base64.b64encode(b"k" * 32).decode()
PUBLIC = f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={KEY};EndpointSuffix=core.windows.net"


def auth_failure() -> ClientAuthenticationError:
    error = ClientAuthenticationError(message=AUTH_FAILURE)
    # The SDK sets these from the response; its error code is a StorageErrorCode.
    error.status_code, error.error_code = 403, StorageErrorCode.AUTHENTICATION_FAILED
    return error


def assert_nothing_secret(text: str) -> None:
    for leak in (SIGNATURE, "string to sign", "<?xml", "RequestId", "x-ms-date"):
        assert leak not in text


class TestErrorText:

    def test_refused_credentials_say_so_and_keep_status_and_code(self) -> None:
        text = azure_error_text(auth_failure())

        assert text == f"{CREDENTIALS_REFUSED} (HTTP 403 AuthenticationFailed)"
        assert text != INVALID_ACCOUNT_KEY
        assert azure_error_code(auth_failure()) == "AuthenticationFailed"

    def test_a_network_error_has_no_status_and_no_signed_url(self) -> None:
        error = ServiceRequestError("Cannot reach https://acct.blob.core.windows.net/docs/a.txt?sv=2022&sig=SECRETSIG")

        text = azure_error_text(error)

        assert text.startswith("Cannot reach https://acct.blob.core.windows.net/docs/a.txt")
        assert "SECRETSIG" not in text

    def test_it_is_bounded(self) -> None:
        assert len(azure_error_text(ValueError("x" * 5000))) <= 300

    def test_an_empty_message_names_the_error_type(self) -> None:
        assert azure_error_text(TimeoutError()) == "TimeoutError"


# What the address guard reports for the account endpoint, wrapped by the client builder.
GUARD_REASON = "Failed to create AsyncBlobServiceClient: Could not find a server named 'acct.blob.core.windows.net'."


class TestUnknownAccount:
    """A mistyped AccountName fails in the address guard, before the SDK sends anything."""

    @pytest.fixture(autouse=True)
    def no_proxy(self, monkeypatch) -> None:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)

    @staticmethod
    async def list_with_resolver_error(monkeypatch, errno: int) -> str:
        def getaddrinfo(host: str, *args: object, **kwargs: object) -> NoReturn:
            raise socket.gaierror(errno, "resolver says no")

        monkeypatch.setattr("app.utils.url_fetcher.socket.getaddrinfo", getaddrinfo)
        ds = AzureBlobDataSource(AzureBlobClient(AzureBlobRESTClient(AzureBlobConnectionStringConfig(PUBLIC))))
        response = await ds.list_containers()
        assert not response.success
        return response.error

    @pytest.mark.asyncio
    async def test_a_host_that_does_not_exist_is_named_with_the_host(self, monkeypatch) -> None:
        text = await self.list_with_resolver_error(monkeypatch, socket.EAI_NONAME)

        assert text == f"{ACCOUNT_NOT_FOUND} ({GUARD_REASON})"

    @pytest.mark.asyncio
    async def test_a_temporary_resolver_failure_is_not_called_a_missing_account(self, monkeypatch) -> None:
        text = await self.list_with_resolver_error(monkeypatch, socket.EAI_AGAIN)

        assert text == GUARD_REASON

    def test_a_dns_error_from_the_sdk_is_not_blamed_on_the_account(self) -> None:
        # Behind a proxy the guard does not look the account up; the host that failed may be the proxy.
        reason = "Cannot connect to host proxy.corp:3128 ssl:default [Name or service not known]"

        assert azure_error_text(ServiceRequestError(reason)) == reason


class TestDataSourceResponses:

    @staticmethod
    def failing(**methods: Exception) -> AzureBlobDataSource:
        ds = AzureBlobDataSource(AzureBlobClient(AzureBlobRESTClient(AzureBlobConnectionStringConfig(PUBLIC))))
        container = MagicMock()
        container.get_container_properties = AsyncMock(side_effect=methods.get("container"))
        blob = MagicMock()
        blob.get_blob_properties = AsyncMock(side_effect=methods.get("blob"))
        client = MagicMock()
        client.get_container_client.return_value = container
        client.get_blob_client.return_value = blob
        ds._get_async_blob_service_client = AsyncMock(return_value=client)
        return ds

    @pytest.mark.asyncio
    async def test_container_properties_return_the_short_reason_and_the_code(self) -> None:
        response = await self.failing(container=auth_failure()).get_container_properties("docs")

        assert not response.success
        assert response.error.startswith(CREDENTIALS_REFUSED)
        assert response.error_code == "AuthenticationFailed"
        assert_nothing_secret(response.error)

    @pytest.mark.asyncio
    async def test_blob_properties_do_too(self) -> None:
        response = await self.failing(blob=auth_failure()).get_blob_properties("docs", "a.txt")

        assert response.error_code == "AuthenticationFailed"
        assert_nothing_secret(response.error)


def connector() -> AzureBlobConnector:
    proc = MagicMock()
    proc.org_id = "org-1"
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        c = AzureBlobConnector(logging.getLogger("test.azure_blob.errors"), proc, MagicMock(), AsyncMock(), "az-1", "personal", "user-1")
    c.notify = AsyncMock()
    return c


class TestConnectionTest:
    """The user sees why the connection test failed, not "please verify your credentials"."""

    @staticmethod
    def wired(error: Exception) -> AzureBlobConnector:
        class Pages:
            def by_page(self) -> AsyncIterator[list]:
                async def pages() -> AsyncIterator[list]:
                    raise error
                    yield  # pragma: no cover
                return pages()

        c = connector()
        c.data_source = MagicMock()
        c.data_source.list_containers = AsyncMock(return_value=SimpleNamespace(success=True, data=[{"name": "docs"}]))
        c.data_source.list_blobs = AsyncMock(return_value=SimpleNamespace(success=True, data=Pages()))
        return c

    @pytest.mark.asyncio
    async def test_refused_credentials_raise_the_reason(self, caplog) -> None:
        c = self.wired(auth_failure())

        with caplog.at_level(logging.DEBUG), pytest.raises(ConnectorInitError) as raised:
            await c.test_connection_and_access()

        assert f"Cannot list blobs in docs: {CREDENTIALS_REFUSED}" in str(raised.value)
        assert_nothing_secret(str(raised.value))
        assert_nothing_secret(c.notify.await_args.kwargs["message"])
        assert_nothing_secret(caplog.text)

    @pytest.mark.asyncio
    async def test_a_missing_permission_is_named(self) -> None:
        error = HttpResponseError(message="This request is not authorized to perform this operation using this permission.\nRequestId:1")
        error.status_code, error.error_code = 403, "AuthorizationPermissionMismatch"

        with pytest.raises(ConnectorInitError, match="HTTP 403 AuthorizationPermissionMismatch: This request is not authorized"):
            await self.wired(error).test_connection_and_access()

    @pytest.mark.asyncio
    async def test_an_error_with_no_message_still_fails(self) -> None:
        c = self.wired(auth_failure())
        c.data_source.list_containers = AsyncMock(side_effect=TimeoutError())

        with pytest.raises(ConnectorInitError, match="TimeoutError"):
            await c.test_connection_and_access()


def blob_record(name: str) -> FileRecord:
    return FileRecord(
        id="r1", record_name=name, record_type=RecordType.FILE, external_record_id=f"docs/{name}",
        external_record_group_id="docs", external_revision_id="md5-old", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.AZURE_BLOB, connector_id="az-1", is_file=True,
    )


class TestReindexArchive:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("revision", ["md5-old", "md5-new"], ids=["unchanged", "changed"])
    async def test_an_archived_blob_is_not_sent_to_indexing(self, revision) -> None:
        c = connector()
        c.data_entities_processor.on_new_records = AsyncMock()
        c.data_entities_processor.reindex_existing_records = AsyncMock()
        properties = SimpleNamespace(
            name="a.txt", last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), creation_time=None, etag='"e"',
            size=1, content_settings=SimpleNamespace(content_type="text/plain", content_md5=revision),
            metadata={}, blob_tier="Archive", blob_tier_change_time=None,
        )
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock(return_value=SimpleNamespace(success=True, data=properties))

        await c.reindex_records([blob_record("a.txt")])

        c.data_entities_processor.on_new_records.assert_not_awaited()
        c.data_entities_processor.reindex_existing_records.assert_not_awaited()

    def test_the_listing_tier_enum_reads_as_archive(self) -> None:
        blob = SimpleNamespace(name="a.txt", blob_tier=AccessTier.ARCHIVE, content_settings=None)

        assert connector()._blob_properties_to_dict(blob)["blob_tier"] == "Archive"

    @pytest.mark.asyncio
    async def test_an_unexpected_error_still_reindexes_the_stored_record(self) -> None:
        c = connector()
        c.data_entities_processor.on_new_records = AsyncMock()
        c.data_entities_processor.reindex_existing_records = AsyncMock()
        properties = SimpleNamespace(
            name="a.txt", last_modified=None, creation_time=None, etag='"e"', size=1,
            content_settings=None, metadata={}, blob_tier="Hot", blob_tier_change_time=None,
        )
        c.data_source = MagicMock()
        c.data_source.get_blob_properties = AsyncMock(return_value=SimpleNamespace(success=True, data=properties))
        record = blob_record("a.txt")

        with patch.object(c, "_get_azure_blob_revision_id", side_effect=KeyError("md5")):
            await c.reindex_records([record])

        c.data_entities_processor.reindex_existing_records.assert_awaited_once_with([record])
