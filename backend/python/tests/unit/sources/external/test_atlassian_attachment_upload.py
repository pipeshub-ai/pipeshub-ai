"""Request shape of the hand-maintained Jira and Confluence attachment upload methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.confluence.confluence import ConfluenceDataSource
from app.sources.external.jira.jira import JiraDataSource


def _http(base_url: str) -> MagicMock:
    http = MagicMock()
    http.get_base_url.return_value = base_url
    http.execute = AsyncMock(return_value=MagicMock(status=200))
    return http


def _jira(base_url: str = "https://api.atlassian.com/ex/jira/cloud-1") -> JiraDataSource:
    client = MagicMock()
    client.get_client.return_value = _http(base_url)
    return JiraDataSource(client)


def _confluence(base_url: str) -> ConfluenceDataSource:
    client = MagicMock()
    client.get_client.return_value = _http(base_url)
    return ConfluenceDataSource(client)


class TestJiraUploadIssueAttachment:
    @pytest.mark.asyncio
    async def test_cloud_request_shape(self) -> None:
        source = _jira()
        await source.upload_issue_attachment("KAN-1", "bug.png", b"PNG", "image/png")

        req = source._client.execute.await_args.args[0]
        assert req.method == "POST"
        assert req.url == "https://api.atlassian.com/ex/jira/cloud-1/rest/api/3/issue/KAN-1/attachments"
        assert req.headers["X-Atlassian-Token"] == "no-check"
        assert "Content-Type" not in req.headers
        assert req.files == [("file", ("bug.png", b"PNG", "image/png"))]
        assert req.body is None
        assert "timeout" in source._client.execute.await_args.kwargs

    @pytest.mark.asyncio
    async def test_data_center_uses_v2(self) -> None:
        source = _jira("https://jira.corp.example")
        await source.upload_issue_attachment("OPS-7", "a.pdf", b"%PDF", "application/pdf", api_version="2")

        req = source._client.execute.await_args.args[0]
        assert req.url == "https://jira.corp.example/rest/api/2/issue/OPS-7/attachments"

    @pytest.mark.asyncio
    async def test_issue_key_is_path_escaped(self) -> None:
        source = _jira("https://jira.test")
        await source.upload_issue_attachment("../../admin", "a.txt", b"x", "text/plain")

        req = source._client.execute.await_args.args[0]
        assert req.url == "https://jira.test/rest/api/3/issue/..%2F..%2Fadmin/attachments"

    @pytest.mark.asyncio
    async def test_rejects_unknown_api_version(self) -> None:
        source = _jira()
        with pytest.raises(ValueError):
            await source.upload_issue_attachment("KAN-1", "a.txt", b"x", "text/plain", api_version="latest")
        source._client.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attachment_settings_path(self) -> None:
        source = _jira("https://jira.test")
        await source.get_attachment_settings(api_version="2")

        req = source._client.execute.await_args.args[0]
        assert req.method == "GET"
        assert req.url == "https://jira.test/rest/api/2/attachment/meta"


class TestConfluenceUploadContentAttachment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url, expected",
        [
            (
                "https://api.atlassian.com/ex/confluence/c1/wiki/api/v2",
                "https://api.atlassian.com/ex/confluence/c1/wiki/rest/api/content/123/child/attachment",
            ),
            ("https://wiki.corp.example", "https://wiki.corp.example/rest/api/content/123/child/attachment"),
            (
                "https://corp.example/confluence",
                "https://corp.example/confluence/rest/api/content/123/child/attachment",
            ),
        ],
    )
    async def test_uses_v1_endpoint(self, base_url: str, expected: str) -> None:
        source = _confluence(base_url)
        await source.upload_content_attachment("123", "diagram.png", b"PNG", "image/png")

        req = source._client.execute.await_args.args[0]
        assert req.method == "POST"
        assert req.url == expected

    @pytest.mark.asyncio
    async def test_request_shape(self) -> None:
        source = _confluence("https://acme.atlassian.net/wiki/api/v2")
        await source.upload_content_attachment("123", "diagram.png", b"PNG", "image/png", comment="from chat")

        req = source._client.execute.await_args.args[0]
        assert req.headers["X-Atlassian-Token"] == "nocheck"
        assert req.body == {"minorEdit": "true", "comment": "from chat"}
        assert req.files == [("file", ("diagram.png", b"PNG", "image/png"))]

    @pytest.mark.asyncio
    async def test_content_id_is_path_escaped(self) -> None:
        source = _confluence("https://wiki.test")
        await source.upload_content_attachment("1/../2", "a.txt", b"x", "text/plain")

        req = source._client.execute.await_args.args[0]
        assert req.url == "https://wiki.test/rest/api/content/1%2F..%2F2/child/attachment"
