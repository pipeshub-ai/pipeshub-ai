"""The R2 client and data source. Real aiobotocore clients are built, but no request leaves:
requests are read at botocore's last step before sending, so these check what R2 would receive."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from app.sources.client.cloudflare_r2.cloudflare_r2 import (
    CloudflareR2AccessKeyConfig,
    CloudflareR2Client,
    CloudflareR2RESTClientViaAccessKey,
    build_endpoint_url,
)
from app.sources.external.cloudflare_r2.cloudflare_r2 import CloudflareR2DataSource

ACCOUNT = "0123456789abcdef0123456789abcdef"
HOST = f"{ACCOUNT}.r2.cloudflarestorage.com"
LOGGER = logging.getLogger("test.cloudflare_r2")


class _Unsent(Exception):
    """Stops a request at the point it would have been sent."""


def _config_service(auth: dict | None) -> AsyncMock:
    return AsyncMock(get_config=AsyncMock(return_value=None if auth is None else {"auth": auth}))


def _client() -> CloudflareR2Client:
    return CloudflareR2Client.build_with_config(CloudflareR2AccessKeyConfig("key-id", "secret", ACCOUNT))


async def _sent_request(operation: str, **params: object) -> dict:
    """URL and headers of the request this data source call would send."""
    seen: dict = {}

    def capture(request: object, **_: object) -> None:
        seen.update(url=request.url, headers={k.lower(): v for k, v in request.headers.items()})
        raise _Unsent

    client = _client()
    client.get_session().register("before-send.s3.*", capture)
    await getattr(CloudflareR2DataSource(client), operation)(**params)
    assert seen, "no request was built"
    return seen


class TestEndpoint:
    @pytest.mark.parametrize(
        ("account", "jurisdiction", "expected"),
        [
            (ACCOUNT, None, f"https://{HOST}"),
            (ACCOUNT, "", f"https://{HOST}"),
            (ACCOUNT, "eu", f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"),
            (ACCOUNT, "fedramp", f"https://{ACCOUNT}.fedramp.r2.cloudflarestorage.com"),
            (ACCOUNT, "us", f"https://{ACCOUNT}.us.r2.cloudflarestorage.com"),
            (f"  {ACCOUNT.upper()}\n", " EU ", f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"),
        ],
    )
    def test_the_address_is_built_from_the_account_and_jurisdiction(
        self, account: str, jurisdiction: str | None, expected: str,
    ) -> None:
        assert build_endpoint_url(account, jurisdiction) == expected

    @pytest.mark.parametrize(
        "account",
        [
            "",
            None,
            ACCOUNT[:-1],
            ACCOUNT + "0",
            "g" * 32,
            "127.0.0.1",
            "169.254.169.254",
            "evil.example#",
            f"{ACCOUNT}.evil.example",
            f"x@{ACCOUNT}",
            f"{ACCOUNT}\n.evil.example",
        ],
    )
    def test_an_account_id_that_could_name_another_server_is_refused(self, account: str | None) -> None:
        with pytest.raises(ValueError, match="32 hexadecimal"):
            build_endpoint_url(account)

    @pytest.mark.parametrize("jurisdiction", ["apac", "evil.example/", "eu.evil.example", "eu#"])
    def test_an_unknown_jurisdiction_is_refused(self, jurisdiction: str) -> None:
        with pytest.raises(ValueError, match="jurisdiction"):
            build_endpoint_url(ACCOUNT, jurisdiction)


class TestBuildFromServices:
    async def test_the_stored_auth_config_reaches_the_client(self) -> None:
        client = await CloudflareR2Client.build_from_services(
            LOGGER,
            _config_service(
                {"accessKey": "key-id", "secretKey": "secret", "accountId": ACCOUNT, "jurisdiction": "eu", "bucket": "b"}
            ),
            "connector-1",
        )
        credentials = client.get_credentials()
        assert credentials["aws_access_key_id"] == "key-id"
        assert credentials["aws_secret_access_key"] == "secret"
        assert client.get_endpoint_url() == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"
        assert client.get_region_name() == "auto"
        assert client.get_bucket_name() == "b"

    async def test_a_missing_configuration_is_refused(self) -> None:
        with pytest.raises(ValueError, match="connector-1"):
            await CloudflareR2Client.build_from_services(LOGGER, _config_service(None), "connector-1")

    @pytest.mark.parametrize(
        "auth",
        [
            {"secretKey": "secret", "accountId": ACCOUNT},
            {"accessKey": "key-id", "accountId": ACCOUNT},
            {"accessKey": "", "secretKey": "", "accountId": ACCOUNT},
        ],
    )
    async def test_missing_keys_are_refused_rather_than_using_the_hosts_aws_credentials(self, auth: dict) -> None:
        with pytest.raises(ValueError, match="access key"):
            await CloudflareR2Client.build_from_services(LOGGER, _config_service(auth), "connector-1")

    async def test_a_missing_account_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="account ID"):
            await CloudflareR2Client.build_from_services(
                LOGGER, _config_service({"accessKey": "key-id", "secretKey": "secret"}), "connector-1",
            )

    def test_the_secret_stays_out_of_the_config_repr(self) -> None:
        assert "hunter2" not in repr(CloudflareR2AccessKeyConfig("key-id", "hunter2", ACCOUNT))


class TestRequests:
    @pytest.mark.parametrize(
        ("operation", "params", "path"),
        [
            ("get_object", {"Bucket": "docs", "Key": "a/b.pdf"}, "/docs/a/b.pdf"),
            ("head_object", {"Bucket": "docs", "Key": "a/b.pdf"}, "/docs/a/b.pdf"),
            ("list_objects_v2", {"Bucket": "docs", "Prefix": "a/"}, "/docs"),
            ("list_buckets", {"MaxBuckets": 1000}, "/"),
            ("put_object", {"Bucket": "docs", "Key": "a.txt", "Body": b"x"}, "/docs/a.txt"),
        ],
    )
    async def test_requests_are_path_style_signed_for_auto_without_checksum_headers(
        self, operation: str, params: dict, path: str,
    ) -> None:
        sent = await _sent_request(operation, **params)
        url = urlparse(sent["url"])
        assert (url.scheme, url.netloc, url.path) == ("https", HOST, path)
        authorization = sent["headers"]["authorization"]
        authorization = authorization.decode() if isinstance(authorization, bytes) else authorization
        assert "/auto/s3/aws4_request" in authorization
        assert not [h for h in sent["headers"] if "checksum" in h or h == "x-amz-trailer"]

    async def test_a_presigned_url_points_at_r2_and_carries_the_token_key(self) -> None:
        response = await CloudflareR2DataSource(_client()).generate_presigned_url(
            ClientMethod="get_object", Params={"Bucket": "docs", "Key": "a/b.pdf"}, ExpiresIn=60,
        )
        assert response.success, response.error
        url = urlparse(response.data)
        query = parse_qs(url.query)
        assert (url.netloc, url.path) == (HOST, "/docs/a/b.pdf")
        assert query["X-Amz-Credential"][0].startswith("key-id/")
        assert "/auto/s3/aws4_request" in query["X-Amz-Credential"][0]
        assert query["X-Amz-Expires"] == ["60"]
        assert "checksum" not in response.data.lower()

    async def test_the_built_client_keeps_r2_settings_alongside_the_retry_budget(self) -> None:
        rest_client = CloudflareR2RESTClientViaAccessKey("key-id", "secret", ACCOUNT)
        rest_client.get_session().set_default_client_config(
            AioConfig(retries={"mode": "standard", "total_max_attempts": 4})
        )
        with patch("app.sources.client.minio.minio.check_url_allowed"):
            client_context = await rest_client.get_s3_client()
        async with client_context as client:
            assert client.meta.endpoint_url == f"https://{HOST}"
            assert client.meta.region_name == "auto"
            assert client.meta.config.retries["total_max_attempts"] == 4
            assert client.meta.config.request_checksum_calculation == "when_required"
            assert client.meta.config.response_checksum_validation == "when_required"


class TestResponses:
    @staticmethod
    def _data_source(s3_client: AsyncMock) -> CloudflareR2DataSource:
        data_source = CloudflareR2DataSource(_client())

        @asynccontextmanager
        async def _fake_client() -> AsyncIterator[AsyncMock]:
            yield s3_client

        data_source._client = _fake_client
        return data_source

    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (401, "Unauthorized", "Unauthorized"),
            (403, "InvalidAccessKeyId", "check the Access Key"),
            (403, "SignatureDoesNotMatch", "check the Secret Key"),
            (403, "RequestTimeTooSkewed", "clocks"),
            (404, "NoSuchBucket", "NoSuchBucket"),
            (429, "TooManyRequests", "TooManyRequests"),
            (500, "InternalError", "InternalError"),
            (503, "SlowDown", "SlowDown"),
        ],
    )
    async def test_a_refusal_keeps_its_status_and_never_names_another_product(
        self, status: int, code: str, expected: str,
    ) -> None:
        s3_client = AsyncMock()
        s3_client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": code, "Message": "refused."}, "ResponseMetadata": {"HTTPStatusCode": status}},
            "ListObjectsV2",
        )
        response = await self._data_source(s3_client).list_objects_v2(Bucket="docs")
        assert not response.success
        assert response.status_code == status
        assert expected in response.error
        assert "minio" not in response.error.lower()

    async def test_the_next_page_token_is_sent_and_returned(self) -> None:
        s3_client = AsyncMock()
        s3_client.list_objects_v2.return_value = {"Contents": [{"Key": "a"}], "NextContinuationToken": "page-3"}
        response = await self._data_source(s3_client).list_objects_v2(Bucket="docs", ContinuationToken="page-2")
        s3_client.list_objects_v2.assert_awaited_once_with(Bucket="docs", ContinuationToken="page-2")
        assert response.success
        assert response.data["NextContinuationToken"] == "page-3"

    async def test_an_empty_answer_is_a_failure_that_does_not_name_another_product(self) -> None:
        s3_client = AsyncMock()
        s3_client.get_object.return_value = None
        response = await self._data_source(s3_client).get_object(Bucket="docs", Key="a")
        assert not response.success
        assert "minio" not in response.error.lower()
