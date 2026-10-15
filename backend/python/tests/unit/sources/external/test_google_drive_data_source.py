from unittest.mock import MagicMock

import pytest

from app.sources.client.google.google import GOOGLE_HTTP_NUM_RETRIES
from app.sources.external.google.drive.drive import GoogleDriveDataSource


def _data_source_with_request(request: MagicMock) -> GoogleDriveDataSource:
    client = MagicMock()
    client.files.return_value.create.return_value = request
    return GoogleDriveDataSource(client)


async def _upload(data_source: GoogleDriveDataSource) -> dict:
    return await data_source.files_create_with_media(
        file_metadata={"name": "a.txt", "parents": ["p"]},
        content=b"hello",
        mime_type="text/plain",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [TimeoutError("The read operation timed out"), ConnectionResetError("reset")],
)
async def test_media_upload_resumes_same_request_after_transport_error(error) -> None:
    request = MagicMock()
    request.execute.side_effect = [error, {"id": "file-1"}]
    data_source = _data_source_with_request(request)

    assert await _upload(data_source) == {"id": "file-1"}
    # Re-executing the same HttpRequest resumes its upload session instead of creating a new file.
    data_source.client.files.return_value.create.assert_called_once()
    assert request.execute.call_count == 2
    request.execute.assert_called_with(num_retries=GOOGLE_HTTP_NUM_RETRIES)


@pytest.mark.asyncio
async def test_media_upload_gives_up_after_retry_cap() -> None:
    request = MagicMock()
    request.execute.side_effect = TimeoutError("The read operation timed out")

    with pytest.raises(TimeoutError):
        await _upload(_data_source_with_request(request))
    assert request.execute.call_count == GOOGLE_HTTP_NUM_RETRIES + 1


@pytest.mark.asyncio
async def test_media_upload_does_not_retry_other_errors() -> None:
    request = MagicMock()
    request.execute.side_effect = ValueError("bad request")

    with pytest.raises(ValueError):
        await _upload(_data_source_with_request(request))
    request.execute.assert_called_once()
