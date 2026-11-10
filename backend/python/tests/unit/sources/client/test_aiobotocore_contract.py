"""Our S3 and MinIO clients build real aiobotocore clients with the installed release.

The other tests patch the session. These build the client for real (no request is sent), so a
change to the session, config or client-creation signature fails here and not in production.
"""

import pytest
from aiobotocore.config import AioConfig

from app.sources.client.minio.minio import MinIORESTClientViaAccessKey
from app.sources.client.s3.s3 import S3RESTClientViaAccessKey


@pytest.fixture(autouse=True)
def _address_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # The address check has its own tests; only aiobotocore is under test here.
    monkeypatch.setattr("app.sources.client.minio.minio.check_url_allowed", lambda *_a, **_k: None)


def _s3_client() -> S3RESTClientViaAccessKey:
    return S3RESTClientViaAccessKey("AKIAEXAMPLE", "secret-for-tests", "eu-west-1", "bucket")


def _minio_client() -> MinIORESTClientViaAccessKey:
    return MinIORESTClientViaAccessKey(
        access_key_id="minio-user",
        secret_access_key="minio-secret",
        endpoint_url="http://localhost:9000",
        bucket_name="bucket",
        use_ssl=False,
        verify_ssl=False,
        region_name="us-east-1",
    )


async def test_s3_client_builds_with_region_and_credentials() -> None:
    async with await _s3_client().get_s3_client() as client:
        assert client.meta.region_name == "eu-west-1"
        assert client.meta.service_model.service_name == "s3"


async def test_minio_client_builds_against_its_endpoint_with_the_guarded_resolver() -> None:
    async with await _minio_client().get_s3_client() as client:
        assert client.meta.endpoint_url == "http://localhost:9000"
        assert client.meta.region_name == "us-east-1"


async def test_minio_client_signs_a_presigned_url_offline() -> None:
    async with await _minio_client().get_s3_client() as client:
        url = await client.generate_presigned_url(
            "get_object", Params={"Bucket": "bucket", "Key": "a/b.txt"}, ExpiresIn=60
        )
    assert url.startswith("http://localhost:9000/bucket/a/b.txt?")
    assert "Signature=" in url


@pytest.mark.parametrize("make_client", [_s3_client, _minio_client])
async def test_the_retry_budget_config_reaches_the_built_client(make_client) -> None:
    # The same AioConfig the connectors set through _with_retry_budget.
    client_wrapper = make_client()
    client_wrapper.get_session().set_default_client_config(
        AioConfig(retries={"mode": "standard", "total_max_attempts": 4})
    )
    async with await client_wrapper.get_s3_client() as client:
        assert client.meta.config.retries["total_max_attempts"] == 4
