"""Unit tests for the async GitHub data source (httpx-backed, connector-only).

The async data source is a drop-in for PyGithub-shaped consumers: attribute
access (nested, with ISO dates parsed to datetime), ``_rawData`` for
``listing_payload``, and ``completed=True`` so users.py guards trust the
fields.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from app.sources.client.github.github import GitHubClient, GitHubClientViaToken
from app.sources.external.github.github_async import GhObject, GitHubAsyncDataSource

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _client_wrapper() -> GitHubClient:
    inner = GitHubClientViaToken(token="dummy-token", per_page=90)
    inner.create_client()
    return GitHubClient(inner)


def _data_source(handler) -> GitHubAsyncDataSource:
    return GitHubAsyncDataSource(
        _client_wrapper(), transport=httpx.MockTransport(handler),
    )


class TestGhObject:
    def test_nested_attributes_and_date_parsing(self) -> None:
        commit = GhObject({
            "sha": "abc",
            "commit": {"author": {"name": "Ada", "date": "2024-01-05T00:00:00Z"}},
        })
        assert commit.sha == "abc"
        assert commit.commit.author.name == "Ada"
        assert commit.commit.author.date == datetime(2024, 1, 5, tzinfo=timezone.utc)

    def test_missing_attribute_supports_getattr_default(self) -> None:
        obj = GhObject({"path": "src"})
        assert getattr(obj, "size", None) is None

    def test_raw_data_for_listing_payload(self) -> None:
        obj = GhObject({"login": "octo"})
        assert obj._rawData == {"login": "octo"}
        assert obj.raw_data == {"login": "octo"}

    def test_decoded_content_from_base64(self) -> None:
        obj = GhObject({"content": "aGVsbG8=\n", "encoding": "base64"})
        assert obj.decoded_content == b"hello"


class TestAsyncEndpoints:
    async def test_get_git_tree_recursive(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["Authorization"]
            return httpx.Response(200, json={
                "sha": "head",
                "truncated": False,
                "tree": [{"path": "src/a.py", "type": "blob", "sha": "s1", "size": 10}],
            })

        ds = _data_source(handler)
        res = await ds.get_git_tree("acme", "widgets", "head", recursive=True)

        assert res.success is True
        assert "recursive=1" in seen["url"]
        assert seen["auth"] == "Bearer dummy-token"
        [entry] = res.data.tree
        assert (entry.path, entry.type, entry.sha, entry.size) == ("src/a.py", "blob", "s1", 10)
        assert res.data.truncated is False

    async def test_http_error_maps_status_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "API rate limit exceeded"})

        ds = _data_source(handler)
        res = await ds.compare_commits("acme", "widgets", "base", "head")

        assert res.success is False
        assert res.status_code == 403
        assert "rate limit" in (res.error or "")

    async def test_commits_first_and_last_jumps_to_link_last_page(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if "page=7" in str(request.url):
                return httpx.Response(200, json=[
                    {"sha": "old", "commit": {"author": {"date": "2023-01-01T00:00:00Z"}}},
                ])
            return httpx.Response(
                200,
                json=[{"sha": "new", "commit": {"author": {"date": "2024-01-05T00:00:00Z"}}}],
                headers={"link": '<https://api.github.com/x?per_page=1&page=7>; rel="last"'},
            )

        ds = _data_source(handler)
        res = await ds.list_commits_first_and_last("acme", "widgets", "src/a.py")

        assert res.success is True
        newest, oldest = res.data
        assert newest.sha == "new"
        assert oldest.sha == "old"
        assert len(calls) == 2
        assert "path=src%2Fa.py" in calls[0]

    async def test_single_commit_path_returns_same_commit_twice(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {"sha": "only", "commit": {"author": {"date": "2024-01-05T00:00:00Z"}}},
            ])

        ds = _data_source(handler)
        res = await ds.list_commits_first_and_last("acme", "widgets")

        newest, oldest = res.data
        assert newest.sha == oldest.sha == "only"

    async def test_graphql_returns_raw_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["query"].startswith("query")
            return httpx.Response(200, json={"data": {"repository": {"id": "R_1"}}})

        ds = _data_source(handler)
        res = await ds.graphql_query("query { viewer { login } }")

        assert res.success is True
        assert res.data == {"repository": {"id": "R_1"}}  # plain dict, not wrapped


class TestListing:
    async def test_listing_without_page_args_walks_every_page(self) -> None:
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page"))
            pages.append(page)
            if page == 1:
                return httpx.Response(200, json=[{"id": i} for i in range(100)])
            return httpx.Response(200, json=[{"id": 100}])

        ds = _data_source(handler)
        res = await ds.list_org_members("acme")

        assert res.success is True
        assert len(res.data) == 101
        assert pages == [1, 2]

    async def test_listing_with_page_args_requests_one_page(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["per_page"] = request.url.params.get("per_page")
            seen["page"] = request.url.params.get("page")
            return httpx.Response(200, json=[{"id": 1}])

        ds = _data_source(handler)
        res = await ds.list_pulls(
            "acme", "widgets", state="all", sort="updated", direction="desc",
            per_page=100, page=3,
        )

        assert res.success is True
        assert (seen["per_page"], seen["page"]) == ("100", "3")
        assert res.data[0].id == 1

    async def test_search_repositories_unwraps_items(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("q") == "widgets in:name"
            return httpx.Response(200, json={"total_count": 1, "items": [{"full_name": "acme/widgets"}]})

        ds = _data_source(handler)
        res = await ds.search_repositories("widgets in:name", per_page=10)

        assert res.data[0].full_name == "acme/widgets"

    async def test_aclose_closes_cached_http_client(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"login": "octo"})

        ds = _data_source(handler)
        assert await ds.get_authenticated()
        client = ds._rest._client
        assert client is not None
        await ds.aclose()
        assert client.is_closed
        assert ds._rest._client is None
