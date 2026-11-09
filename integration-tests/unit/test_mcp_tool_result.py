"""A tool result reads as JSON from its structured content, the way @pipeshub-ai/mcp 4.10's search answers.

That search writes its records as text for the agent and keeps the JSON in ``structuredContent``; the MCP tests read the
JSON, so they check the same data the agent is given.
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult

from helper.mcp_client import McpToolResult, tool_result

_RECORDS = {"totalRecords": 1, "records": [{"recordId": "r1", "recordName": "runbook", "passages": [{"text": "drill"}]}]}


def _server() -> FastMCP:
    server = FastMCP("results")

    @server.tool
    def search() -> ToolResult:
        return ToolResult(content="totalRecords: 1, best first\n\n1. runbook\n   recordId: r1", structured_content=_RECORDS)

    @server.tool
    def sources() -> ToolResult:
        # JSON as text and nothing structured: a dict return would be given structured content too.
        return ToolResult(content='{"sources": []}')

    return server


async def _call(name: str) -> McpToolResult:
    async with Client(_server()) as client:
        return tool_result(await client.call_tool_mcp(name, {}))


def test_a_result_written_as_text_reads_as_its_structured_content() -> None:
    result = asyncio.run(_call("search"))

    assert not result.is_error
    assert result.text.startswith("totalRecords: 1, best first")
    assert result.json() == _RECORDS


def test_a_result_with_json_text_reads_the_same() -> None:
    result = asyncio.run(_call("sources"))

    assert result.structured is None
    assert result.json() == {"sources": []}


def test_text_that_is_not_json_and_no_structured_content_says_what_came_back() -> None:
    result = McpToolResult(False, [{"type": "text", "text": "not json"}])

    with pytest.raises(AssertionError, match="not json"):
        result.json()
