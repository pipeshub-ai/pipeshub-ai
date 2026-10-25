"""Every connector client whose server is an address a user typed applies the deployment's rule
for such hosts: this server (loopback), link-local and cloud metadata addresses never; private
ones only while PIPESHUB_BLOCK_PRIVATE_ADDRESSES is off. The HTTPClient guard itself is tested in
``http/test_http_address_guard.py``; these are the clients that don't send through it."""
from __future__ import annotations

import http.server
import socket
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.web.address_guard import (
    GuardedResolver,
    UnsafeAddressError,
    create_guarded_session,
)
from app.sources.client.http.address_guard import (
    AddressNotAllowedError,
    check_url_allowed,
)
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_PUBLIC_IP = "93.184.216.34"
_NEVER = ["127.0.0.1", "169.254.169.254", "fd00:ec2::254", "0.0.0.0"]


def _dns(answers: dict[str, str]) -> "Callable[..., list]":
    def _getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list:
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        family = socket.AF_INET6 if ":" in answers[host] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (answers[host], 0))]

    return _getaddrinfo


@pytest.fixture(autouse=True)
def _deployment_allows_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
    monkeypatch.setattr("urllib.request.getproxies", dict)


@pytest.fixture
def listener() -> "Iterator[tuple[str, list[str]]]":
    """A real server on loopback; the list is every path it was asked for."""
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"secret")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


