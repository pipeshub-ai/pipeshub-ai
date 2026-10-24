import pytest

from app.sources.client.linear.upload_urls import is_linear_upload_url


@pytest.mark.parametrize("url", [
    "https://uploads.linear.app/org/a/b",
    "https://UPLOADS.linear.app/org/a/b",
    "https://uploads.linear.app:443/org/a/b",
    "  https://uploads.linear.app/org/a/b  ",
])
def test_linear_uploads_are_accepted(url) -> None:
    assert is_linear_upload_url(url)


@pytest.mark.parametrize("url", [
    "https://evil.example/uploads.linear.app/x.pdf",
    "https://uploads.linear.app.evil.example/x.pdf",
    "https://evil.example/x.pdf?h=uploads.linear.app",
    "http://169.254.169.254/latest?x=uploads.linear.app",
    "http://uploads.linear.app/x.pdf",
    "https://uploads.linear.app:8443/x.pdf",
    "https://user:pw@uploads.linear.app/x.pdf",
    "https://up\nloads.linear.app/x.pdf",
    "https://uploads.linear.app/x\t.pdf",
    "uploads.linear.app/x.pdf",
    "",
    None,
    42,
])
def test_anything_else_is_refused(url) -> None:
    assert not is_linear_upload_url(url)


@pytest.mark.asyncio
async def test_data_source_refuses_to_send_the_token_elsewhere() -> None:
    from unittest.mock import MagicMock

    from app.sources.external.linear.linear import LinearDataSource

    source = LinearDataSource(MagicMock())
    with pytest.raises(ValueError):
        async for _ in source.download_file("https://evil.example/uploads.linear.app/x"):
            pass
    with pytest.raises(ValueError):
        await source.get_file_size("https://evil.example/uploads.linear.app/x")
    source._linear_client.get_client.assert_not_called()
