"""What the mcp 2.x SDK hands us, read the same way as 1.x shapes: snake_case result and tool
fields, httpx2 errors, and the HTTP status the client restores as `MCPHttpStatusError`."""
from __future__ import annotations

from types import SimpleNamespace

import httpx2
import pytest

from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_result import mcp_result_to_tool_output
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.discovery import tool_infos_from_listing
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPHttpStatusError,
    is_http_unauthorized,
)
from app.agents.mcp.failure import MCPFailureReason, classify_mcp_failure
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerConfig,
    MCPToolInfo,
    MCPTransport,
)

_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def _status_error(status: int, url: str = "https://mcp.example.com/mcp?api_key=secret") -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", url)
    return httpx2.HTTPStatusError("boom", request=request, response=httpx2.Response(status, request=request))


class TestTheRestoredHttpStatus:
    def test_is_a_connection_error_with_its_status(self) -> None:
        error = MCPHttpStatusError(503, "HTTP 503 from https://mcp.example.com/mcp")
        assert isinstance(error, MCPConnectionError)
        assert error.status_code == 503
        assert str(error) == "HTTP 503 from https://mcp.example.com/mcp"

    @pytest.mark.parametrize("error,unauthorized", [
        (MCPHttpStatusError(401), True),
        (MCPHttpStatusError(403), False),
        (_status_error(401), True),
        (_status_error(500), False),
    ])
    def test_a_401_is_recognised_whichever_library_raised_it(self, error: Exception, unauthorized: bool) -> None:
        assert is_http_unauthorized(error) is unauthorized

    def test_a_401_behind_another_error_is_found(self) -> None:
        outer = MCPConnectionError("connect failed")
        outer.__cause__ = MCPHttpStatusError(401)
        assert is_http_unauthorized(outer)


class TestFailureReasons:
    @pytest.mark.parametrize("error,reason", [
        (MCPHttpStatusError(401), MCPFailureReason.UNAUTHORIZED),
        (MCPHttpStatusError(503), MCPFailureReason.UNREACHABLE),
        (httpx2.ConnectError("refused"), MCPFailureReason.UNREACHABLE),
        (httpx2.ReadTimeout("slow"), MCPFailureReason.TIMEOUT),
        (_status_error(502), MCPFailureReason.UNREACHABLE),
    ])
    def test_httpx2_and_restored_statuses_classify_like_httpx_ones(self, error: Exception, reason: MCPFailureReason) -> None:
        assert classify_mcp_failure(error) == reason


def _adapter() -> MCPToolAdapter:
    server = ResolvedMCPServer(
        instance_id="inst-1", name="JiraMCP", display_name="Jira MCP",
        instance={"authMode": "none"}, auth={}, owner_id="user-1", attached_tools=None,
    )
    tool = MCPToolInfo(name="search", namespaced_name="mcp_jira_mcp_search", input_schema={"type": "object"})
    return MCPToolAdapter(server, tool, session_manager=None)  # type: ignore[arg-type]


class TestWhatTheModelIsTold:
    def test_a_restored_status_names_the_status(self) -> None:
        assert _adapter()._error_message(MCPHttpStatusError(503)) == "The Jira MCP MCP server returned HTTP 503."

    def test_a_tool_the_server_dropped_says_so(self) -> None:
        from app.agents.mcp.errors import MCPToolNotOfferedError

        error = MCPToolNotOfferedError("The Jira MCP MCP server no longer offers the tool create_issue.")
        assert _adapter()._error_message(error) == str(error)

    def test_a_listing_that_timed_out_isnt_reported_as_the_call_timing_out(self) -> None:
        from app.agents.mcp.client import MCPListingTimeoutError

        message = _adapter()._error_message(MCPListingTimeoutError("slow"))
        assert message == "The Jira MCP MCP server didn't list its tools within 15 seconds."

    def test_an_httpx2_status_error_names_the_status_but_not_the_url(self) -> None:
        message = _adapter()._error_message(_status_error(500))
        assert message == "The Jira MCP MCP server returned HTTP 500."
        assert "secret" not in message


class TestResultsInTheNewShape:
    async def test_snake_case_images_and_resources_are_read(self) -> None:
        result = SimpleNamespace(
            content=[
                SimpleNamespace(type="image", data=_PNG, mime_type="image/png"),
                SimpleNamespace(type="resource", resource=SimpleNamespace(uri="file:///x.bin", mime_type="application/zip", text=None, blob="AAAA")),
                SimpleNamespace(type="audio", data="AAAA", mime_type="audio/wav"),
            ],
            structured_content=None,
            is_error=False,
            meta=None,
        )

        output = await mcp_result_to_tool_output(result, None)

        assert output.success is True
        text = str(output.data)
        assert "binary resource file:///x.bin (application/zip)" in text
        assert "audio returned by the tool (audio/wav)" in text

    async def test_snake_case_error_and_structured_content_are_read(self) -> None:
        result = SimpleNamespace(content=[], structured_content={"count": 2}, is_error=True, meta=None)
        output = await mcp_result_to_tool_output(result, None)
        assert output.success is False


class TestToolsInTheNewShape:
    def test_a_2x_tool_gives_its_input_schema(self) -> None:
        config = MCPServerConfig(
            id="inst-1", org_id="org-1", created_by="u1", name="Jira", type_id="jira",
            transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.NONE, created_at=0, updated_at=0,
        )
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tools = tool_infos_from_listing(
            [SimpleNamespace(name="search", description="Search", input_schema=schema)], config,
        )

        assert tools[0].input_schema == schema
        assert tools[0].namespaced_name.endswith("_search")


def _jira() -> MCPServerConfig:
    return MCPServerConfig(
        id="inst-1", org_id="org-1", created_by="u1", name="Jira", type_id="jira",
        transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.NONE, created_at=0, updated_at=0,
    )


class TestToolAnnotations:
    def test_a_2x_tools_hints_are_kept_with_protocol_names(self) -> None:
        from mcp.types import Tool, ToolAnnotations

        tool = Tool(
            name="delete_issue", input_schema={"type": "object"},
            annotations=ToolAnnotations(title="Delete issue", read_only_hint=False, destructive_hint=True),
        )
        (info,) = tool_infos_from_listing([tool], _jira())

        assert info.annotations == {"title": "Delete issue", "readOnlyHint": False, "destructiveHint": True}
        assert info.model_dump(by_alias=True)["annotations"]["readOnlyHint"] is False

    def test_a_dict_tools_hints_are_kept(self) -> None:
        (info,) = tool_infos_from_listing(
            [{"name": "search", "inputSchema": {}, "annotations": {"readOnlyHint": True}}], _jira(),
        )
        assert info.annotations == {"readOnlyHint": True}

    @pytest.mark.parametrize("annotations", [None, {}, "not-a-dict"])
    def test_no_hints_is_none(self, annotations: object) -> None:
        (info,) = tool_infos_from_listing([SimpleNamespace(name="search", input_schema={}, annotations=annotations)], _jira())
        assert info.annotations is None