class TestCheckUrlAllowed:
    @pytest.mark.parametrize("address", _NEVER)
    def test_this_server_and_metadata_are_refused(self, address: str) -> None:
        with patch("socket.getaddrinfo", _dns({"store.example": address})), pytest.raises(AddressNotAllowedError):
            check_url_allowed("https://store.example/")

    def test_a_private_server_is_allowed_unless_the_deployment_blocks_it(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with patch("socket.getaddrinfo", _dns({"store.corp": "10.1.2.3"})):
            check_url_allowed("https://store.corp/")
            monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
            with pytest.raises(AddressNotAllowedError):
                check_url_allowed("https://store.corp/")

    def test_a_name_that_does_not_resolve_is_refused_without_a_proxy(self) -> None:
        with patch("socket.getaddrinfo", _dns({})), pytest.raises(AddressNotAllowedError):
            check_url_allowed("https://nowhere.example/")


class TestAiohttpSessions:
    """Signed download URLs (stream_content) and OAuth token requests."""

    @pytest.mark.parametrize("url", ["http://127.0.0.1:9/", "http://169.254.169.254/latest/meta-data/"])
    async def test_the_deployment_rule_still_refuses_this_server_and_metadata(self, url: str) -> None:
        async with create_guarded_session(allow_private=True) as session:
            with pytest.raises(UnsafeAddressError):
                async with session.get(url):
                    pass

    async def test_the_deployment_rule_lets_a_private_answer_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resolver = GuardedResolver(allow_private=True)
        answer = [{"hostname": "x", "host": "10.0.0.1", "port": 80, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        monkeypatch.setattr(resolver._resolver, "resolve", AsyncMock(return_value=answer))
        assert await resolver.resolve("x", 80) == answer
        await resolver.close()

    @pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "::1"])
    async def test_the_deployment_rule_refuses_a_loopback_or_metadata_answer(
        self, monkeypatch: pytest.MonkeyPatch, host: str,
    ) -> None:
        resolver = GuardedResolver(allow_private=True)
        answer = [{"hostname": "x", "host": host, "port": 80, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        monkeypatch.setattr(resolver._resolver, "resolve", AsyncMock(return_value=answer))
        with pytest.raises(UnsafeAddressError):
            await resolver.resolve("x", 80)
        await resolver.close()

    async def test_stream_content_never_fetches_this_server(self, listener: tuple[str, list[str]]) -> None:
        from app.utils.streaming import stream_content

        base, hits = listener
        with pytest.raises(HTTPException) as refused:
            async for _ in stream_content(f"{base}/presigned?X-Amz-Signature=x"):
                pass
        assert refused.value.status_code == 502
        assert hits == []

    async def test_oauth_token_requests_go_through_the_guard(self) -> None:
        from app.connectors.core.base.token_service.oauth_service import (
            OAuthConfig,
            OAuthProvider,
        )

        config = OAuthConfig(
            client_id="c", client_secret="s", redirect_uri="https://app/cb",
            authorize_url="https://auth.example/authorize", token_url="http://127.0.0.1:9/token",
        )
        provider = OAuthProvider(config, MagicMock(), "/creds")
        session = await provider.session
        try:
            assert isinstance(session.connector._resolver, GuardedResolver)
            with pytest.raises(UnsafeAddressError):
                await provider._make_token_request({"grant_type": "client_credentials"})
        finally:
            await provider.close()


class TestRefusalReachesTheUser:
    def test_a_refused_address_streams_as_a_409_with_its_reason(self) -> None:
        from app.connectors.core.base.error.stream_errors import to_stream_error

        error = to_stream_error(AddressNotAllowedError("The address 127.0.0.1 isn't allowed: x."), connector="Jira")
        assert error.status_code == 409
        assert "127.0.0.1 isn't allowed" in error.detail


class TestSdkClients:
    async def test_minio_refuses_this_server(self, listener: tuple[str, list[str]]) -> None:
        from app.sources.client.minio.minio import MinIORESTClientViaAccessKey

        base, hits = listener
        client = MinIORESTClientViaAccessKey("key", "secret", endpoint_url=base)
        with pytest.raises(AddressNotAllowedError):
            await client.get_s3_client()
        assert hits == []

    async def test_minio_names_are_checked_by_the_session_resolver(self) -> None:
        from app.sources.client.minio.minio import MinIORESTClientViaAccessKey

        client = MinIORESTClientViaAccessKey("key", "secret", endpoint_url="https://minio.corp:9000")
        with patch("socket.getaddrinfo", _dns({"minio.corp": "10.1.2.3"})), patch.object(
            client, "get_session",
        ) as get_session:
            await client.get_s3_client()
        config = get_session.return_value.create_client.call_args.kwargs["config"]
        assert isinstance(config.connector_args["resolver"], GuardedResolver)
        assert config.connector_args["resolver"]._allow_private is True

    @pytest.mark.parametrize(
        "connection_string",
        [
            "DefaultEndpointsProtocol=http;AccountName=a;AccountKey=a2V5;BlobEndpoint=http://169.254.169.254/a;",
            "DefaultEndpointsProtocol=http;AccountName=a;AccountKey=a2V5;BlobEndpoint=http://127.0.0.1:10000/a;",
        ],
    )
    async def test_azure_blob_refuses_an_endpoint_on_this_server_or_metadata(self, connection_string: str) -> None:
        from app.sources.client.azure.azure_blob import (
            AzureBlobConfigurationError,
            AzureBlobConnectionStringConfig,
            AzureBlobRESTClient,
        )

        client = AzureBlobRESTClient(AzureBlobConnectionStringConfig(azureBlobConnectionString=connection_string))
        with pytest.raises(AzureBlobConfigurationError, match="isn't allowed"):
            await client.get_async_blob_service_client()
        assert client._async_blob_service_client is None

    def test_gcs_refuses_a_token_uri_on_metadata(self) -> None:
        from app.sources.client.gcs.gcs import GCSRESTClientViaServiceAccount

        client = GCSRESTClientViaServiceAccount({"token_uri": "http://169.254.169.254/token", "project_id": "p"})
        with pytest.raises(AddressNotAllowedError):
            client._get_credentials()

    def test_cifs_connects_to_the_checked_address(self) -> None:
        from app.sources.client.cifs.cifs import CifsClient

        client = CifsClient(server="files.corp", username="u", password="p", remote_name="FILES")
        conn = MagicMock(isUsingSMB2=False)
        conn.connect.return_value = True
        with patch.object(client, "_new_connection", return_value=conn), patch(
            "socket.getaddrinfo", _dns({"files.corp": "10.1.2.3"}),
        ):
            client._ensure_connected()
        conn.connect.assert_called_once_with("10.1.2.3", 445)

    @pytest.mark.parametrize("server", ["127.0.0.1", "169.254.169.254"])
    def test_cifs_refuses_this_server_and_metadata(self, server: str) -> None:
        from app.connectors.sources.network_share.errors import NetworkShareAuthError
        from app.sources.client.cifs.cifs import CifsClient

        client = CifsClient(server=server, username="u", password="p", remote_name="FILES")
        conn = MagicMock()
        with patch.object(client, "_new_connection", return_value=conn), pytest.raises(NetworkShareAuthError):
            client._ensure_connected()
        conn.connect.assert_not_called()

    async def test_mariadb_refuses_metadata_before_dialling(self) -> None:
        from app.sources.client.mariadb import mariadb as module

        client = module.MariaDBClient(host="169.254.169.254", user="u", password="p")
        with patch.object(module.mariadb, "create_async_pool", new=AsyncMock()) as pool, pytest.raises(ConnectionError):
            await client.connect()
        pool.assert_not_awaited()

    async def test_mariadb_dials_the_checked_address(self) -> None:
        from app.sources.client.mariadb import mariadb as module

        client = module.MariaDBClient(host="db.corp", user="u", password="p")
        with patch.object(module.mariadb, "create_async_pool", new=AsyncMock()) as pool, patch(
            "socket.getaddrinfo", _dns({"db.corp": "10.1.2.3"}),
        ):
            await client.connect()
        assert pool.await_args.kwargs["host"] == "10.1.2.3"


class TestBareHttpxClients:
    def test_gitlab_downloads_go_through_the_guard(self) -> None:
        from app.sources.client.http.address_guard import AddressGuardTransport
        from app.sources.external.gitlab.gitlab_data_source import GitLabDataSource

        data_source = GitLabDataSource(MagicMock(), base_url="https://gitlab.corp")
        assert isinstance(data_source.http_client._transport, AddressGuardTransport)


    async def test_salesforce_record_images_reach_public_addresses_only(
        self, listener: tuple[str, list[str]],
    ) -> None:
        import logging

        from app.connectors.sources.salesforce.connector import SalesforceConnector

        base, hits = listener
        html = f'<p><img src="{base}/file-asset-public/logo"></p>'
        connector = MagicMock(logger=logging.getLogger("test"))
        out = await SalesforceConnector._process_html_images(connector, html)
        assert "file-asset-public/logo" in out  # left as it was, not inlined
        assert hits == []


class TestSnowflakeAccount:
    @pytest.mark.parametrize(
        ("identifier", "base_url"),
        [
            ("myorg-myacct", "https://myorg-myacct.snowflakecomputing.com/api/v2"),
            ("xy12345.us-east-1.aws", "https://xy12345.us-east-1.aws.snowflakecomputing.com/api/v2"),
            ("https://myorg-myacct.snowflakecomputing.com", "https://myorg-myacct.snowflakecomputing.com/api/v2"),
            ("myorg-myacct.snowflakecomputing.com/", "https://myorg-myacct.snowflakecomputing.com/api/v2"),
            # Used to become the host itself: "https://evil.example#.snowflakecomputing.com/api/v2".
            ("evil.example#", "https://evil.example.snowflakecomputing.com/api/v2"),
        ],
    )
    def test_the_host_is_always_snowflakes(self, identifier: str, base_url: str) -> None:
        from app.sources.client.snowflake.snowflake import (
            SnowflakeRESTClientViaOAuth,
            SnowflakeRESTClientViaPAT,
        )

        assert SnowflakeRESTClientViaPAT._build_base_url(identifier) == base_url
        assert SnowflakeRESTClientViaOAuth._build_base_url(identifier) == base_url

    @pytest.mark.parametrize("identifier", ["127.0.0.1:8080?", "x@evil.example", "evil.example:443"])
    def test_an_identifier_that_names_another_server_is_refused(self, identifier: str) -> None:
        from app.sources.client.snowflake.snowflake import SnowflakeRESTClientViaPAT

        with pytest.raises(ValueError, match="account identifier"):
            SnowflakeRESTClientViaPAT._build_base_url(identifier)
