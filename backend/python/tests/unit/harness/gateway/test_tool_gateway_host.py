"""TG-1 (E0): ``ToolGatewayHostContract`` on ``HttpToolGatewayHost`` over real HTTP with the MCP SDK client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.harness.gateway.host import HttpToolGatewayHost
from harness_core.testing.contracts.engine_controller import ToolGatewayHostContract
from tests.unit.harness.gateway.support import GatewayServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pydantic import JsonValue

    from harness_core.gateway.types import GatewayBinding


class _OverHttp:
    def __init__(self, server: GatewayServer) -> None:
        self._server = server
        self.host = HttpToolGatewayHost(server.gateway, base_url=server.base)

    async def call(
        self, upstream: str, binding: GatewayBinding, name: str, arguments: Mapping[str, JsonValue]
    ) -> str | None:
        headers = {"Authorization": f"Bearer {self._server.mint.mint(binding)}"}
        async with httpx.AsyncClient(headers=headers, timeout=30) as http:
            probe = await http.post(f"{upstream}/mcp", json={})
            if probe.status_code == 404:
                return None
            async with (
                streamable_http_client(f"{upstream}/mcp", http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(name, dict(arguments))
        return "".join(c.text for c in result.content if c.type == "text")


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


class TestHttpToolGatewayHost(ToolGatewayHostContract):
    @pytest.fixture
    def host_env(self, server: GatewayServer) -> _OverHttp:
        return _OverHttp(server)
