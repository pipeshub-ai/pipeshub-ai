"""Notion: the NOTION_API_BASE_URL override, the toolset client builder, and the toolset's data-source calls."""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.notion.notion import (
    DEFAULT_NOTION_API_BASE_URL,
    NotionClient,
    NotionRESTClientViaOAuth,
    NotionRESTClientViaToken,
    notion_api_base_url,
)

LOG = logging.getLogger("test")


class TestBaseUrl:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTION_API_BASE_URL", raising=False)
        assert notion_api_base_url() == DEFAULT_NOTION_API_BASE_URL == "https://api.notion.com/v1"
        assert NotionRESTClientViaToken("t").get_base_url() == "https://api.notion.com/v1"

    def test_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_API_BASE_URL", "http://notion.test:8103/v1/")
        assert NotionRESTClientViaToken("t").get_base_url() == "http://notion.test:8103/v1"
        oauth = NotionRESTClientViaOAuth("id", "secret", "https://app/cb", access_token="a")
        assert oauth.get_base_url() == "http://notion.test:8103/v1"
        assert oauth.get_authorization_url().startswith("http://notion.test:8103/v1/oauth/authorize?")

    def test_empty_value_means_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_API_BASE_URL", "")
        assert notion_api_base_url() == "https://api.notion.com/v1"


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_oauth(self) -> None:
        client = await NotionClient.build_from_toolset(
            {"authType": "OAUTH", "isAuthenticated": True, "credentials": {"access_token": "tok"}}, LOG)
        http = client.get_client()
        assert isinstance(http, NotionRESTClientViaToken)
        assert http.headers["Authorization"] == "Bearer tok"
        assert http.headers["Notion-Version"] == "2025-09-03"

    @pytest.mark.asyncio
    async def test_api_token(self) -> None:
        client = await NotionClient.build_from_toolset(
            {"authType": "API_TOKEN", "isAuthenticated": True, "auth": {"apiToken": " ntn_x "}}, LOG)
        assert client.get_client().headers["Authorization"] == "Bearer ntn_x"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("config", "message"), [
        ({}, "required"),
        ({"authType": "OAUTH", "isAuthenticated": False}, "not authenticated"),
        ({"authType": "OAUTH", "isAuthenticated": True, "credentials": {}}, "Access token"),
        ({"authType": "API_TOKEN", "isAuthenticated": True, "auth": {}}, "API token"),
        ({"authType": "BASIC_AUTH", "isAuthenticated": True}, "Unsupported"),
    ])
    async def test_invalid(self, config: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            await NotionClient.build_from_toolset(config, LOG)

    @pytest.mark.asyncio
    async def test_factory_uses_it(self) -> None:
        from app.agents.tools.factories.notion import NotionClientFactory

        client = await NotionClientFactory().create_client(
            None, LOG, {"authType": "API_TOKEN", "isAuthenticated": True, "auth": {"apiToken": "x"}})
        assert isinstance(client, NotionClient)


def _response(success: bool = True, body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.success = success
    resp.data = MagicMock()
    resp.data.json.return_value = body or {}
    return resp


@pytest.fixture
def notion_tool() -> Iterator[tuple[Any, MagicMock]]:
    from app.agents.actions.notion import notion as actions

    with patch.object(actions, "NotionDataSource") as ds_cls:
        yield actions.Notion(MagicMock()), ds_cls.return_value


class TestToolsetDataSources:
    def test_query_uses_first_data_source(self, notion_tool: tuple[Any, MagicMock]) -> None:
        tool, ds = notion_tool
        ds.retrieve_database = AsyncMock(
            return_value=_response(body={"data_sources": [{"id": "ds-1"}, {"id": "ds-2"}]}))
        ds.query_data_source_by_id = AsyncMock(return_value=_response(body={"object": "list", "results": []}))
        ok, _ = asyncio.run(tool.query_database("db-1", filter={"and": []}, page_size=5))
        assert ok
        ds.query_data_source_by_id.assert_awaited_once_with(
            data_source_id="ds-1", request_body={"filter": {"and": []}, "page_size": 5})

    def test_query_takes_a_data_source_id(self, notion_tool: tuple[Any, MagicMock]) -> None:
        tool, ds = notion_tool
        ds.retrieve_database = AsyncMock(return_value=_response(success=False))
        ds.query_data_source_by_id = AsyncMock(return_value=_response(body={"object": "list", "results": []}))
        asyncio.run(tool.query_database("ds-9"))
        assert ds.query_data_source_by_id.await_args.kwargs["data_source_id"] == "ds-9"

    def test_create_puts_schema_on_initial_data_source(self, notion_tool: tuple[Any, MagicMock]) -> None:
        tool, ds = notion_tool
        ds.create_database = AsyncMock(return_value=_response(body={"object": "database", "id": "db"}))
        props = {"Name": {"title": {}}}
        ok, _ = asyncio.run(tool.create_database("page-1", "Tasks", props))
        assert ok
        body = ds.create_database.await_args.kwargs["request_body"]
        assert body["initial_data_source"] == {"properties": props}
        assert "properties" not in body
        assert body["parent"] == {"page_id": "page-1"}
