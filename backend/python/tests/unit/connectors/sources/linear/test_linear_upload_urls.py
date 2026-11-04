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


@pytest.mark.asyncio
@pytest.mark.parametrize("target, followed", [
    ("https://evil.example/x.pdf", False),
    ("https://uploads.linear.app/org/moved/x.pdf", True),
])
async def test_a_redirect_is_followed_only_to_the_upload_host(monkeypatch, target, followed) -> None:
    from unittest.mock import MagicMock

    import httpx

    from app.sources.external.linear import linear as module

    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path == "/org/a/x.pdf":
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, content=b"pdf")

    client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda **options: client(transport=httpx.MockTransport(answer), **options))
    source = module.LinearDataSource(MagicMock())
    source._linear_client.get_client.return_value.get_auth_header.return_value = "lin_api_test"

    if followed:
        assert b"".join([c async for c in source.download_file("https://uploads.linear.app/org/a/x.pdf")]) == b"pdf"
        assert asked == ["https://uploads.linear.app/org/a/x.pdf", target]
    else:
        with pytest.raises(ValueError):
            async for _ in source.download_file("https://uploads.linear.app/org/a/x.pdf"):
                pass
        assert asked == ["https://uploads.linear.app/org/a/x.pdf"], "the redirect to another host was followed"
