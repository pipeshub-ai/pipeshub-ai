"""The stored record's PUT to a direct-upload signed URL: Azure needs the blob type
named and answers 201; S3 and local storage answer 200."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp
import pytest

from app.modules.transformers.blob_storage import (
    _is_azure_sas_url,
    _signed_upload_headers,
    get_shared_session,
)
from tests.unit.modules.artifacts.fake_storage import SIGNATURE, FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

KEY = "org-1/PipesHub/records/vr-1/rec/record.json"


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


def _azure_url(fake: FakeStorage) -> str:
    return f"{fake.base}/blob/{KEY}?sv=2024-08-04&sr=b&sp=cw&sig={SIGNATURE}%2F%2B"


def _s3_url(fake: FakeStorage) -> str:
    return (f"{fake.base}/blob/{KEY}?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
            f"&sig={SIGNATURE}%2F%2B")


def test_azure_sas_urls_are_told_apart() -> None:
    assert _is_azure_sas_url("https://acct.blob.core.windows.net/c/k?sv=2024-08-04&se=x&sp=cw&sig=a%2Fb")
    assert not _is_azure_sas_url("https://bucket.s3.amazonaws.com/k?X-Amz-Signature=abc&X-Amz-Date=1")
    assert not _is_azure_sas_url("http://localhost:3000/api/v1/document/internal/x")
    assert _signed_upload_headers("https://a.blob.core.windows.net/c/k?sv=1&sig=2") == {"x-ms-blob-type": "BlockBlob"}
    assert _signed_upload_headers("https://bucket.s3.amazonaws.com/k?X-Amz-Signature=abc") == {}


async def test_json_upload_to_azure_names_the_blob_type_and_accepts_201(storage: FakeStorage) -> None:
    storage.put_status = 201
    blob = storage.blob_storage()

    status = await blob._upload_to_signed_url(get_shared_session(), _azure_url(storage), {"record": {"a": 1}})

    assert status == 201
    assert storage.puts[KEY].headers["x-ms-blob-type"] == "BlockBlob"


async def test_raw_upload_to_azure_names_the_blob_type_and_accepts_201(storage: FakeStorage) -> None:
    storage.put_status = 201
    blob = storage.blob_storage()

    await blob._upload_raw_to_signed_url(get_shared_session(), _azure_url(storage), b"%PDF-1.7", "application/pdf")

    assert storage.puts[KEY].data == b"%PDF-1.7"
    assert storage.puts[KEY].headers["x-ms-blob-type"] == "BlockBlob"


async def test_s3_upload_gets_no_azure_header(storage: FakeStorage) -> None:
    storage.put_status = 200
    blob = storage.blob_storage()

    assert await blob._upload_to_signed_url(get_shared_session(), _s3_url(storage), {"record": {}}) == 200
    assert "x-ms-blob-type" not in storage.puts[KEY].headers


@pytest.mark.parametrize("status", [403, 409, 500])
async def test_a_non_2xx_upload_still_raises(storage: FakeStorage, status: int) -> None:
    storage.put_status = status
    blob = storage.blob_storage()

    with pytest.raises(aiohttp.ClientError):
        await blob._upload_to_signed_url(get_shared_session(), _azure_url(storage), {"record": {}})
    with pytest.raises(aiohttp.ClientError):
        await blob._upload_raw_to_signed_url(get_shared_session(), _azure_url(storage), b"x", "text/plain")
