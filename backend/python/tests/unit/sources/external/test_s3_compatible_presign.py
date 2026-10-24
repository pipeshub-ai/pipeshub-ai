"""The S3 and MinIO data sources sign requests with the keys and region they were built with.

Presigning runs the full client stack (session, credentials, region, endpoint,
SigV4) without any network call, so it checks the aiobotocore wiring end to end.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from app.sources.client.minio.minio import MinIOAccessKeyConfig, MinIOClient
from app.sources.client.s3.s3 import S3AccessKeyConfig, S3Client
from app.sources.external.minio.minio import MinIODataSource
from app.sources.external.s3.s3 import S3DataSource


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


@pytest.mark.asyncio
async def test_s3_presigned_url_is_signed_with_the_configured_keys_and_region() -> None:
    client = S3Client.build_with_config(
        S3AccessKeyConfig(access_key_id="AKIDEXAMPLE", secret_access_key="SECRET", region_name="eu-west-1")
    )

    response = await S3DataSource(client).generate_presigned_url(
        ClientMethod="get_object", Params={"Bucket": "docs", "Key": "a/b.pdf"}, ExpiresIn=60
    )

    assert response.success, response.error
    url = response.data
    query = _query(url)
    assert urlparse(url).path.endswith("/a/b.pdf")
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Credential"][0].startswith("AKIDEXAMPLE/")
    assert "/eu-west-1/s3/aws4_request" in query["X-Amz-Credential"][0]
    assert query["X-Amz-Expires"] == ["60"]


@pytest.mark.asyncio
async def test_minio_presigned_url_points_at_the_minio_endpoint() -> None:
    client = MinIOClient.build_with_config(
        MinIOAccessKeyConfig(
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
            endpoint_url="http://minio.internal:9000",
            use_ssl=False,
            verify_ssl=False,
        )
    )

    response = await MinIODataSource(client).generate_presigned_url(
        ClientMethod="get_object", Params={"Bucket": "docs", "Key": "report.docx"}
    )

    assert response.success, response.error
    url = urlparse(response.data)
    assert (url.scheme, url.netloc) == ("http", "minio.internal:9000")
    assert _query(response.data)["X-Amz-Credential"][0].startswith("minioadmin/")
