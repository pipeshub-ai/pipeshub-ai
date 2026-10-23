"""Azure Files data source: failures carry Azure's error code, and their text holds
no response body.

The real SDK runs over an in-process transport that answers like the Azure Files
REST API, so the error classes and codes are the ones the service would produce.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from azure.core.exceptions import ClientAuthenticationError
from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport
from azure.core.utils import CaseInsensitiveDict

from app.sources.client.azure.azure_files import (
    AzureFilesClient,
    AzureFilesConnectionStringConfig,
    AzureFilesRESTClient,
)
from app.sources.external.azure.azure_files import (
    CREDENTIALS_REJECTED,
    AzureFilesDataSource,
    describe_error,
    error_code_of,
)

KEY_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=a2V5a2V5a2V5;EndpointSuffix=core.windows.net"
)
WRITTEN = "2024-05-01T10:00:00.1234567Z"


class _Response(AsyncHttpResponse):
    def __init__(self, request, status, headers, body) -> None:
        super().__init__(request, None)
        self.status_code = status
        self.headers = CaseInsensitiveDict(headers)
        self.reason = "Error" if status >= 400 else "OK"
        self.content_type = self.headers.get("Content-Type")
        self._body = body

    def body(self) -> bytes:
        return self._body

    async def load_body(self) -> None:
        return None

    async def read(self) -> bytes:
        return self._body

    def text(self, encoding: str | None = None) -> str:
        return self._body.decode()


class _Service(AsyncHttpTransport):
    """Answers every request with one status and error code, or lists a directory in pages."""

    def __init__(self, status: int = 200, code: str | None = None, body_extra: str = "") -> None:
        self.status = status
        self.code = code
        self.body_extra = body_extra
        self.requests = []

    async def __aenter__(self) -> "_Service":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send(self, request: object, **kwargs: object) -> _Response:
        self.requests.append(request)
        if self.status >= 400:
            body = (
                f'<?xml version="1.0" encoding="utf-8"?><Error><Code>{self.code}</Code>'
                f"<Message>Azure says {self.code}.\nRequestId:1\nTime:2</Message>{self.body_extra}</Error>"
            ).encode()
            headers = {"Content-Type": "application/xml", "x-ms-error-code": self.code}
            if self.status == 503:
                headers["Retry-After"] = "1"
            return _Response(request, self.status, headers, body)
        # A directory with three entries, listed two to a page.
        marker = parse_qs(urlparse(request.url).query).get("marker", [""])[0]
        names, next_marker = (["a.txt", "b.txt"], "tok-2") if not marker else (["c.txt"], "")
        entries = "".join(
            f"<File><FileId>{i}</FileId><Name>{n}</Name><Properties><Content-Length>1</Content-Length>"
            f"<LastWriteTime>{WRITTEN}</LastWriteTime><Last-Modified>Wed, 01 May 2024 10:00:00 GMT</Last-Modified>"
            f"<Etag>\"0x{i}\"</Etag></Properties></File>"
            for i, n in enumerate(names, start=len(marker))
        )
        body = (
            '<?xml version="1.0" encoding="utf-8"?><EnumerationResults ServiceEndpoint="https://acct.file.core.windows.net/" '
            f'ShareName="s" DirectoryPath=""><Entries>{entries}</Entries><NextMarker>{next_marker}</NextMarker></EnumerationResults>'
        ).encode()
        return _Response(request, 200, {"Content-Type": "application/xml"}, body)


def _data_source(service: _Service) -> AzureFilesDataSource:
    config = AzureFilesConnectionStringConfig(connectionString=KEY_CONNECTION_STRING)
    from azure.storage.fileshare.aio import ShareServiceClient

    async def create() -> object:
        # No retries: each case is one answer from the service.
        return ShareServiceClient.from_connection_string(KEY_CONNECTION_STRING, transport=service, retry_total=0)

    config.create_async_share_service_client = create
    return AzureFilesDataSource(AzureFilesClient(AzureFilesRESTClient(config)))


class TestErrorCodes:
    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (404, "ShareNotFound"),
            (404, "ResourceNotFound"),
            (403, "AuthenticationFailed"),
            (403, "AuthorizationPermissionMismatch"),
            (403, "AuthorizationFailure"),
            (503, "ServerBusy"),
            (500, "InternalError"),
        ],
    )
    async def test_listing_failure_carries_the_code_and_status(self, status: int, code: str) -> None:
        ds = _data_source(_Service(status, code))
        response = await ds.list_directories_and_files("s", "")
        assert response.success is False
        assert response.error_code == code
        if status != 404:
            assert f"{code} (HTTP {status})" in response.error

    async def test_share_properties_of_a_gone_share(self) -> None:
        response = await _data_source(_Service(404, "ShareNotFound")).get_share_properties("s")
        assert response.error_code == "ShareNotFound"

    async def test_the_response_body_is_not_kept(self) -> None:
        """azure-core appends the whole body to str(error); an endpoint can put anything there."""
        service = _Service(500, "InternalError", body_extra="<Detail>INTERNAL-ONLY-DATA</Detail>")
        response = await _data_source(service).list_shares()
        assert "INTERNAL-ONLY-DATA" not in response.error
        assert "Content:" not in response.error
        assert "RequestId" not in response.error

    async def test_a_listing_is_read_to_the_last_page(self) -> None:
        service = _Service()
        response = await _data_source(service).list_directories_and_files("s", "")
        assert [i["name"] for i in response.data] == ["a.txt", "b.txt", "c.txt"]
        markers = [parse_qs(urlparse(r.url).query).get("marker", [""])[0] for r in service.requests]
        assert markers == ["", "tok-2"]


class TestEntraIdFailures:
    def test_a_token_failure_saying_not_found_is_a_rejected_credential(self) -> None:
        """AADSTS700016 and AADSTS90002 say "not found": they must not read as a gone share."""
        error = ClientAuthenticationError(
            message="ClientSecretCredential authentication failed: AADSTS700016: Application with "
            "identifier 'x' was not found in the directory 'y'."
        )
        assert error_code_of(error) == CREDENTIALS_REJECTED
        assert "not found" in describe_error(error)

    def test_storage_auth_failure_keeps_its_own_code(self) -> None:
        error = ClientAuthenticationError(message="Server failed to authenticate the request.")
        error.error_code = "AuthenticationFailed"
        assert error_code_of(error) == "AuthenticationFailed"

    def test_urls_in_a_reason_lose_their_query(self) -> None:
        error = ConnectionError("Cannot connect to https://acct.file.core.windows.net/s?sig=SECRET&se=1")
        described = describe_error(error)
        assert "SECRET" not in described and "acct.file.core.windows.net" in described
