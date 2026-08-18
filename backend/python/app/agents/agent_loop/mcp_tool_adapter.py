"""`MCPToolAdapter` — wraps a single MCP-discovered tool as an agent-loop `Tool`.

Parallel to `PipesHubStructuredToolAdapter` (`tool_adapter.py`): identity/description/
parameters come straight from the `MCPToolInfo` `discovery.py` (Phase 1) already produces
— either live-discovered or the graph-node fallback `MCPToolProvider` builds when live
discovery fails (see `mcp_tool_loader.py`). Execution goes through `MCPSessionManager` for
per-request connection reuse + on-demand OAuth refresh, instead of
`MCPClientManager.connect()`'s per-call connect/disconnect.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agents.agent_loop.tool_adapter import (
    _params_from_schema,
    _PermissiveValidationMixin,
    _to_tool_output,
)
from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.mcp.token_refresh import MCPTokenRefreshError

if TYPE_CHECKING:
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from app.agents.agent_loop.mcp_session import MCPSessionManager
    from app.agents.mcp.models import MCPToolInfo

logger = logging.getLogger(__name__)

__all__ = ["MCPToolAdapter"]


def _mcp_result_to_tuple(result: Any) -> tuple[bool, Any]:  # noqa: ANN401
    """Normalizes fastmcp's `CallToolResult` (`content`/`data`/`is_error`) into the
    `(success, data)` tuple format `_to_tool_output` (`tool_adapter.py`) already knows how
    to read — it unwraps a 2-tuple back to its bare `data`/error payload before returning
    `ToolOutput`, whereas a `{"success": ..., "data": ...}` dict would come back out with
    that wrapper still attached, since `clean_tool_result` only strips `REMOVE_FIELDS` keys,
    it never unwraps a nested `"data"`. `ToolResultExtractor.extract_success_status` was
    written against dict/str/tuple results, not an arbitrary SDK object — falling through to
    its generic `str(result)` substring scan for `CallToolResult` would be unreliable, so
    this reads `is_error` directly instead."""
    is_error = bool(getattr(result, "is_error", False))
    data = getattr(result, "data", None)
    if data is None:
        content = getattr(result, "content", None) or []
        text_parts = [text for block in content if (text := getattr(block, "text", None))]
        data = "\n".join(text_parts) if text_parts else (content or None)
    if is_error:
        return False, data if isinstance(data, str) else str(data)
    return True, data


class MCPToolAdapter(_PermissiveValidationMixin, Tool):
    """Wraps one `MCPToolInfo` — discovered from `server` — as an agent-loop `Tool`."""

    def __init__(
        self,
        server: "ResolvedMCPServer",
        tool_info: "MCPToolInfo",
        session_manager: "MCPSessionManager",
    ) -> None:
        self._server = server
        self._tool_info = tool_info
        self._session_manager = session_manager

    @property
    def name(self) -> str:
        return self._tool_info.namespaced_name

    @property
    def short_description(self) -> str:
        return self._tool_info.description or self._tool_info.name

    @property
    def description(self) -> str:
        return self._tool_info.description or f"{self._server.display_name}: {self._tool_info.name}"

    @property
    def path(self) -> str:
        return f"/mcp/{self._server.instance_id}/{self._tool_info.name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return _params_from_schema(self._tool_info.input_schema)

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        try:
            raw_result = await self._session_manager.call(self._server, self._tool_info.name, kwargs)
        except (MCPConnectionError, MCPTokenRefreshError, MCPOAuthError) as exc:
            return ToolOutput(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("MCP tool %s raised unexpectedly", self.name)
            return ToolOutput(success=False, error=str(exc))
        return _to_tool_output(_mcp_result_to_tuple(raw_result))
