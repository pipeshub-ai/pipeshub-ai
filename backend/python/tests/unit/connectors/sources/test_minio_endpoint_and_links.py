"""MinIO endpoint handling, Console links and the errors an admin sees.

Literal addresses only, so no test here depends on DNS.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, SSLError

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.minio.connector import MinIOConnector
from app.connectors.sources.web.address_guard import GuardedResolver
from app.sources.client.minio.minio import (
    MinIOClient,
    check_endpoint_address,
    resolve_endpoint_url,
)
from app.sources.external.minio.minio import MinIODataSource


def _connector(auth: dict, *, endpoint_url: str = "http://10.0.0.5:9000") -> MinIOConnector:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={"auth": auth})
    processor = MagicMock()
    processor.org_id = "org-1"
    return MinIOConnector(
        logger=logging.getLogger("test.minio"),
        data_entities_processor=processor,
        data_store_provider=MagicMock(),
        config_service=config_service,
        connector_id="minio-1",
        scope="team",
        created_by="user-1",
        endpoint_url=endpoint_url,
        console_url=auth.get("consoleUrl"),
    )


def _keys(**auth: object) -> dict:
    return {"accessKey": "ak", "secretKey": "sk", **auth}


class TestResolveEndpointUrl:
    def test_an_address_without_a_scheme_takes_it_from_use_ssl(self) -> None:
        # botocore refuses "minio.local:9000" as "Invalid endpoint".
        assert resolve_endpoint_url("minio.local:9000", use_ssl=True) == "https://minio.local:9000"
        assert resolve_endpoint_url("minio.local:9000", use_ssl=False) == "http://minio.local:9000"

    def test_a_scheme_the_admin_typed_wins(self) -> None:
        assert resolve_endpoint_url("http://minio.local:9000", use_ssl=True) == "http://minio.local:9000"
        assert resolve_endpoint_url("https://minio.local", use_ssl=False) == "https://minio.local"

    def test_spaces_and_a_trailing_slash_are_dropped(self) -> None:
        assert resolve_endpoint_url("  http://minio.local:9000/ ") == "http://minio.local:9000"

    @pytest.mark.parametrize(
        ("url", "reason"),
        [
            ("", "required"),
            ("ftp://minio.local", "http:// or https://"),
            ("file:///etc/passwd", "http:// or https://"),
            ("http://:9000", "no valid host"),
            ("http://min io.local:9000", "no valid host"),
            ("http://minio.local:99999", "invalid port"),
            ("http://admin:secret@minio.local:9000", "user name or password"),
            ("http://minio.local:9000?x=1", "query string"),
        ],
    )
    def test_an_unusable_address_is_refused_with_the_reason(self, url: str, reason: str) -> None:
        with pytest.raises(ValueError, match=reason):
            resolve_endpoint_url(url)

    @pytest.mark.parametrize(
        "url",
        ["http://admin:hunter2@minio.local:99999", "admin:hunter2@minio.local:9000", "admin:9hunter2@minio.local"],
    )
    def test_credentials_in_the_url_are_refused_without_repeating_them(self, url: str) -> None:
        # Without a scheme, "admin:" once read as a scheme and came back in the message.
        with pytest.raises(ValueError, match="user name or password") as refused:
            resolve_endpoint_url(url)
        assert "hunter2" not in str(refused.value)
        assert "admin" not in str(refused.value)


class TestCheckEndpointAddress:
    @pytest.mark.asyncio
    async def test_loopback_is_always_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The rule every connector's server follows: never this server itself.
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        with pytest.raises(ValueError, match="loopback"):
            await check_endpoint_address("http://127.0.0.1:9000")

    @pytest.mark.asyncio
    async def test_cloud_metadata_is_always_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        with pytest.raises(ValueError, match="metadata"):
            await check_endpoint_address("http://169.254.169.254")

    @pytest.mark.asyncio
    async def test_a_private_address_is_allowed_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Most MinIO servers run on a private network.
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        await check_endpoint_address("http://10.0.0.5:9000")

    @pytest.mark.asyncio
    async def test_a_private_address_is_refused_when_the_deployment_blocks_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        with pytest.raises(ValueError, match="public address"):
            await check_endpoint_address("http://10.0.0.5:9000")


class TestClientUsesTheResolvedEndpoint:
    @pytest.mark.asyncio
    async def test_build_from_services_applies_use_ssl_to_a_bare_address(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={"auth": _keys(endpointUrl="10.0.0.5:9000", useSsl=False)}
        )
        client = await MinIOClient.build_from_services(logging.getLogger("test.minio"), config_service, "minio-1")
        assert client.get_endpoint_url() == "http://10.0.0.5:9000"

    @pytest.mark.asyncio
    async def test_build_from_services_refuses_a_metadata_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Every data source is built here, so the address rule holds on this path too, not only in init().
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": _keys(endpointUrl="http://169.254.169.254")})
        with pytest.raises(ValueError, match="metadata"):
            await MinIOClient.build_from_services(logging.getLogger("test.minio"), config_service, "minio-1")


class TestInitRefusesABadEndpoint:
    @pytest.mark.asyncio
    async def test_a_metadata_endpoint_fails_init_with_a_reason_the_user_sees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        connector = _connector(_keys(endpointUrl="http://169.254.169.254"))
        with patch("app.connectors.sources.minio.connector.MinIOClient.build_from_services") as build:
            with pytest.raises(ConnectorInitError, match="metadata"):
                await connector.init()
        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_wrong_scheme_fails_init_with_a_reason_the_user_sees(self) -> None:
        connector = _connector(_keys(endpointUrl="ftp://10.0.0.5:9000"))
        with pytest.raises(ConnectorInitError, match="http:// or https://"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_a_bare_address_connects_with_the_scheme_use_ssl_names(self) -> None:
        connector = _connector(_keys(endpointUrl="10.0.0.5:9000", useSsl=False))
        with patch(
            "app.connectors.sources.minio.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            assert await connector.init() is True
        assert connector.endpoint_url == "http://10.0.0.5:9000"
        assert connector.data_source._endpoint_url == "http://10.0.0.5:9000"


class TestConsoleLinks:
    """The S3 API port reads /browser/... as a bucket named "browser" and answers 403."""

    def test_default_layout_links_to_the_console_port(self) -> None:
        assert MinIOConnector._parse_console_url("http://10.0.0.5:9000") == "http://10.0.0.5:9001"
        assert MinIOConnector._parse_console_url("http://[fd00::5]:9000") == "http://[fd00::5]:9001"

    def test_another_port_keeps_the_endpoint_address(self) -> None:
        assert MinIOConnector._parse_console_url("https://minio.example.com") == "https://minio.example.com"

    def test_a_configured_console_url_wins(self) -> None:
        assert (
            MinIOConnector._parse_console_url("http://10.0.0.5:9000", "https://console.example.com/minio/")
            == "https://console.example.com/minio"
        )

    def test_an_empty_console_url_uses_the_default_layout(self) -> None:
        assert MinIOConnector._parse_console_url("http://10.0.0.5:9000", "   ") == "http://10.0.0.5:9001"

    def test_a_console_url_without_a_scheme_takes_it_from_use_ssl(self) -> None:
        assert (
            MinIOConnector._parse_console_url("http://10.0.0.5:9000", "console.example.com:9001", use_ssl=False)
            == "http://console.example.com:9001"
        )

    @pytest.mark.parametrize(
        ("console_url", "reason"),
        [
            ("javascript:alert(1)", "http:// or https://"),
            ("ftp://console", "http:// or https://"),
            ("not a url", "no valid host"),
            ("http://console.example.com?next=/x", "query string"),
            ("http://admin:hunter2@console.example.com", "user name or password"),
        ],
    )
    def test_a_console_url_that_cannot_be_linked_to_is_refused(self, console_url: str, reason: str) -> None:
        # It lands in every citation: a guess would send users elsewhere, userinfo would show a password.
        with pytest.raises(ValueError, match=reason) as refused:
            MinIOConnector._parse_console_url("http://10.0.0.5:9000", console_url)
        assert "hunter2" not in str(refused.value)

    @pytest.mark.asyncio
    async def test_init_refuses_a_console_url_that_cannot_be_linked_to(self) -> None:
        connector = _connector(
            _keys(endpointUrl="http://10.0.0.5:9000", consoleUrl="http://admin:hunter2@console.example.com")
        )
        # Built anyway, with a link placeholder: init() is where the admin is told.
        assert "hunter2" not in connector.base_console_url
        with pytest.raises(ConnectorInitError, match="Console URL") as refused:
            await connector.init()
        assert "hunter2" not in str(refused.value)

    @pytest.mark.asyncio
    async def test_init_takes_the_console_url_from_the_config(self) -> None:
        connector = _connector(
            _keys(endpointUrl="http://10.0.0.5:9000", consoleUrl="https://console.example.com")
        )
        with patch(
            "app.connectors.sources.minio.connector.load_connector_filters",
            AsyncMock(return_value=(MagicMock(), MagicMock())),
        ):
            await connector.init()
        assert connector._generate_web_url("docs", "a.txt") == "https://console.example.com/browser/docs/a.txt"
        assert connector.data_entities_processor.base_console_url == "https://console.example.com"

    def test_a_file_link_encodes_the_key_as_the_console_does(self) -> None:
        connector = _connector(_keys())
        # The Console reads the path after the bucket as one encodeURIComponent value.
        assert (
            connector._generate_web_url("docs", "Résumé dossier/naïve & co #1+2.txt")
            == "http://10.0.0.5:9001/browser/docs/R%C3%A9sum%C3%A9%20dossier%2Fna%C3%AFve%20%26%20co%20%231%2B2.txt"
        )

    def test_a_folder_link_ends_with_an_encoded_slash(self) -> None:
        connector = _connector(_keys())
        assert connector._generate_parent_web_url("docs/a/b") == "http://10.0.0.5:9001/browser/docs/a%2Fb%2F"
        assert connector._generate_parent_web_url("docs") == "http://10.0.0.5:9001/browser/docs"


def _client_error(code: str, message: str, status: int = 403) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "ListBuckets",
    )


class TestDataSourceConnectionsAreGuarded:
    """The data source opens its own clients; each must go through the guarded resolver.

    Async: aiohttp builds its resolver on the running loop, as it does when a client is opened.
    """

    @pytest.mark.asyncio
    async def test_every_client_resolves_through_the_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        client = MagicMock()
        client.get_credentials.return_value = {"endpoint_url": "http://10.0.0.5:9000", "verify_ssl": True}
        kwargs = MinIODataSource(client)._get_client_kwargs()
        resolver = kwargs["config"].connector_args["resolver"]
        assert isinstance(resolver, GuardedResolver)
        assert resolver._allow_private is True

    @pytest.mark.asyncio
    async def test_private_addresses_stay_out_when_the_deployment_blocks_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        client = MagicMock()
        client.get_credentials.return_value = {"endpoint_url": "http://10.0.0.5:9000", "verify_ssl": True}
        resolver = MinIODataSource(client)._get_client_kwargs()["config"].connector_args["resolver"]
        assert resolver._allow_private is False


class TestStoreErrorsTheAdminCanActOn:
    @pytest.fixture
    def data_source(self) -> MinIODataSource:
        client = MagicMock()
        client.get_credentials.return_value = {"endpoint_url": "http://10.0.0.5:9000", "verify_ssl": True}
        return MinIODataSource(client)

    @staticmethod
    def _failing_client(data_source: MinIODataSource, error: Exception) -> None:
        s3 = MagicMock()
        s3.list_buckets = AsyncMock(side_effect=error)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=s3)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.create_client.return_value = context
        data_source._session = session

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "says"),
        [
            ("SignatureDoesNotMatch", "check the Secret Key"),
            ("InvalidAccessKeyId", "check the Access Key"),
            ("RequestTimeTooSkewed", "clocks"),
        ],
    )
    async def test_a_credential_error_says_which_field_to_fix(
        self, data_source: MinIODataSource, code: str, says: str
    ) -> None:
        self._failing_client(data_source, _client_error(code, "The request signature we calculated does not match."))
        response = await data_source.list_buckets()
        assert response.success is False
        assert response.error.startswith(f"{code}: ")
        assert says in response.error
        # MinIO's own reason stays, for the logs.
        assert "(The request signature we calculated does not match)" in response.error
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_other_errors_keep_the_store_message_without_a_trailing_period(
        self, data_source: MinIODataSource
    ) -> None:
        # Callers end the sentence: "Could not list buckets: {error}."
        self._failing_client(data_source, _client_error("AccessDenied", "Access Denied."))
        response = await data_source.list_buckets()
        assert response.error == "AccessDenied: Access Denied"

    @pytest.mark.asyncio
    async def test_https_to_a_plain_http_server_says_so(self, data_source: MinIODataSource) -> None:
        self._failing_client(
            data_source,
            SSLError(endpoint_url="https://10.0.0.5:9000", error="[SSL: WRONG_VERSION_NUMBER] wrong version number"),
        )
        response = await data_source.list_buckets()
        assert response.success is False
        assert "does not answer HTTPS" in response.error
