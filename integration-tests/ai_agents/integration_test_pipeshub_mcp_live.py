"""PipesHub's own MCP endpoint used the way an MCP host uses it: with a PAT.

CTO list: MCPs. ``response-validation/mcp`` pins the served tool surface; this
calls a search tool and checks it finds a document this run indexed.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pipeshub_client import PipeshubClient

from helper.mcp_client import mcp_url

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.mcp]

SEARCH_TOOL = "pipeshub_search"


async def _tools_and_search(url: str, token: str, query: str) -> tuple[list[str], str, bool]:
    async with Client(StreamableHttpTransport(url, auth=token)) as client:
        tools = [tool.name for tool in await client.list_tools()]
        result = await client.call_tool_mcp(SEARCH_TOOL, {"query": query})
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        return tools, text + json.dumps(result.structuredContent or {}), bool(result.isError)


@pytest.fixture(scope="module")
def search_over_mcp(pipeshub_client: PipeshubClient, personal_access_token: str, it_document: dict[str, str]):
    return asyncio.run(_tools_and_search(
        mcp_url(pipeshub_client.base_url), personal_access_token, it_document["needle"],
    ))


class TestPipesHubMcpWithAPat:
    def test_the_tool_list_has_search(self, search_over_mcp) -> None:
        tools, _text, _is_error = search_over_mcp
        assert SEARCH_TOOL in tools, tools

    def test_search_finds_the_indexed_document(self, search_over_mcp, it_document: dict[str, str]) -> None:
        _tools, text, is_error = search_over_mcp
        assert not is_error, f"{SEARCH_TOOL} failed: {text[:2000]}"
        assert it_document["name"] in text or it_document["record_id"] in text, text[:3000]
